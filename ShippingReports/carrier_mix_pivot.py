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
    python carrier_mix_pivot.py --verify-gate   # reproduce the 2026-08-24 reference numbers
    python carrier_mix_pivot.py --self-test     # exercise the branches a normal week never hits
    python carrier_mix_pivot.py --no-ledger     # render only, touch nothing
    python carrier_mix_pivot.py --write-sheet   # …and repaint the `Carrier Mix` sheet tab (D35c)

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
SHEET_CREDS = _HERE.parents[1] / "shipping-perfomance-review-accd39ac4b78.json"  # gitignored SA key
# A1 marker: ownership test for the repaint gate AND the tab's visible title. 🔴 Written in the
# MAIN batch; the "Last refreshed" stamp row is written LAST in a separate call, so a missing
# stamp row = an incomplete paint (crash between clear and finish), loudly visible.
SHEET_TITLE = "Carrier Mix — ship weeks as columns (D35)"

# The `fulfillments` ingest (sync_logon.py) is expected to touch rows at least this often;
# mirrors `_outputs/scripts/freshness_sweep.py`'s own 3-day rule for the same table.
STALE_AFTER_DAYS = 3

REFERENCE_0824 = {ONTRAC: 1763, FEDEX_GND: 648, FEDEX_AIR: 69, UPS_GND: 20, TOTAL: 2500}


class CarrierMixError(RuntimeError):
    """Named so a refusal is greppable in a log."""


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
        e["counts"] = col["counts"]
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


def write_sheet(cols, ledger, notes):
    """Repaint the `Carrier Mix` tab from `grid()` — full repaint, stamp written LAST.

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
    from google.oauth2.service_account import Credentials  # type: ignore[reportMissingImports]  # noqa: PLC0415
    from googleapiclient.discovery import build  # type: ignore[reportMissingImports]  # noqa: PLC0415

    if not SHEET_CREDS.exists():
        raise CarrierMixError(f"CM_SHEET_NO_CREDS: {SHEET_CREDS} not found")
    creds = Credentials.from_service_account_file(
        str(SHEET_CREDS), scopes=["https://www.googleapis.com/auth/spreadsheets"])
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
def verify_gate(con, dss):
    """🔴 Reproduce the numbers the system already produced BEFORE trusting any extension.

    Reference computed 2026-08-25 for `_SHIP_2026-08-24`: OnTrac 1763 · FedEx Ground 648 ·
    FedEx 2Day 69 · UPS 20 · Total 2500. If this does not match, the query is wrong — not the
    reference.

    🔴 THE EMPTY INVOICE INDEX BELOW IS DELIBERATE — DO NOT "FIX" IT BY PASSING `inv`.
    The reference is the SHIP-TIME (tag-basis) reading: `_SHIP_2026-08-24` had zero invoices
    when it was taken. Feeding the live invoice index in would let a later-arriving invoice move
    the air row (5-9 boxes/week of dock-upgraded air — see D35's air reconciliation) and the gate
    would start failing against a reference that is still correct, or worse, pass for the wrong
    reason once the two errors cancelled. This function takes NO invoice argument precisely so
    there is nothing to wire in by accident.
    """
    col = build_column(con, "_SHIP_2026-08-24", {}, dss)   # {} = tag basis, on purpose — see above
    got = dict(col["counts"])
    got[TOTAL] = col["total"]
    bad = {k: (v, got.get(k)) for k, v in REFERENCE_0824.items() if got.get(k) != v}
    return (not bad), got, bad


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

    # unresolved_orders: all three arms, against the live DB.
    def _arms():
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
        assert seen >= {"above", "below", "empty"}, f"arms exercised: {sorted(seen)}"
        return sorted(seen)
    check("unresolved_orders: all 3 arms reached", _arms)

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

        ok, got, bad = verify_gate(con, dss)
        print(f"\nCM_REPRODUCE_GATE (_SHIP_2026-08-24, tag basis): "
              f"{'PASS' if ok else 'FAIL'}  {got}")
        if bad:
            print(f"  mismatches (expected, got): {bad}")
        if a.verify_gate:
            return 0 if ok else 1
        if not ok:
            raise CarrierMixError("CM_REPRODUCE_GATE failed — refusing to extend to other weeks")

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
                reconcile_ledger(led, col)
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
            write_sheet(cols, led, notes)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
