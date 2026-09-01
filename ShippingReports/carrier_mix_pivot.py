"""Carrier Mix pivot — ship weeks as COLUMNS, carrier-service lanes as ROWS.

🔴 CONSTRAINTS SSOT = ``ShippingReports/RESHIP_REPORT_RULES.md`` **D35**. Read it before
changing anything here. This module is the implementation; the doc is the authority.

WHY IT EXISTS (negatives first)
-------------------------------
Kurt watches OnTrac share, because OnTrac is the cheapest lane we own and share erosion is
invisible in a weekly total. Two ways of drawing this table destroy the signal outright:

* **FedEx 2Day folded into FedEx Ground** hides the air spend inside a ground row. The table
  still sums to the cohort, so it *looks* right. Air is the expensive escape hatch and it gets
  its own row, always, tested FIRST and POSITIVELY.
* **OnTrac and LaserShip counted as two carriers** halves the share we are trying to watch.
  ``canon.normalize_carrier`` already folds the alias; this module never re-implements it.

DB READ-ONLY BY CONSTRUCTION. ``connect_reporting`` (mode=ro on BOTH stores) only — Claude never
write-connects ``shipping.db`` (MSIX/WAL corruption, 6/27 + 7/01). The ONLY write anywhere is the
Sheets API call behind ``--write-sheet`` (Kurt-authorized 2026-08-26), which repaints the ``Carrier Mix`` tab of the
Running Reship sheet as a VIEW of the ledger — see D35c. It never touches any other tab.

Run::

    python carrier_mix_pivot.py                 # last 5 ship weeks, render + update ledger
    python carrier_mix_pivot.py --weeks 8
    python carrier_mix_pivot.py --verify-gate   # the structural reproduce-gate (D35d)
    python carrier_mix_pivot.py --self-test     # exercise the branches a normal week never hits
    python carrier_mix_pivot.py --no-ledger     # render only, touch nothing
    python carrier_mix_pivot.py --write-sheet   # …and repaint the `Carrier Mix` sheet tab (D35c)

🔴 THE GATE IS KEYED TO LOGIC, NEVER TO A VOLUME (D35d). It used to pin five literals to the
CURRENT week's counts, so the Tuesday Dallas leg — which lands EVERY week — broke it and the
pivot refused to render for two days over a cohort that had done nothing but grow 2500 → 2545.
Ship weeks are multi-leg and are not final until Tuesday night. Cohort growth is REPORTED, never
fatal; what refuses is a classifier regression, a lane that stops partitioning the cohort, air
sitting in the ground row, or the MATURED (closed, cannot-grow) anchor cohort moving.

🔴 `--verify-gate` is a HAPPY PATH and is NOT evidence on its own: it resolves before the
Pending/denominator branch is ever reached. Run `--self-test` too, or a change lands green
against code that never executed.

Data source (DO_READ_CONTRACT §1, C4)::

    REPORTING_CLOUD_DB=1   delivery_status <- DO cloud mirror; everything else LOCAL
    (unset / =0)           every table LOCAL, byte-identical to the pre-migration path

🔴 ``shipments`` — the COST and invoice basis for every row in this pivot — stays LOCAL and is
NOT negotiable yet: cloud ``shipments`` holds two ``ship_date`` formats (so ``MAX``/``BETWEEN``
are wrong) and 25,795 duplicate rows worth $21,319 of double-counted cost. Blocked on B2/B2b.
Measured 2026-08-27: flipping ``delivery_status`` moves NOTHING in this report, because
``delivery_status.service`` is populated on **0 of 121,185 cloud rows** (and 0 of 118,909
local) — the service basis is the routing TAG, not the carrier feed. That makes this the safe
first consumer to flip; it does not make it the one that benefits.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 🔴 Both inserts are derived from THIS FILE's own location (`__file__`), never from the cwd, so
# the module imports identically however it is invoked. That matters because the scheduled owner
# runs it unattended, where the cwd is whatever the task scheduler hands it — verified running
# from `C:\` and `C:\Windows`. `ShipRouting` is inserted LAST so it wins position 0 for the very
# generic package name `lib`; AppyHour has no competing `lib` package (checked).
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))                       # AppyHour/    → appyhour_lib
sys.path.insert(0, str(_HERE.parents[2] / "ShipRouting"))       # ShipRouting/ → lib.canon

# `lib` resolves at RUNTIME via the insert above; a static checker cannot see a sys.path mutation,
# so pyright's reportMissingImports here is a false positive, not a broken import.
from appyhour_lib.cloud_reads import connect_reporting  # noqa: E402  isort:skip
from appyhour_lib.credentials import get_google_credentials  # noqa: E402  isort:skip
from lib import canon  # type: ignore[reportMissingImports]  # noqa: E402  isort:skip

# ── Row model ────────────────────────────────────────────────────────────────
# 🔴 EXACTLY these lanes, in this order (Kurt 2026-08-25). FedEx Ground and FedEx Home
# Delivery are ONE row — same economics, and the `!ANY FedEx - <Hub>_AHB!` fence deliberately
# leaves RMFG to pick between them, so the merge is what makes that fence unambiguous.
# FedEx 2Day is NEVER merged into it and never into UPS/OnTrac either.
ONTRAC = "OnTrac Ground"
FEDEX_GND = "FedEx Ground-HD"
FEDEX_AIR = "FedEx 2Day Air"
UPS_GND = "UPS Ground"
OTHER = "Other / Unmapped"
PENDING = "Unresolved / Pending"
TOTAL = "Total"
LANES = (ONTRAC, FEDEX_GND, FEDEX_AIR, UPS_GND, OTHER)
ROWS = LANES + (PENDING, TOTAL)

AIR_LEVELS = (canon.TWO_DAY, canon.OVERNIGHT)
GROUND_LEVELS = (canon.GROUND, canon.HOME_DELIVERY)

# 🔴 Threshold is MEASURED, not chosen for roundness. Invoice coverage per cohort asymptotes at
# 98-100% and never reaches 100 (`_SHIP_2026-07-06` sits at 98% at age 51d — a residue of boxes
# is cancelled/undeliverable and is never billed). A 100% gate would therefore never fire and
# every cost cell would stay provisional forever.
COST_COMPLETE_COVERAGE = 0.98

# Same 98%, same reasoning, applied to the `shopify_orders` replica used for the Pending row.
# Measured 2026-08-25 across the five live cohorts the replica sits at 99.9-100% of the label
# count on four of them and at 40% on `_SHIP_2026-08-24`, so the floor separates "a few orders
# cancelled after their label was cut" from "the pull chain is broken" without a judgement call.
REPLICA_MIN_COMPLETENESS = 0.98

# Backstop for the COUNT freeze. Reusing PivotAnalytics' `PA_MATURITY_DAYS` (D15) rather than
# inventing a constant: it is the already-decided age at which this sheet stops owning a column.
# 🔴 Without a backstop a single order that is never labelled (cancelled at RMFG, stuck on hold)
# holds a column provisional forever, so a mutable routing tag keeps being re-read months later
# — the D23 degradation this design exists to prevent. `_SHIP_2026-07-27` sat at pending=1 at
# age 30d. On force-freeze the residual is RECORDED, never swallowed.
COUNT_FREEZE_MAX_AGE_DAYS = 10

OUT_DIR = _HERE.parents[2] / "_outputs" / "reports"
LEDGER = OUT_DIR / "carrier_mix_ledger.json"
REPORT = OUT_DIR / "carrier-mix-pivot.md"

# ── Sheet view (D35c) ────────────────────────────────────────────────────────
# The Running Reship PIVOT sheet. The tab is a VIEW of the ledger, repainted whole each run;
# the ledger above is the memory. NOT an Apps Script tab (D35 "Why it is not a .gs tab").
SHEET_ID = "1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU"
SHEET_TAB = "Carrier Mix"
# SA credentials resolve via appyhour_lib.credentials.get_google_credentials():
# GOOGLE_SVC_ACCOUNT_JSON_CONTENT (inline JSON, App Platform) else the gitignored
# key file. No path literal here — see that module for the resolution order.
# A1 marker: ownership test for the repaint gate AND the tab's visible title. 🔴 Written in the
# MAIN batch; the "Last refreshed" stamp row is written LAST in a separate call, so a missing
# stamp row = an incomplete paint (crash between clear and finish), loudly visible.
SHEET_TITLE = "Carrier Mix — ship weeks as columns (D35)"

# The `fulfillments` ingest (sync_logon.py) is expected to touch rows at least this often;
# mirrors `_outputs/scripts/freshness_sweep.py`'s own 3-day rule for the same table.
STALE_AFTER_DAYS = 3

# ── D41: basis, as_of, and the settle clock ──────────────────────────────────
# 🔴 THIS PIVOT'S COUNT BASIS IS `raw` — every `fulfillments` row carrying the tag, with NO
# reship exclusion and NO date predicate. Measured 2026-09-01: the four older columns equal the
# full raw tag count exactly (2225 / 2362 / 2365 / 2366), which is what rules out the
# once-suspected weekday filter. Named in the tab so it is never inferred: the sibling `TnT2`
# tab is `reship_excluded` and over Shopify ORDERS, so the two tabs' totals for one week are
# SUPPOSED to differ and a reader comparing them needs both bases on screen.
COUNTS_BASIS = "raw"

# 🔴 Kurt's rule: a ship week is fully fulfilled by WEDNESDAY morning. MEASURED over the seven
# cohorts 07-13…08-24 on `fulfilled_at` (2026-09-01): every cohort is a Monday bulk plus a
# Tuesday tail and NOT ONE records a single Wednesday fulfilment.
#
#   cohort      Monday   Tuesday(+1)   later
#   07-13         1987          39
#   07-20         2025          48       +7 on 07-27
#   07-27         2135          90
#   08-03         2235         119       +1 on 08-17  (+7 pre-Monday)
#   08-10         2252         108       +3 on 08-17  (+2 pre-Monday)
#   08-17         2282          84
#   08-24         2500          45
#
# 🔴 SETTLED IS NOT FROZEN. 3 of 7 kept gaining afterwards, always on a LATER MONDAY — boxes
# re-tagged into an old cohort. The gate says "safe to publish", never "final"; `as_of` is what
# makes a published number honest after it stops being true.
COHORT_SETTLES_DAY_OFFSET = 2      # Wednesday = ship Monday + 2

# ── Reproduce-gate anchors ───────────────────────────────────────────────────
# 🔴 THE GATE IS KEYED TO LOGIC, NEVER TO A VOLUME. See D35d.
#
# The first version of this gate pinned five literals to `_SHIP_2026-08-24` as measured on
# 2026-08-25 (OnTrac 1763 / FedEx Ground-HD 648 / FedEx 2Day 69 / UPS 20 / Total 2500) and
# refused to render anything when they moved. They moved on 2026-08-25 and the pivot could not
# render for two days. Nothing was wrong: `fulfilled_at` on that cohort is 2,500 rows dated
# 08-24 and 45 rows dated 08-25 — the Tuesday Dallas leg, which lands EVERY week. The reference
# was a Monday-only snapshot of a cohort that is not final until Tuesday night, so it was
# guaranteed to break weekly; restating the literals would only have reset that clock.
#
# What a reproduce-gate is FOR is catching a LOGIC regression — 2Day folded into Ground-HD, a
# bucket silently dropped, LaserShip counted apart from OnTrac. None of those care whether the
# cohort is 2,500 or 2,545. So the anchors below are (a) a frozen classifier truth table with no
# data in it at all and (b) a MATURED cohort whose legs are all in and which therefore cannot
# grow. Cohort growth is REPORTED by `reconcile_ledger`, never fatal.

# (a) Frozen classifier truth table — zero DB, zero volume. This is the real anchor: it
# reproduces the DECISION, which is the only thing the old literals were ever evidence for.
# 🔴 The first two rows are the whole point of D35 failure #1 — if a change ever folds FedEx
# 2Day into Ground-HD, these fail before a single row is read.
CLASSIFIER_GOLDEN = (
    ("FedEx 2Day by tag",      "FedEx",  {"tag": canon.TWO_DAY},                  FEDEX_AIR),
    ("FedEx 2Day by invoice",  "FedEx",  {"invoice": canon.TWO_DAY},              FEDEX_AIR),
    ("FedEx Home Delivery",    "FedEx",  {"tag": canon.HOME_DELIVERY},            FEDEX_GND),
    ("FedEx Ground",           "FedEx",  {"tag": canon.GROUND},                   FEDEX_GND),
    ("FedEx no signal",        "FedEx",  {},                                      FEDEX_GND),
    ("OnTrac ground",          "OnTrac", {"tag": canon.GROUND},                   ONTRAC),
    ("UPS ground",             "UPS",    {"tag": canon.GROUND},                   UPS_GND),
    ("UPS 2Day (no lane)",     "UPS",    {"invoice": canon.TWO_DAY},              OTHER),
    ("FedEx Overnight",        "FedEx",  {"invoice": canon.OVERNIGHT},            OTHER),
    ("Veho (dead carrier)",    "Veho",   {"tag": canon.GROUND},                   OTHER),
    ("unrecognized carrier",   None,     {},                                      OTHER),
    ("air+ground conflict",    "FedEx",  {"tag": canon.TWO_DAY,
                                          "invoice": canon.GROUND},               OTHER),
)

# (b) MATURED-cohort numeric anchor, TAG BASIS. `_SHIP_2026-07-27` is 31 days old: every leg
# landed weeks ago, so unlike the current week it CANNOT grow underneath the gate.
# 🔴 Chosen because all five lanes are non-zero on it — including `Other / Unmapped` = 372 Veho
# boxes (Veho is dead as a carrier but still inside older windows). A cohort with an empty lane
# would let a regression that drops that lane pass unnoticed.
# 🔴 TAG BASIS (`{}` for the invoice index), and that is not a detail: the frozen LEDGER entry
# for this cohort reads `FedEx 2Day Air: 124` because it was computed WITH invoices, while the
# tag basis reads 117. The 7-box gap is D35's own measured air reconciliation (tag 117 / invoice
# 124), not drift. Verified 2026-08-27 the tag basis reproduces the air-reconciliation table
# published in D35 on all three matured cohorts — 07-27: 117, 08-03: 179, 08-10: 166, exact.
# 🔴 Do NOT re-pin this to the current week. That is the bug this replaced.
ANCHOR_TAG = "_SHIP_2026-07-27"
ANCHOR_AS_OF = "2026-08-27"      # all legs in; cohort closed since 2026-07-28
ANCHOR_COUNTS = {ONTRAC: 1133, FEDEX_GND: 359, FEDEX_AIR: 117, UPS_GND: 244, OTHER: 372,
                 TOTAL: 2225}


class CarrierMixError(RuntimeError):
    """Named so a refusal is greppable in a log."""


class NotPublishable(CarrierMixError):
    """Base: this column must not be PAINTED to the sheet. Catch THIS to mean 'refused'.

    Deliberately the same name and the same shape as `ingest/slack_reship/sync.NotPublishable`
    — one convention for "the number is not wrong, it is not ready", not two.
    """


class CohortNotSettled(NotPublishable):
    """Refusal to paint a cohort whose fulfilment has not landed in the table we are reading."""


class CountsAboveRaw(NotPublishable):
    """Refusal to paint a cohort size larger than the tag population can possibly support."""


# ── Cohort discovery ─────────────────────────────────────────────────────────
def ship_mondays(n, today=None):
    """The last `n` ship Mondays, oldest first. DERIVED FROM THE CALENDAR, never from a
    sheet header and never from the set of tags in the DB.

    🔴 Reading the newest tag present in the data pins the window to whatever already
    shipped, so a cohort that has not been labelled yet can never be discovered and the table
    silently stops walking forward. Non-Monday `_SHIP_` tags exist (`_SHIP_2026-07-24`, 7
    boxes — a drift-in leg) and are NOT ship weeks; anchoring on Monday excludes them.
    """
    d = today or date.today()
    monday = d - timedelta(days=d.weekday())
    return [f"_SHIP_{(monday - timedelta(weeks=i)).isoformat()}" for i in range(n - 1, -1, -1)]


# ── Classification ───────────────────────────────────────────────────────────
def service_signals(tags, carrier, ds_service, inv_service):
    """Every service signal for one box, each labelled by source. Returns
    ``{source: canonical_service_level}`` for the sources that actually said something.

    🔴 A routing-tag signal counts ONLY when the tag names the carrier that actually carried
    the box. A `!FedEx 2Day` tag on a box RMFG handed to OnTrac describes a plan that did not
    happen; letting it vote would invent air on a ground lane.
    🔴 An `!ANY <Courier> - <Hub>_AHB!` tag carries NO service (RMFG picks) — `is_any` is not a
    service level and must never be read as one.
    """
    out = {}
    if inv_service is not None:
        out["invoice"] = canon.normalize_service(inv_service)
    if ds_service is not None and str(ds_service).strip():
        out["delivery_status"] = canon.normalize_service(ds_service)
    p = canon.parse_routing_tag(tags or "")
    if p and not p.get("is_any") and p.get("carrier") == carrier and p.get("service_level"):
        out["tag"] = p["service_level"]
    return {k: v for k, v in out.items() if v != canon.UNKNOWN}


def classify(carrier, signals):
    """(canonical carrier, service signals) → one row label, plus the reason.

    🔴 AIR IS TESTED FIRST AND POSITIVELY. Ground-HD is what a FedEx box falls to only after
    air has been ruled out — never the other way round, and never by asking "does the label
    contain 'Home Delivery'". A substring/partial test on a service label is how a 2Day box
    slides into the ground row while the column still sums to the cohort. This project has
    shipped four separate partial-label-matching bugs into these reports; every fix was the
    same move to full-token, dimension-scoped comparison.
    """
    if carrier is None:
        return OTHER, "unrecognized carrier"
    air = [s for s, lv in signals.items() if lv in AIR_LEVELS]
    ground = [s for s, lv in signals.items() if lv in GROUND_LEVELS]
    if air and ground:
        # Surfaced, never silently resolved — CM_ASSERT_AIR_GROUND_EXCLUSIVE turns this into
        # a refusal at the cohort level.
        return OTHER, f"conflicting service signals air={air} ground={ground}"
    if air:
        if carrier == "FedEx" and all(signals[s] == canon.TWO_DAY for s in air):
            return FEDEX_AIR, f"air via {air}"
        # UPS 2Day, an Overnight of any carrier, an OnTrac "air": no row exists for these and
        # inventing one is worse than showing them. Named, never folded into a ground lane.
        return OTHER, f"{carrier} {signals[air[0]]} — no lane row"
    if carrier == "OnTrac":
        return ONTRAC, "ground (air ruled out)"
    if carrier == "FedEx":
        return FEDEX_GND, "ground/HD (air ruled out)"
    if carrier == "UPS":
        return UPS_GND, "ground (air ruled out)"
    return OTHER, f"{carrier} — no lane row"


# ── Data loading ─────────────────────────────────────────────────────────────
def _invoice_index(con):
    """tracking → (service, cost). The carrier-invoice ingest (`shipments`, fed by the
    `invoices` email ledger) is the ONLY cost authority — see D35."""
    idx = {}
    for trk, svc, cost in con.execute(
            "SELECT tracking, service, cost FROM shipments WHERE tracking IS NOT NULL"):
        idx[str(trk).strip()] = (svc, cost)
    return idx


def _delivery_service_index(con):
    """order_number → delivery_status.service, for the rows where it is populated.

    🔴 Measured 2026-08-25: this column is NULL on **all 118,909 rows**, every carrier, every
    cohort. The branch is kept because the contract names it and it may start carrying data,
    but its coverage is reported every run — a signal that is silently always-absent is
    indistinguishable from one that is silently always-wrong.
    """
    return {o: s for o, s in con.execute(
        "SELECT order_number, service FROM delivery_status WHERE service IS NOT NULL AND TRIM(service) <> ''")}


def _cohort_rows(con, tag):
    return con.execute(
        "SELECT order_number, tags, tracking_number, tracking_company, updated_at "
        "FROM fulfillments WHERE tags LIKE ?", (f"%{tag}%",)).fetchall()


def unresolved_orders(con, tag, labelled):
    """Orders in the cohort that have NO label yet → the fence has not resolved.

    Returns ``(pending_count_or_None, replica_completeness)``.

    🔴 A SET DIFFERENCE on `order_number`, never a subtraction of two counts. Labels outnumber
    open orders whenever an order cancels *after* its label is cut, so `orders - labels` goes
    negative on a perfectly healthy week and says nothing about which boxes are actually
    waiting. Join on `order_number` — never `tracking_number` (FedEx reuses them).

    🔴 Returns None when the replica is not credible rather than a plausible small number. The
    local `shopify_orders` copy goes stale silently (the 7/07 dead-cadence class): measured
    2026-08-25 it held 1,005 open orders for `_SHIP_2026-08-24` against 2,500 real labels, i.e.
    40% complete. A set difference against 40% of the cohort under-reports pending to near zero
    — the flattering direction, which is exactly the one that needs proof.
    """
    try:
        orders = {r[0] for r in con.execute(
            "SELECT order_name FROM shopify_orders WHERE ship_tag=? "
            "AND (cancelled_at IS NULL OR cancelled_at='')", (tag,))}
    except Exception:
        return None, None
    if not orders or not labelled:
        return None, 0.0
    # Key-format guard: `#132940` vs `132940` has produced confident zeros here twice.
    norm = {str(o).lstrip("#").strip() for o in orders}
    lab = {str(o).lstrip("#").strip() for o in labelled}
    completeness = len(norm) / len(lab)
    if completeness < REPLICA_MIN_COMPLETENESS:
        return None, completeness
    return len(norm - lab), completeness


# ── Column build ─────────────────────────────────────────────────────────────
def build_column(con, tag, inv, dss):
    """One ship week → counts, cost, coverage, provenance. Pure read."""
    rows = _cohort_rows(con, tag)
    counts = {r: 0 for r in LANES}
    spend = {r: 0.0 for r in LANES}
    invoiced = {r: 0 for r in LANES}
    reasons, conflicts, basis_used = {}, [], {"invoice": 0, "delivery_status": 0, "tag": 0, "none": 0}
    max_updated = ""

    for onum, tags, trk, tc, upd in rows:
        max_updated = max(max_updated, upd or "")
        carrier = canon.normalize_carrier(tc)
        iv = inv.get(str(trk).strip())
        sig = service_signals(tags, carrier, dss.get(onum), iv[0] if iv else None)
        row, why = classify(carrier, sig)
        counts[row] += 1
        reasons.setdefault(row, {}).setdefault(why, 0)
        reasons[row][why] += 1
        if "air=" in why:
            conflicts.append((onum, why))
        for src in ("invoice", "delivery_status", "tag"):
            if src in sig:
                basis_used[src] += 1
                break
        else:
            basis_used["none"] += 1
        if iv and iv[1] is not None:
            invoiced[row] += 1
            spend[row] += float(iv[1])

    total = len(rows)
    pending, completeness = unresolved_orders(con, tag, {r[0] for r in rows})
    return {
        "tag": tag, "counts": counts, "total": total, "pending": pending,
        "replica_completeness": completeness, "spend": spend, "invoiced": invoiced,
        "coverage": {r: (invoiced[r] / counts[r] if counts[r] else None) for r in LANES},
        "reasons": reasons, "conflicts": conflicts, "basis_used": basis_used,
        "source_max_updated_at": max_updated,
        "age_days": (date.today() - date.fromisoformat(tag.replace("_SHIP_", ""))).days,
    }


# ── Asserts (named, greppable, refuse — never repair) ────────────────────────
def assert_column(col, known_key=None):
    tag = col["tag"]
    if col["total"] == 0:
        return [f"CM_NO_LABELS_YET: {tag} has no fulfillments rows — column is not written"]
    notes = []
    # CM_ASSERT_ROWS_SUM_TO_COHORT — the hub-literal undercount class: a box that matches no
    # row must never simply vanish. Every box lands in exactly one of LANES by construction;
    # this proves the construction, on the numbers that will actually be published.
    s = sum(col["counts"].values())
    if s != col["total"]:
        raise CarrierMixError(
            f"CM_ASSERT_ROWS_SUM_TO_COHORT: {tag} rows sum to {s}, cohort is {col['total']}")
    # CM_ASSERT_AIR_GROUND_EXCLUSIVE — a box may not satisfy both tests. If one ever does it is
    # a real finding about the sources disagreeing, not something to pick a winner for.
    if col["conflicts"]:
        raise CarrierMixError(
            f"CM_ASSERT_AIR_GROUND_EXCLUSIVE: {tag} has {len(col['conflicts'])} box(es) with both an "
            f"air and a ground signal, e.g. {col['conflicts'][:3]}")
    # CM_ASSERT_KNOWN_KEY_PASSES — a zero is a claim. Prove a known-present order survives the
    # filter before believing any bucket that reads 0. Key formats have differed here (`#132940`
    # vs `132940`) and produced confident zeros twice.
    if known_key is not None and known_key not in col["_keys"]:
        raise CarrierMixError(f"CM_ASSERT_KNOWN_KEY_PASSES: {tag} lost known order {known_key!r}")
    if col["counts"][OTHER]:
        # Allowed, but never a bare number: every box in it is itemized by reason.
        notes.append(f"CM_UNMAPPED: {tag} {OTHER}={col['counts'][OTHER]} → {col['reasons'].get(OTHER)}")
    if col["pending"] is None:
        cp = col["replica_completeness"]
        notes.append(f"CM_PENDING_UNKNOWN: {tag} shopify_orders replica is "
                     f"{'absent' if cp is None else f'{cp:.0%} complete'} against the label count — "
                     f"pending reported as unknown, never 0, and this column CANNOT freeze")
    elif col["pending"] > 0:
        notes.append(f"CM_FENCES_OPEN: {tag} has {col['pending']} order(s) with no label yet — "
                     f"their carrier is genuinely undecided; column stays provisional")
    if col["source_max_updated_at"]:
        age = (datetime.now() - datetime.fromisoformat(col["source_max_updated_at"])).days
        if age > STALE_AFTER_DAYS:
            notes.append(f"CM_STALE_SOURCE: {tag} fulfillments last touched {age}d ago "
                         f"(> {STALE_AFTER_DAYS}d) — the ingest may be dead")
    return notes


# ── D41 publish gates (paint path only — see `write_sheet`) ──────────────────
def fulfilment_legs(con, tag):
    """``{fulfilled_at date: row count}`` for the cohort. The cohort's SHAPE, not its size."""
    return {d: c for d, c in con.execute(
        "SELECT substr(fulfilled_at,1,10) d, COUNT(*) FROM fulfillments "
        "WHERE tags LIKE ? GROUP BY d", (f"%{tag}%",)) if d}


def assert_cohort_settled(con, tag, today=None):
    """🔴 D41 — DO NOT PAINT AN OPEN WEEK. Two arms, because the calendar alone is not enough.

    THE DEFECT THIS EXISTS FOR. The `Carrier Mix` tab published **2,500** for
    `_SHIP_2026-08-24` — neither the raw tag count (2,545) nor any reship-excluded net (2,503).
    2,500 is the MONDAY-ONLY count: `fulfilled_at` on that cohort is 2,500 rows dated 08-24 and
    45 dated 08-25, the Tuesday Dallas leg that lands EVERY week. Its four older columns equal
    the full raw total, so this is not a definition problem — one column was painted over a
    half-landed cohort and never restated, and this tab has no scheduled owner to restate it.

    🔴 A WEEKDAY GATE ALONE WOULD NOT HAVE CAUGHT IT, and that is the whole lesson. The paint
    ran at 2026-08-26 09:52 ET — a WEDNESDAY, two days after the Tuesday leg was fulfilled. The
    week was closed; what was still open was OUR INGEST. `build_column` counts rows in
    `fulfillments`, and those 45 rows were not in the table yet. So the second arm asks the
    DATA, not the calendar: a cohort with Monday rows and ZERO Tuesday rows has not finished
    arriving here, whatever day it is. (`updated_at` cannot answer this — it is an ingest
    metadata column that gets re-stamped wholesale; on 2026-09-01 it read `2026-09-01` for all
    2,545 rows. Distinguishing metadata from event dates is the standing rule.)

    Scoped to the PAINT. `--no-ledger`/terminal rendering and the ledger's own self-healing are
    untouched: the ledger reconciles and reports growth every run, which is how the tab would
    heal if anything ran it. A durable tab that nobody re-checks is the surface that needs the
    refusal.
    """
    monday = date.fromisoformat(tag.replace("_SHIP_", ""))
    ready = monday + timedelta(days=COHORT_SETTLES_DAY_OFFSET)
    d = today or date.today()
    if d < ready:
        raise CohortNotSettled(
            f"CM_COHORT_WEEK_OPEN: {tag} has not finished fulfilling — it settles Wednesday "
            f"{ready.isoformat()} and today is {d.isoformat()}. Painting now publishes a "
            "partially-fulfilled cohort into a tab with no scheduled owner to restate it.")
    legs = fulfilment_legs(con, tag)
    mon_n = legs.get(monday.isoformat(), 0)
    tue = (monday + timedelta(days=1)).isoformat()
    if mon_n and not legs.get(tue, 0):
        raise CohortNotSettled(
            f"CM_TUESDAY_LEG_MISSING: {tag} has {mon_n} fulfilments dated {monday.isoformat()} "
            f"and ZERO dated {tue}. The Tuesday Dallas leg lands every week (measured 39–119 "
            "boxes across seven cohorts), so it has not reached this table yet — the calendar "
            "week is closed but the ingest is not. This is the exact shape of the 2,500 that "
            "was painted for _SHIP_2026-08-24 against a true 2,545.")


def assert_counts_not_above_raw(col, entry):
    """🔴 D41 CEILING — the painted lane counts can never sum above the raw tag population.

    Every valid basis is a SUBSET of `tags LIKE '%_SHIP_<week>%'`, and `col["total"]` IS that
    population, so `published > total` is impossible rather than merely odd — it means the
    frozen distribution and the live cohort are not the same cohort. The sibling instance on the
    `TnT2` tab is what motivates it: 2,227 published for `_SHIP_2026-07-27` against a raw
    uncancelled tag population of 2,226, one box that cannot exist, live for nine weeks.
    """
    published = sum((entry.get("counts") or {}).values())
    if published > col["total"]:
        raise CountsAboveRaw(
            f"CM_COUNTS_ABOVE_RAW: {col['tag']} painted lane counts sum to {published}, above "
            f"the raw tag population {col['total']} by {published - col['total']}. Every valid "
            "basis is a subset of that population, so this cannot be a real cohort size.")


# ── Ledger (write-once per matured cell) ─────────────────────────────────────
def _load_ledger():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    return {"_schema": 1, "columns": {}}


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def reconcile_ledger(led, col):
    """Apply D35's TWO independent clocks to one column, and refuse to restate a frozen cell.

    🔴 The counts and the costs mature on different clocks and are frozen SEPARATELY. Freezing
    the whole column when the counts settle would nail the cost cells to whatever partial
    invoice data happened to exist that day — the cost half arrives days-to-weeks later.
    """
    tag = col["tag"]
    e = led["columns"].setdefault(tag, {"counts": None, "counts_frozen": False,
                                        "cost": None, "cost_frozen": {}, "log": []})
    now = datetime.now().isoformat(timespec="seconds")
    events = []

    # --- COUNT clock: freezes once the fences have resolved -------------------
    # A fence (`!NO …` stack / bare `!ANY`) is not a carrier. Before the labels exist those
    # boxes have no carrier at all and must never be distributed by guess. Resolution is
    # observable as a `fulfillments` row with a non-blank `tracking_company` — RMFG's dock pick
    # materialises as the label. `pending == 0` is therefore "every fence resolved".
    # 🔴 Frozen thereafter because the SERVICE half is read from the routing TAG, and the tag is
    # MUTABLE after ship (D23: `_SHIP_2026-08-10` logged 376 corrective tag writes). A later
    # recompute compares day-0 carriers against tags that are no longer what shipped — it
    # degrades with age instead of converging.
    if e["counts_frozen"]:
        if e["counts"] != col["counts"]:
            raise CarrierMixError(
                f"CM_ASSERT_FROZEN_COUNTS: {tag} recomputed {col['counts']} against frozen "
                f"{e['counts']} — refusing. The ship-time reading is unrecoverable once overwritten.")
        events.append("counts: frozen, unchanged")
    else:
        # 🔴 A COHORT GROWING IS INFORMATION, NOT A FAILURE. Ship weeks are MULTI-LEG: the Monday
        # leg lands first and the Tuesday Dallas leg lands Tuesday night, every week. A column
        # measured before Tuesday night is provisional BY CONSTRUCTION. Name the delta so a
        # reader sees "the Tuesday leg landed" instead of wondering which number is wrong — and
        # so nobody is tempted to turn a weekly-expected change back into a refusal (D35d).
        prev = e.get("counts")
        if prev and prev != col["counts"]:
            moved = {ln: col["counts"][ln] - prev.get(ln, 0)
                     for ln in LANES if col["counts"][ln] != prev.get(ln, 0)}
            events.append(f"counts: GREW {sum(prev.values())} → {col['total']} "
                          f"(+{col['total'] - sum(prev.values())}) — a later leg landed; "
                          f"by lane {moved}")
        e["counts"] = col["counts"]
        # 🔴 D41 — as_of is stamped at COMPUTE time and travels with the number it describes.
        # A cohort keeps accruing (and losing) rows for weeks, so any published count is only
        # true as of an instant; without that instant recorded, every later recompute reads as
        # a discrepancy instead of as the expected drift. Stamped where the value is ASSIGNED,
        # never on the frozen-unchanged path — a frozen cell keeps the as_of it was frozen at.
        e["counts_as_of"] = now
        e["counts_basis"] = COUNTS_BASIS
        age = col["age_days"]
        if col["total"] == 0:
            events.append("counts: no labels yet — nothing to write")
        elif col["pending"] == 0:
            e["counts_frozen"] = True
            events.append(f"counts: FROZEN at {now} (fences resolved, pending=0)")
        elif age is not None and age >= COUNT_FREEZE_MAX_AGE_DAYS and col["pending"] is not None:
            # 🔴 LOUD. A column still carrying pending this long after its Monday means the
            # assignment was never recorded — the silent-gap class, not a slow week.
            e["counts_frozen"] = True
            e["residual_pending"] = col["pending"]
            events.append(f"counts: FORCE-FROZEN at {now} — age {age}d with pending="
                          f"{col['pending']} STILL OPEN (recorded as residual_pending)")
        else:
            events.append(f"counts: provisional (age={age}d, pending={col['pending']})")

    # --- COST clock: per LANE, independent of the counts ----------------------
    e["cost"] = e["cost"] or {}
    for lane in LANES:
        cov = col["coverage"][lane]
        if e["cost_frozen"].get(lane):
            events.append(f"cost[{lane}]: frozen, skipped")
            continue
        if cov is None or col["invoiced"][lane] == 0:
            e["cost"][lane] = None          # 🔴 None renders as "—". NEVER 0.0: a zero claims
            events.append(f"cost[{lane}]: not invoiced yet")   # the lane cost nothing.
            continue
        e["cost"][lane] = {"spend": round(col["spend"][lane], 2),
                           "invoiced": col["invoiced"][lane], "boxes": col["counts"][lane],
                           "coverage": round(cov, 4),
                           # 🔴 Unit divides by INVOICED boxes, not by total boxes. Dividing
                           # measured dollars by a population that was never billed understates
                           # the unit by exactly the uninvoiced share — the denominator has to
                           # come from the same place as the numerator.
                           "per_box": round(col["spend"][lane] / col["invoiced"][lane], 2)}
        if cov >= COST_COMPLETE_COVERAGE:
            e["cost_frozen"][lane] = True
            events.append(f"cost[{lane}]: FROZEN at {now} (coverage {cov:.1%})")
        else:
            events.append(f"cost[{lane}]: partial {cov:.1%}")
    e["log"].append({"at": now, "events": events})
    e["log"] = e["log"][-20:]
    return e


# ── Rendering ────────────────────────────────────────────────────────────────
def _cell_count(e, lane, total):
    """🔴 `e` is `ledger["columns"].get(tag, {})` and IS `{}` for a week that has no ledger entry
    yet — a cohort whose labels have not been cut (`total == 0`) is deliberately never written to
    the ledger, but it IS still rendered as a column. `e["counts"]` therefore raised
    `KeyError: 'counts'` and took the whole run down. Measured 2026-08-26: rendering
    `_SHIP_2026-08-31` alongside the live weeks crashed here.
    🔴 That is an UNATTENDED-RUN KILLER, not a cosmetic bug: `ship_mondays` always includes the
    CURRENT week's Monday, so every run between Monday 00:00 and the moment RMFG cuts that
    week's labels hits it — precisely the window a scheduled owner runs in. It never fired in
    testing because every hand-run happened mid-week with the column already populated.
    `_cell_cost` below already used `.get`; this one had lost it. Missing ledger → `—`, which is
    the same blank-≠-zero reading an un-invoiced cost cell gets, and never a fabricated 0.
    """
    c = (e.get("counts") or {}).get(lane)
    if c is None:
        return "—"
    pct = f"{100.0 * c / total:.0f}%" if total else "n/a"
    return f"{c} ({pct})"


def _cell_cost(e, lane):
    """🔴 Blank ≠ zero. A lane with no invoices renders `—`, never `$0`, and nothing sums it.

    🔴 A PARTIAL cell leads with its coverage and marks the spend "so far". Spend scales with
    coverage, so a lane 40% invoiced shows a real-looking total that is 60% low; the per-box
    unit does NOT scale (it is an average over the invoiced boxes only) and stays comparable.
    Putting the percentage after the dollars is how a partial reads as complete at a glance.
    """
    v = (e.get("cost") or {}).get(lane)
    if not v:
        return "—"
    if e.get("cost_frozen", {}).get(lane):     # same missing-ledger-entry guard as _cell_count
        return f"${v['spend']:,.0f} · ${v['per_box']:.2f}/bx"
    return f"{v['coverage']:.0%} inv · ${v['per_box']:.2f}/bx · ${v['spend']:,.0f} so far"


def _cell_as_of(e):
    """🔴 D41 — when this column's counts were last computed, and whether they are frozen.

    Prefers the explicit `counts_as_of` stamp. Entries frozen BEFORE that field existed carry
    the same fact inside their own event log (`counts: FROZEN at <ts>` / `counts: provisional`),
    so it is read back from there rather than left blank or, worse, back-dated to now — reading
    the ledger's own record is not restating anything.
    """
    if not e:
        return "—"
    ts = e.get("counts_as_of")
    if not ts:
        for entry in reversed(e.get("log") or []):
            if any(ev.startswith("counts:") and "frozen, unchanged" not in ev
                   for ev in entry.get("events", [])):
                ts = entry.get("at")
                break
    if not ts:
        return "unrecorded"
    return f"{ts[:16].replace('T', ' ')}{' (frozen)' if e.get('counts_frozen') else ''}"


def grid(cols, ledger):
    """The pivot as a plain 2-D grid of cell STRINGS — header row first, label in column 0.

    🔴 This is the ONE place ledger state becomes cell text. Both renderers — the markdown
    `render` below and the sheet repaint in `write_sheet` — consume this grid verbatim and add
    only presentation (pipes/bold vs. a Sheets range). Forking either renderer onto its own cell
    logic is how the terminal and the tab drift into showing two different tables (D35c).
    """
    tags = [c["tag"] for c in cols]
    rows = [["Row"] + [t.replace("_SHIP_", "") for t in tags]]
    for lane in LANES:
        rows.append([lane] + [
            _cell_count(ledger["columns"].get(t, {}), lane, next(c["total"] for c in cols if c["tag"] == t))
            for t in tags])
        rows.append([lane + " $"] + [_cell_cost(ledger["columns"].get(t, {}), lane) for t in tags])
    rows.append([PENDING] + [("unknown" if c["pending"] is None else str(c["pending"])) for c in cols])
    rows.append([TOTAL] + [str(c["total"]) for c in cols])
    # Total $ is only meaningful once every lane in the week is complete — a sum over a mix of
    # frozen and partial lanes is a real-looking number that is low by an unknown amount.
    tot = []
    for c in cols:
        e = ledger["columns"].get(c["tag"], {})
        lanes = [ln for ln in LANES if c["counts"][ln]]
        if lanes and all(e.get("cost_frozen", {}).get(ln) for ln in lanes):
            tot.append(f"${sum(e['cost'][ln]['spend'] for ln in lanes):,.0f}")
        else:
            done = sum(1 for ln in lanes if e.get("cost_frozen", {}).get(ln))
            tot.append("—" if not done else f"partial ({done}/{len(lanes)} lanes)")
    rows.append([TOTAL + " $"] + tot)
    # 🔴 D41 — BASIS and as_of ride on every column, in the tab, beside the numbers they
    # describe. `Counts basis` is constant today and written anyway: the sibling `TnT2` tab
    # publishes a DIFFERENT basis (`reship_excluded`, over Shopify orders) for the same weeks,
    # so a reader comparing the two tabs needs both stated rather than inferred. `Counts as_of`
    # is what turns a later recompute from "these numbers disagree" into "of course, that was
    # nine weeks ago" — the confusion that cost a day on 2026-09-01.
    rows.append(["Counts basis"] + [
        (ledger["columns"].get(c["tag"], {}).get("counts_basis") or COUNTS_BASIS) for c in cols])
    rows.append(["Counts as_of"] + [_cell_as_of(ledger["columns"].get(c["tag"], {})) for c in cols])
    return rows


def render(cols, ledger):
    """Markdown pivot: `grid()` plus presentation only (pipes and the approved bolding)."""
    g = grid(cols, ledger)
    out = ["| " + " | ".join(g[0]) + " |", "|---|" + "---:|" * (len(g[0]) - 1)]
    for row in g[1:]:
        label, cells = row[0], row[1:]
        if label in LANES or label == TOTAL:
            label = f"**{label}**"
        if row[0] == TOTAL:
            cells = [f"**{c}**" for c in cells]
        elif row[0] == TOTAL + " $":
            cells = [f"**{c}**" if c.startswith("$") else c for c in cells]
        out.append("| " + label + " | " + " | ".join(cells) + " |")
    return "\n".join(out)


# ── Sheet view (D35c): repaint the `Carrier Mix` tab as a VIEW of the ledger ─
def _foreign_tab(a1_value, tab_is_empty):
    """True when an existing `Carrier Mix` tab is NOT ours to repaint.

    Ours = A1 carries the SHEET_TITLE marker, or the tab is completely empty (a crash between
    the clear and the main batch leaves exactly that state — it must be repaintable, not a
    wall). Anything else is somebody's tab: 🔴 REFUSE, never overwrite. Pure so the refusal is
    exercisable in --self-test without a network.
    """
    if tab_is_empty:
        return False
    return str(a1_value or "").strip() != SHEET_TITLE


def write_sheet(cols, ledger, notes, con):
    """Repaint the `Carrier Mix` tab from `grid()` — full repaint, stamp written LAST.

    🔴 D41 — THE PAINT IS GATED PER COLUMN, and the gate is here rather than in `main` on
    purpose: this is the only durable, unowned surface. The terminal table and the markdown
    report keep every column (a human reads those in context, with the run notes beside them);
    the TAB drops any cohort that has not settled and names it in the note block. Dropping a
    column is loud — the reader sees the week is missing AND why — whereas painting it is
    silent, which is how 2,500 sat there for a week.

    🔴 The LEDGER is the memory (write-once semantics live there and only there); this tab is a
    VIEW and is cleared + rewritten whole every run. Do NOT "fix" the repaint into per-cell
    write-once — that duplicates the ledger's job in a second store and the two will disagree.
    🔴 All values go up with valueInputOption=RAW so every cell lands as literal text and Sheets
    coerces nothing — `—` stays `—` (blank/em-dash ≠ $0; a numeric 0 in an un-invoiced cost cell
    claims the lane cost nothing, D35 failure #7).
    🔴 The `Last refreshed` stamp row is a SEPARATE final write: a missing stamp row = the paint
    died partway and must be rerun. The note row (in the main batch) says exactly that.
    """
    from zoneinfo import ZoneInfo  # noqa: PLC0415 — sheet mode only; keep read paths dep-free

    # google-api-python-client ships no py.typed/stubs — resolvable at runtime (proved by the
    # hold_write/tnt1 writers on this same SA), invisible to a static checker. Suppressed
    # narrowly, same pattern as `from lib import canon` above.
    from googleapiclient.discovery import build  # type: ignore[reportMissingImports]  # noqa: PLC0415

    # 🔴 Gate BEFORE any credential or network call, so a refusal costs nothing and reads first.
    paintable, refusals = [], []
    for c in cols:
        entry = ledger["columns"].get(c["tag"], {})
        # The ceiling is an IMPOSSIBILITY, not a timing problem: it takes the whole paint down
        # rather than quietly dropping one column, because a number that cannot exist means the
        # ledger and the cohort have come apart and nothing on the tab is trustworthy.
        assert_counts_not_above_raw(c, entry)
        try:
            assert_cohort_settled(con, c["tag"])
        except CohortNotSettled as exc:
            refusals.append(str(exc))
            continue
        paintable.append(c)
    if not paintable:
        raise CohortNotSettled(
            "CM_NOTHING_PUBLISHABLE: every column in the window was refused — "
            + " | ".join(refusals))
    notes = list(notes) + [f"CM_COLUMN_NOT_PAINTED: {r}" for r in refusals]
    cols = paintable

    try:
        creds = get_google_credentials(["https://www.googleapis.com/auth/spreadsheets"])
    except RuntimeError as exc:
        raise CarrierMixError(f"CM_SHEET_NO_CREDS: {exc}") from exc
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # Metadata first: prove we can see the spreadsheet (and echo its identity) before writing.
    meta = svc.spreadsheets().get(
        spreadsheetId=SHEET_ID, fields="properties.title,sheets.properties.title").execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    print(f"  sheet: {meta['properties']['title']!r} ({SHEET_ID}) · tabs: {tabs}")

    vals = svc.spreadsheets().values()
    if SHEET_TAB not in tabs:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_TAB}}}]}).execute()
        print(f"  created tab {SHEET_TAB!r}")
    else:
        got = vals.get(spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!A1:B2",
                       valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
        a1 = got[0][0] if got and got[0] else ""
        if _foreign_tab(a1, not got):
            raise CarrierMixError(
                f"CM_SHEET_FOREIGN_TAB: a tab named {SHEET_TAB!r} already exists and its A1 "
                f"({str(a1)[:60]!r}) is not this tool's marker — refusing to overwrite a tab "
                "this tool did not paint.")

    g = grid(cols, ledger)
    block = [[SHEET_TITLE], [""]] + g + [[""]] + [[
        "Rules SSOT: AppyHour/ShippingReports/RESHIP_REPORT_RULES.md D35/D35c · the ledger "
        "(_outputs/reports/carrier_mix_ledger.json) is the write-once MEMORY, this tab is a VIEW "
        "repainted whole each run — do not add per-cell write-once here · '—' = not invoiced yet, "
        "NEVER $0 · if the 'Last refreshed' row below the run notes is missing, the paint is "
        "INCOMPLETE — rerun --write-sheet."]] \
        + [[ln] for ln in ([f"- {n}" for n in notes] or ["- run notes: none"])] \
        + [[""]]
    vals.clear(spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'").execute()
    vals.update(spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!A1",
                valueInputOption="RAW", body={"values": block}).execute()

    # Read back the header row before stamping — the stamp asserts a verified paint, not a sent one.
    hdr_row = 3  # A1 title, A2 blank, row 3 = grid header
    back = vals.get(spreadsheetId=SHEET_ID,
                    range=f"'{SHEET_TAB}'!A{hdr_row}:Z{hdr_row}",
                    valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [[]])[0]
    if [str(v) for v in back[:len(g[0])]] != g[0]:
        raise CarrierMixError(
            f"CM_SHEET_READBACK: header row read back {back!r}, expected {g[0]!r} — "
            "stamp NOT written, tab is marked incomplete by its absence.")

    stamp_row = len(block) + 1
    stamp = (f"Last refreshed: "
             f"{datetime.now(ZoneInfo('America/New_York')):%Y-%m-%d %I:%M %p} ET — paint complete")
    vals.update(spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!A{stamp_row}",
                valueInputOption="RAW", body={"values": [[stamp]]}).execute()
    print(f"  tab {SHEET_TAB!r} repainted: {len(g)} table rows, {len(notes)} note(s), "
          f"stamp at A{stamp_row}")
    return stamp_row


# ── Reproduce gate ───────────────────────────────────────────────────────────
def lane_sets(rows, dss, classifier=classify):
    """Raw `fulfillments` rows → ``(lane → set of order_number, tag_air set)``.

    Two derivations, on purpose:

    * ``lanes`` is whatever ``classifier`` decided, as SETS rather than the counters
      ``build_column`` keeps — so a bucket that is dropped or double-counted shows up as a
      partition failure rather than as a plausible number.
    * ``tag_air`` is the FedEx-2Day set read STRAIGHT OFF THE ROUTING TAG, never through the
      classifier. 🔴 That is the entire point: a check that asks the classifier whether the
      classifier is right cannot fail. If a change folds 2Day into Ground-HD, ``tag_air`` still
      names those boxes and the gate catches them sitting in the ground lane.

    ``classifier`` is injectable ONLY so ``--self-test`` can prove the gate refuses a seeded
    fault. Nothing in the live path ever passes anything but ``classify``.
    """
    lanes = {lane: set() for lane in LANES}
    tag_air = set()
    for onum, tags, _trk, tc, _upd in rows:
        carrier = canon.normalize_carrier(tc)
        sig = service_signals(tags, carrier, dss.get(onum), None)   # tag basis — see gate docstring
        row = classifier(carrier, sig)[0]
        # 🔴 An unrecognized row label is a LOST BOX, never a crash. Swallowing it here is
        # deliberate: the partition check below compares the union against the cohort's own keys
        # and reports exactly how many went missing. A KeyError would turn the hub-literal
        # undercount class into a stack trace instead of a measured finding.
        if row in lanes:
            lanes[row].add(onum)
        p = canon.parse_routing_tag(tags or "")
        if (p and not p.get("is_any") and p.get("carrier") == "FedEx" and carrier == "FedEx"
                and p.get("service_level") == canon.TWO_DAY):
            tag_air.add(onum)
    return lanes, tag_air


def reproduce_gate(con, dss, classifier=classify):
    """🔴 Prove the LOGIC still decides what it decided, BEFORE extending to other weeks.

    Returns ``(ok, checks)`` where ``checks`` is ``[(name, ok, detail), …]``.

    🔴 NOT KEYED TO A VOLUME. The predecessor pinned five literals to the CURRENT week and was
    therefore guaranteed to break every Tuesday night when the Dallas leg landed — it refused to
    render for two days over a cohort that had grown 2500 → 2545 exactly as it does every week.
    A weekly-expected change must never be able to stop the table rendering. Every check below
    holds whether the cohort is 2,500 or 2,545; cohort growth is reported by `reconcile_ledger`
    as information, never as a failure.

    🔴 THE EMPTY INVOICE INDEX IS DELIBERATE — DO NOT "FIX" IT BY PASSING `inv`.
    Every check reads the SHIP-TIME (tag-basis) signal. Feeding the live invoice index in would
    let a later-arriving invoice move the air row (5-9 boxes/week of dock-upgraded air — D35's
    air reconciliation) and the anchor would start failing against a reference that is still
    correct, or pass for the wrong reason once two errors cancelled. Neither this function nor
    `lane_sets` takes an invoice argument, precisely so there is nothing to wire in by accident.
    """
    checks = []

    # 1. CM_GATE_CLASSIFIER_GOLDEN — the frozen truth table. Zero DB, zero volume. This is what
    #    actually catches "2Day merged into Ground-HD" and it cannot be moved by a Tuesday leg.
    bad = [f"{lbl}: got {classifier(c, s)[0]!r} want {want!r}"
           for lbl, c, s, want in CLASSIFIER_GOLDEN if classifier(c, s)[0] != want]
    checks.append(("CM_GATE_CLASSIFIER_GOLDEN", not bad,
                   f"{len(CLASSIFIER_GOLDEN)} cases" if not bad else "; ".join(bad)))

    # 2. CM_GATE_ALIAS_FOLD — OnTrac and LaserShip are ONE carrier (D35 failure #2). Counting
    #    them apart halves the share this whole table exists to watch.
    fold = canon.normalize_carrier("LaserShip")
    checks.append(("CM_GATE_ALIAS_FOLD", fold == "OnTrac", f"LaserShip → {fold!r}"))

    # 3+4. Structure of the CURRENT week, on whatever volume it happens to be.
    tag = ship_mondays(1)[0]
    rows = _cohort_rows(con, tag)
    if not rows:                       # pre-label Monday window — nothing to assert, and that
        tag = ship_mondays(2)[0]       # is not a failure. Fall back to the week that shipped.
        rows = _cohort_rows(con, tag)
    lanes, tag_air = lane_sets(rows, dss, classifier)
    keys = {r[0] for r in rows}
    sizes = sum(len(v) for v in lanes.values())
    union = set().union(*lanes.values()) if lanes else set()
    overlaps = [(a, b) for i, a in enumerate(LANES) for b in LANES[i + 1:] if lanes[a] & lanes[b]]
    part_ok = not overlaps and union == keys and sizes == len(keys)
    checks.append(("CM_GATE_LANE_PARTITION", part_ok,
                   f"{tag}: {len(keys)} boxes → {sizes} across {len(LANES)} disjoint lanes"
                   if part_ok else
                   f"{tag}: overlaps={overlaps} sizes={sizes} keys={len(keys)} "
                   f"lost={len(keys - union)} extra={len(union - keys)}"))

    # 🔴 SUBSET, not equality, and that direction is measured: D35's air reconciliation found the
    # tag is a 100%-PRECISE but INCOMPLETE air signal (5-9 boxes/week are billed 2Day with no
    # 2Day tag — the fence resolved to air at the dock). So every tag-air box must be in the air
    # lane, but the air lane may legitimately hold more once invoices land.
    air_ok = tag_air <= lanes[FEDEX_AIR] and not (tag_air & lanes[FEDEX_GND])
    checks.append(("CM_GATE_AIR_SEPARATE", air_ok,
                   f"{tag}: {len(tag_air)} tag-air boxes, all in {FEDEX_AIR}, none in {FEDEX_GND}"
                   if air_ok else
                   f"{tag}: {len(tag_air - lanes[FEDEX_AIR])} tag-air box(es) missing from "
                   f"{FEDEX_AIR}, {len(tag_air & lanes[FEDEX_GND])} sitting in {FEDEX_GND}"))

    # 5. CM_GATE_MATURED_ANCHOR — the one numeric anchor, on a CLOSED cohort. All legs landed
    #    weeks ago, so it cannot grow; a mismatch here is a real finding, not the calendar.
    acol = build_column(con, ANCHOR_TAG, {}, dss)          # {} = tag basis, on purpose
    got = dict(acol["counts"])
    got[TOTAL] = acol["total"]
    diff = {k: (v, got.get(k)) for k, v in ANCHOR_COUNTS.items() if got.get(k) != v}
    checks.append(("CM_GATE_MATURED_ANCHOR", not diff,
                   f"{ANCHOR_TAG} (as of {ANCHOR_AS_OF}) reproduces exactly: {got}"
                   if not diff else f"{ANCHOR_TAG} (expected, got): {diff}"))

    return all(ok for _, ok, _ in checks), checks


# ── Self-test ────────────────────────────────────────────────────────────────
def self_test(con):
    """Exercise EVERY branch, including the ones a normal week never reaches.

    🔴 Why this exists: `--verify-gate` and a routine 5-week run are a HAPPY PATH. On a normal
    week the cohort replica is complete, so the below-threshold arm of `unresolved_orders` never
    executes, no column is empty, no assert refuses and no frozen cell is challenged. A green
    run over those weeks says nothing about the code that only runs when something is wrong —
    which is the code that matters. Every case below is a branch a normal run does not take.
    """
    results = []

    def check(name, fn):
        try:
            results.append((name, "PASS", fn()))
        except Exception as exc:                      # noqa: BLE001 — reporting harness
            results.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))

    # classify(): every row outcome, including the ones with no live data today.
    for label, carrier, sig, want in [
        ("FedEx tag 2Day → air", "FedEx", {"tag": canon.TWO_DAY}, FEDEX_AIR),
        ("FedEx invoice 2Day → air", "FedEx", {"invoice": canon.TWO_DAY}, FEDEX_AIR),
        ("FedEx Home Delivery → gnd", "FedEx", {"tag": canon.HOME_DELIVERY}, FEDEX_GND),
        ("FedEx no signal → gnd", "FedEx", {}, FEDEX_GND),
        ("OnTrac → ontrac", "OnTrac", {"tag": canon.GROUND}, ONTRAC),
        ("UPS ground → ups", "UPS", {"tag": canon.GROUND}, UPS_GND),
        ("UPS 2Day → other", "UPS", {"invoice": canon.TWO_DAY}, OTHER),
        ("FedEx Overnight → other", "FedEx", {"invoice": canon.OVERNIGHT}, OTHER),
        ("Veho → other", "Veho", {"tag": canon.GROUND}, OTHER),
        ("unknown carrier → other", None, {}, OTHER),
        ("air+ground conflict → other", "FedEx",
         {"tag": canon.TWO_DAY, "invoice": canon.GROUND}, OTHER),
    ]:
        def _c(carrier=carrier, sig=sig, want=want):
            got = classify(carrier, sig)[0]
            assert got == want, f"got {got!r} want {want!r}"
            return want
        check(f"classify: {label}", _c)

    # LaserShip must fold to OnTrac — the share-halving failure.
    check("canon: LaserShip folds to OnTrac",
          lambda: (_ for _ in ()).throw(AssertionError("alias drift"))
          if canon.normalize_carrier("LaserShip") != "OnTrac" else "OnTrac")

    # Sheet repaint gate (D35c) — the refusal arm never runs on a normal --write-sheet.
    def _ownership():
        assert _foreign_tab("", True) is False, "empty tab must be repaintable"
        assert _foreign_tab(SHEET_TITLE, False) is False, "our marker must be repaintable"
        assert _foreign_tab("Weekly Costs", False) is True, "unknown A1 must refuse"
        assert _foreign_tab("", False) is True, "non-empty tab with blank A1 must refuse"
        return "empty/marker repaint · foreign refuses"
    check("sheet: foreign-tab gate refuses (D35c)", _ownership)

    # unresolved_orders: every arm, DETERMINISTICALLY.
    # 🔴 This used to assert that LIVE data reached all three arms, and it went red the moment
    # the `shopify_orders` replica caught up — 20/21 on HEAD, with the below-threshold arm
    # unreachable. That is the same defect as the old reproduce-gate one level down: a test
    # keyed to a transient DATA state rather than to the code it claims to cover. A replica
    # being healthy must not read as a test failure. The arms are now proven against an
    # in-memory fixture (deterministic, always reaches all four) and the LIVE sweep below only
    # REPORTS which arms today's data happens to hit.
    # 🔴 `sqlite3.connect(":memory:")` is an ephemeral fixture DB, NOT `shipping.db`. The
    # read-only doctrine (connect_ro, WAL corruption 6/27 + 7/01) is about the live store; no
    # writer is opened against it here and none ever may be.
    def _fixture(n_orders, tag="_X", table=True):
        mem = sqlite3.connect(":memory:")
        if table:
            mem.execute("CREATE TABLE shopify_orders "
                        "(order_name TEXT, ship_tag TEXT, cancelled_at TEXT)")
            mem.executemany("INSERT INTO shopify_orders VALUES (?,?,NULL)",
                            [(f"#{i}", tag) for i in range(n_orders)])
        return mem

    def _arm_below():
        pend, comp = unresolved_orders(_fixture(40), "_X", {str(i) for i in range(100)})
        assert pend is None, f"below-threshold must return None, got {pend}"
        assert comp is not None and comp < REPLICA_MIN_COMPLETENESS, comp
        return f"replica {comp:.0%} → pending unknown, never 0"
    check("unresolved_orders: BELOW-threshold arm", _arm_below)

    def _arm_above():
        pend, comp = unresolved_orders(_fixture(100), "_X", {str(i) for i in range(90)})
        assert comp is not None and comp >= REPLICA_MIN_COMPLETENESS, comp
        assert pend == 10, f"set difference should be 10, got {pend}"
        return f"replica {comp:.0%} → pending {pend} (set difference)"
    check("unresolved_orders: ABOVE-threshold arm", _arm_above)

    def _arm_empty():
        assert unresolved_orders(_fixture(0), "_X", set()) == (None, 0.0)
        return "no labels → (None, 0.0)"
    check("unresolved_orders: EMPTY arm", _arm_empty)

    def _arm_absent():
        # No `shopify_orders` table at all — the replica-missing arm.
        assert unresolved_orders(_fixture(0, table=False), "_X", {"1"}) == (None, None)
        return "replica absent → (None, None)"
    check("unresolved_orders: replica-ABSENT arm", _arm_absent)

    def _arms_live():
        seen = set()
        for tag in ship_mondays(6) + [ship_mondays(1, date.today() + timedelta(days=7))[0]]:
            lab = {r[0] for r in _cohort_rows(con, tag)}
            pend, comp = unresolved_orders(con, tag, lab)
            if not lab:
                seen.add("empty")
            elif comp is not None and comp < REPLICA_MIN_COMPLETENESS:
                seen.add("below")
            elif pend is not None:
                seen.add("above")
        return f"live data reached {sorted(seen)} (informational — arms proven above)"
    check("unresolved_orders: live arms reached (report only)", _arms_live)

    # ── The gate must FAIL on a seeded fault (D35d) ───────────────────────────
    # 🔴 A GATE THAT ONLY EVER PASSES IS WORSE THAN NONE. The structural gate is keyed to logic
    # rather than to a volume precisely so it survives the weekly Tuesday leg — which is only an
    # improvement if it still refuses a real regression. These seed the two regressions D35 was
    # written to prevent and assert the gate goes red.
    def _gate_green():
        ok, checks_ = reproduce_gate(con, {})
        assert ok, [c for c in checks_ if not c[1]]
        return f"{len(checks_)} checks green on live data"
    check("gate: PASSES on today's data", _gate_green)

    def _fault_air_merged():
        """D35 failure #1: FedEx 2Day folded into Ground-HD. Still sums to the cohort."""
        def merged(carrier, signals):
            row, why = classify(carrier, signals)
            return (FEDEX_GND, why) if row == FEDEX_AIR else (row, why)
        ok, checks_ = reproduce_gate(con, {}, classifier=merged)
        assert not ok, "gate passed with FedEx 2Day merged into Ground-HD"
        red = {n for n, cok, _ in checks_ if not cok}
        assert "CM_GATE_CLASSIFIER_GOLDEN" in red, red
        assert "CM_GATE_AIR_SEPARATE" in red, red
        return f"refused, red: {sorted(red)}"
    check("gate: FAILS when FedEx 2Day merges into Ground-HD", _fault_air_merged)

    def _fault_bucket_dropped():
        """D35 failure #6: boxes that match no row silently vanish, and the table still adds up.

        🔴 The fault is seeded SURGICALLY — it drops only UPS boxes carrying NO service signal,
        which is the shape a fence leaves behind. Every UPS case in `CLASSIFIER_GOLDEN` carries a
        signal, so the truth table stays GREEN and only the live structural check can see this.
        That asymmetry is the point: a frozen truth table cannot cover states it does not
        enumerate, and the partition check is what covers the rest.
        """
        def dropper(carrier, signals):
            row, why = classify(carrier, signals)
            return (None, why) if (row == UPS_GND and not signals) else (row, why)
        ok, checks_ = reproduce_gate(con, {}, classifier=dropper)
        assert not ok, "gate passed with fenced UPS boxes dropped"
        red = {n for n, cok, _ in checks_ if not cok}
        assert red == {"CM_GATE_LANE_PARTITION"}, f"expected partition alone to catch it, got {red}"
        lanes, _ = lane_sets(_cohort_rows(con, ANCHOR_TAG), {}, classifier=dropper)
        base_lanes, _ = lane_sets(_cohort_rows(con, ANCHOR_TAG), {})
        lost = len(base_lanes[UPS_GND]) - len(lanes[UPS_GND])
        assert lost > 0, "fault seeded but no box actually went missing"
        return (f"refused on CM_GATE_LANE_PARTITION alone (truth table stayed green); "
                f"{lost} fenced UPS boxes vanished from {ANCHOR_TAG}")
    check("gate: FAILS when boxes silently vanish", _fault_bucket_dropped)

    def _fault_anchor_moved():
        """The matured anchor must still be a real equality check, not decoration."""
        saved = dict(ANCHOR_COUNTS)
        try:
            ANCHOR_COUNTS[FEDEX_AIR] = saved[FEDEX_AIR] + 1
            ok, checks_ = reproduce_gate(con, {})
            assert not ok, "gate passed against a wrong matured anchor"
            assert "CM_GATE_MATURED_ANCHOR" in {n for n, cok, _ in checks_ if not cok}
        finally:
            ANCHOR_COUNTS.clear()
            ANCHOR_COUNTS.update(saved)
        return "refused a one-box anchor perturbation"
    check("gate: FAILS when the matured anchor moves", _fault_anchor_moved)

    # Each named refusal must actually refuse.
    # 🔴 PRODUCTION-SHAPE fixture: built from `build_column`'s real key set, not hand-picked.
    # A fixture that carries only the keys the test happens to touch green-lights a guard that
    # would KeyError on the real object — this project has shipped fail-opens in guards written
    # to close the last fail-open, every one green against an injected shape.
    # 🔴 Annotated `dict[str, Any]` on purpose. A column IS a heterogeneous record; inferred from
    # the literal its value type is `str | dict[...] | int | list[...] | set[...] | float`, so a
    # checker reads `col["counts"].values()` as a possible `str.values()` and reports a phantom
    # error on every consumer. Declaring the real shape kills the union at its source — never
    # cast it away at a call site, which would hide a genuine mismatch here later.
    base: dict[str, Any] = {
            "tag": "_T", "counts": dict.fromkeys(LANES, 1), "total": 5, "conflicts": [],
            "pending": 0, "reasons": {}, "source_max_updated_at": "",
            "replica_completeness": 1.0, "_keys": {"1"}, "age_days": 1,
            "spend": dict.fromkeys(LANES, 0.0), "invoiced": dict.fromkeys(LANES, 0),
            "coverage": dict.fromkeys(LANES, None), "basis_used": {}}
    _live = build_column(con, ship_mondays(1)[0], {}, {})
    _missing = (set(_live) | {"_keys"}) - set(base)
    assert not _missing, f"self-test fixture is not production-shaped, missing {sorted(_missing)}"

    def _refuses(name, mutate):
        col = dict(base)
        mutate(col)
        try:
            assert_column(col, known_key=col.pop("known", None))
        except CarrierMixError as exc:
            assert name in str(exc), f"wrong refusal: {exc}"
            return name
        raise AssertionError(f"{name} did not fire")

    check("CM_ASSERT_ROWS_SUM_TO_COHORT refuses",
          lambda: _refuses("CM_ASSERT_ROWS_SUM_TO_COHORT", lambda c: c.update(total=99)))
    check("CM_ASSERT_AIR_GROUND_EXCLUSIVE refuses",
          lambda: _refuses("CM_ASSERT_AIR_GROUND_EXCLUSIVE",
                           lambda c: c.update(conflicts=[("#1", "air=['tag'] ground=['invoice']")])))
    check("CM_ASSERT_KNOWN_KEY_PASSES refuses",
          lambda: _refuses("CM_ASSERT_KNOWN_KEY_PASSES", lambda c: c.update(known="999")))

    def _frozen():
        led: dict[str, Any] = {"columns": {}}
        col = dict(base, tag="_F", counts=dict.fromkeys(LANES, 1))
        reconcile_ledger(led, col)                              # freezes (pending=0)
        assert led["columns"]["_F"]["counts_frozen"], "did not freeze on pending=0"
        moved = dict.fromkeys(LANES, 1)
        moved[FEDEX_AIR] = 99                      # pretend the air row moved after the freeze
        col2 = dict(col, counts=moved)
        try:
            reconcile_ledger(led, col2)
        except CarrierMixError as exc:
            assert "CM_ASSERT_FROZEN_COUNTS" in str(exc)
            return "refused restatement"
        raise AssertionError("frozen counts were silently restated")
    check("CM_ASSERT_FROZEN_COUNTS refuses", _frozen)

    def _backstop():
        led: dict[str, Any] = {"columns": {}}
        reconcile_ledger(led, dict(base, tag="_B", pending=3,
                                   age_days=COUNT_FREEZE_MAX_AGE_DAYS + 1))
        e = led["columns"]["_B"]
        assert e["counts_frozen"] and e.get("residual_pending") == 3, e
        return "force-frozen, residual recorded"
    check("count-freeze backstop records residual", _backstop)

    def _growth_is_not_fatal():
        """🔴 THE REGRESSION THAT MOTIVATED D35d: a cohort that GROWS must keep rendering.

        Ship weeks are multi-leg — the Tuesday Dallas leg lands Tuesday night, every week — so a
        column that gained boxes since the last run is the normal cycle, not a fault. It is
        reported by name and the column stays provisional.
        """
        led: dict[str, Any] = {"columns": {}}
        small = dict(base, tag="_G", counts=dict.fromkeys(LANES, 100), total=500, pending=None,
                     age_days=1)
        reconcile_ledger(led, small)
        grown = dict(small, counts=dict(dict.fromkeys(LANES, 100), **{ONTRAC: 145}), total=545)
        e = reconcile_ledger(led, grown)          # must NOT raise
        ev = " ".join(e["log"][-1]["events"])
        assert "GREW 500 → 545" in ev and "+45" in ev, ev
        assert not e["counts_frozen"], "a growing column must stay provisional"
        return "reported '+45, a later leg landed' and kept rendering"
    check("cohort growth is reported, never fatal", _growth_is_not_fatal)

    def _no_freeze_when_unknown():
        led: dict[str, Any] = {"columns": {}}
        reconcile_ledger(led, dict(base, tag="_U", pending=None, age_days=2))
        assert not led["columns"]["_U"]["counts_frozen"], "froze on an unknown denominator"
        return "stayed provisional"
    check("unknown denominator blocks the freeze", _no_freeze_when_unknown)

    # 🔴 RENDER a cohort set that includes a week with NO labels yet. This is the branch that
    # took the run down (KeyError: 'counts') and the one every Monday-morning scheduled run hits,
    # because `ship_mondays` always includes the current week's Monday. Built from the LIVE
    # weeks + the next, unlabelled Monday — not a fixture, so it exercises the real shapes.
    def _render_with_empty_column():
        tags = [ship_mondays(1)[0], ship_mondays(1, date.today() + timedelta(days=7))[0]]
        cols = [build_column(con, t, {}, {}) for t in tags]
        assert any(c["total"] == 0 for c in cols), (
            f"no unlabelled column available to exercise the branch: "
            f"{[(c['tag'], c['total']) for c in cols]}")
        led: dict[str, Any] = {"columns": {}}
        for c in cols:
            if c["total"]:
                reconcile_ledger(led, c)
        table = render(cols, led)
        empty = next(c["tag"] for c in cols if c["total"] == 0)
        assert "$0" not in table, f"an empty column rendered a fabricated zero:\n{table}"
        assert table.count("\n") >= len(LANES), "table lost rows"
        return f"rendered {len(cols)} columns incl. unlabelled {empty}, no fabricated zeros"
    check("render survives a column with no labels yet", _render_with_empty_column)

    # ── D41 publish gates: each must FIRE on the shape that actually burned us ────
    # 🔴 Every case below is keyed to a MEASURED failure, not to a hypothetical. A gate proven
    # only by its happy path is the D35d mistake one level down.
    def _settle_fixture(legs, tag="_SHIP_2026-08-24"):
        mem = sqlite3.connect(":memory:")
        mem.execute("CREATE TABLE fulfillments (tags TEXT, fulfilled_at TEXT)")
        mem.executemany("INSERT INTO fulfillments VALUES (?,?)",
                        [(tag, f"{d}T09:00:00") for d, n in legs.items() for _ in range(n)])
        return mem

    def _open_week():
        # Monday 08-24 itself: the calendar arm, before any data question is asked.
        try:
            assert_cohort_settled(_settle_fixture({"2026-08-24": 2500}),
                                  "_SHIP_2026-08-24", date(2026, 8, 24))
        except CohortNotSettled as exc:
            assert "CM_COHORT_WEEK_OPEN" in str(exc), exc
            return "Monday paint refused; names the Wednesday it settles"
        raise AssertionError("painted an open week")
    check("D41 settle: refuses a cohort whose week is still open", _open_week)

    def _tuesday_missing():
        # THE LIVE DEFECT. Wednesday 08-26, week closed, but only the Monday leg is in the
        # table — exactly the state that published 2,500 against a true 2,545.
        try:
            assert_cohort_settled(_settle_fixture({"2026-08-24": 2500}),
                                  "_SHIP_2026-08-24", date(2026, 8, 26))
        except CohortNotSettled as exc:
            assert "CM_TUESDAY_LEG_MISSING" in str(exc), exc
            assert "2500" in str(exc), "refusal must name the count it refused"
            return "Wed paint of a Monday-only cohort refused — the 2,500 case"
        raise AssertionError("painted a cohort whose Tuesday leg had not landed")
    check("D41 settle: refuses Monday-only data AFTER the week closed", _tuesday_missing)

    def _settled_passes():
        assert_cohort_settled(_settle_fixture({"2026-08-24": 2500, "2026-08-25": 45}),
                              "_SHIP_2026-08-24", date(2026, 8, 26))
        return "both legs present on a closed week → paints"
    check("D41 settle: PASSES once the Tuesday leg has landed", _settled_passes)

    def _settle_live():
        # The seven measured cohorts must all be paintable today, or the gate refuses healthy
        # weeks — the failure mode that made the OLD reproduce-gate unusable for two days.
        bad = []
        for t in ["_SHIP_2026-07-13", "_SHIP_2026-07-20", "_SHIP_2026-07-27", "_SHIP_2026-08-03",
                  "_SHIP_2026-08-10", "_SHIP_2026-08-17", "_SHIP_2026-08-24"]:
            try:
                assert_cohort_settled(con, t)
            except CohortNotSettled as exc:
                bad.append(f"{t}: {str(exc).split(':')[0]}")
        assert not bad, bad
        return "7/7 measured cohorts paintable on live data"
    check("D41 settle: does NOT refuse the seven healthy cohorts", _settle_live)

    def _ceiling_fires():
        col = {"tag": "_SHIP_2026-07-27", "total": 2226}
        try:
            assert_counts_not_above_raw(col, {"counts": {ONTRAC: 2227}})
        except CountsAboveRaw as exc:
            assert "2227" in str(exc) and "2226" in str(exc), "must name BOTH numbers"
            return "refused 2227 against a raw population of 2226 — the TnT2 shape"
        raise AssertionError("published a cohort size above its own tag population")
    check("D41 ceiling: refuses a count above the raw tag population", _ceiling_fires)

    def _ceiling_allows_equal():
        assert_counts_not_above_raw({"tag": "_X", "total": 2226}, {"counts": {ONTRAC: 2226}})
        assert_counts_not_above_raw({"tag": "_X", "total": 2226}, {})     # no ledger entry yet
        return "equal passes; a missing ledger entry is not a violation"
    check("D41 ceiling: passes at exactly the ceiling and on an empty entry", _ceiling_allows_equal)

    def _as_of_stamped():
        led: dict[str, Any] = {"columns": {}}
        e = reconcile_ledger(led, dict(base, tag="_A", pending=1, age_days=2))
        assert e.get("counts_as_of"), "counts written with no as_of stamp"
        assert e.get("counts_basis") == COUNTS_BASIS, e.get("counts_basis")
        first = e["counts_as_of"]
        # A frozen cell must KEEP the as_of it was frozen at — re-stamping it would claim the
        # number was recomputed today when it was not.
        e["counts_frozen"] = True
        reconcile_ledger(led, dict(base, tag="_A", pending=0, age_days=9))
        assert led["columns"]["_A"]["counts_as_of"] == first, "frozen cell was re-stamped"
        return f"stamped {first[:16]} at compute time; frozen cell keeps it"
    check("D41 as_of: stamped on write, never re-stamped once frozen", _as_of_stamped)

    def _as_of_rendered():
        led: dict[str, Any] = {"columns": {}}
        col = dict(base, tag="_R", pending=0, age_days=3)
        reconcile_ledger(led, col)
        g = grid([col], led)
        labels = [r[0] for r in g]
        assert "Counts basis" in labels and "Counts as_of" in labels, labels
        assert g[labels.index("Counts basis")][1] == COUNTS_BASIS
        assert "(frozen)" in g[labels.index("Counts as_of")][1], g[labels.index("Counts as_of")]
        # A week with no ledger entry must not fabricate provenance for numbers it does not have.
        assert _cell_as_of({}) == "—", _cell_as_of({})
        return "basis + as_of ride on every column; no entry renders '—'"
    check("D41 as_of: basis and as_of reach the painted grid", _as_of_rendered)

    for name, status, detail in results:
        print(f"  [{status}] {name}: {detail}")
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\nCM_SELF_TEST: {len(results) - len(failed)}/{len(results)} passed")
    return not failed


# ── Main ─────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="Carrier Mix pivot (read-only)")
    ap.add_argument("--weeks", type=int, default=5)
    ap.add_argument("--verify-gate", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="exercise every branch, including the ones a normal week never reaches")
    ap.add_argument("--no-ledger", action="store_true", help="render only; write nothing")
    ap.add_argument("--write-sheet", action="store_true",
                    help="repaint the `Carrier Mix` tab on the Running Reship sheet (D35c)")
    a = ap.parse_args(argv)
    if a.write_sheet and a.no_ledger:
        # The tab is a VIEW of the persisted ledger; painting a state that was never persisted
        # puts the view ahead of the memory. Refuse the combination rather than pick a side.
        raise CarrierMixError("CM_SHEET_NEEDS_LEDGER: --write-sheet cannot combine with --no-ledger")

    # 🔴 delivery_status reads DO when REPORTING_CLOUD_DB=1; `shipments` (the COST and invoice
    # basis) and `shopify_orders` stay LOCAL — cloud `shipments` is blocked on DO_READ_CONTRACT
    # B2/B2b (two ship_date formats, 25,795 duplicate rows), so a cost number must never come
    # from it. The banner names which store answered; an unreachable DO degrades to local LOUDLY
    # rather than killing the report.
    con, cloud_status = connect_reporting()
    try:
        if a.self_test:
            return 0 if self_test(con) else 1
        # C4 assert: the age of the data prints BESIDE the number, so a reader can never see a
        # count without seeing how old its source is.
        print(cloud_status.banner())
        inv, dss = _invoice_index(con), _delivery_service_index(con)
        print(f"delivery_status.service coverage: {len(dss)} rows populated "
              f"({'DEAD SIGNAL — tag is the sole service source' if not dss else 'live'})")

        ok, checks = reproduce_gate(con, dss)
        print(f"\nCM_REPRODUCE_GATE (structural, tag basis): {'PASS' if ok else 'FAIL'}")
        for name, cok, detail in checks:
            print(f"  [{'PASS' if cok else 'FAIL'}] {name}: {detail}")
        if a.verify_gate:
            return 0 if ok else 1
        if not ok:
            raise CarrierMixError(
                "CM_REPRODUCE_GATE failed — refusing to extend to other weeks. Failing: "
                + ", ".join(n for n, cok, _ in checks if not cok))

        cols, notes = [], []
        for tag in ship_mondays(a.weeks):
            col = build_column(con, tag, inv, dss)
            col["_keys"] = {r[0] for r in _cohort_rows(con, tag)}
            known = next(iter(sorted(col["_keys"]))) if col["_keys"] else None
            notes += assert_column(col, known)
            del col["_keys"]
            cols.append(col)

        led = _load_ledger()
        for col in cols:
            if col["total"]:
                e = reconcile_ledger(led, col)
                # 🔴 CM_COHORT_GREW must be VISIBLE, not buried in the ledger's event log. A
                # cohort gaining boxes between runs is the multi-leg cycle (Tuesday Dallas lands
                # Tuesday night), and the reader needs to see "+45, a later leg landed" rather
                # than silently comparing two different totals across two days — or, as before
                # D35d, being handed a refusal instead of a table.
                notes += [f"CM_COHORT_GREW: {col['tag']} {ev.split('counts: GREW ', 1)[1]}"
                          for ev in e["log"][-1]["events"] if ev.startswith("counts: GREW ")]
        table = render(cols, led)
        print("\n" + table + "\n")
        for n in notes:
            print("  " + n)
        if not a.no_ledger:
            _atomic_write(LEDGER, json.dumps(led, indent=2))
            _atomic_write(REPORT, "# Carrier Mix — ship weeks as columns\n\n"
                          f"Generated {datetime.now():%Y-%m-%d %H:%M} local. Rules: "
                          "`AppyHour/ShippingReports/RESHIP_REPORT_RULES.md` D35.\n\n"
                          + table + "\n\n## Run notes\n\n"
                          + ("\n".join("- " + n for n in notes) or "- none") + "\n")
            print(f"  ledger → {LEDGER}\n  report → {REPORT}")
        if a.write_sheet:
            # After the ledger persists: the view must never be newer than the memory.
            write_sheet(cols, led, notes, con)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
