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

READ-ONLY BY CONSTRUCTION. ``connect_ro`` only — Claude never write-connects ``shipping.db``
(MSIX/WAL corruption, 6/27 + 7/01). There is no sheet-write path in this file on purpose.

Run::

    python carrier_mix_pivot.py                 # last 5 ship weeks, render + update ledger
    python carrier_mix_pivot.py --weeks 8
    python carrier_mix_pivot.py --verify-gate   # reproduce the 2026-08-24 reference numbers
    python carrier_mix_pivot.py --no-ledger     # render only, touch nothing
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))                       # AppyHour/
sys.path.insert(0, str(_HERE.parents[2] / "ShipRouting"))       # ShipRouting/ (canon)

from appyhour_lib.db import connect_ro  # noqa: E402  isort:skip
from lib import canon  # noqa: E402  isort:skip

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
    c = (e["counts"] or {}).get(lane)
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
    if e["cost_frozen"].get(lane):
        return f"${v['spend']:,.0f} · ${v['per_box']:.2f}/bx"
    return f"{v['coverage']:.0%} inv · ${v['per_box']:.2f}/bx · ${v['spend']:,.0f} so far"


def render(cols, ledger):
    """Markdown pivot: ship weeks as COLUMNS, one count row + one cost row per lane."""
    tags = [c["tag"] for c in cols]
    head = "| Row | " + " | ".join(t.replace("_SHIP_", "") for t in tags) + " |"
    sep = "|---|" + "---:|" * len(tags)
    out = [head, sep]
    for lane in LANES:
        out.append("| **" + lane + "** | " + " | ".join(
            _cell_count(ledger["columns"].get(t, {}), lane, next(c["total"] for c in cols if c["tag"] == t))
            for t in tags) + " |")
        out.append("| " + lane + " $ | " + " | ".join(
            _cell_cost(ledger["columns"].get(t, {}), lane) for t in tags) + " |")
    out.append("| " + PENDING + " | " + " | ".join(
        ("unknown" if c["pending"] is None else str(c["pending"])) for c in cols) + " |")
    out.append("| **" + TOTAL + "** | " + " | ".join(f"**{c['total']}**" for c in cols) + " |")
    # Total $ is only meaningful once every lane in the week is complete — a sum over a mix of
    # frozen and partial lanes is a real-looking number that is low by an unknown amount.
    tot = []
    for c in cols:
        e = ledger["columns"].get(c["tag"], {})
        lanes = [ln for ln in LANES if c["counts"][ln]]
        if lanes and all(e.get("cost_frozen", {}).get(ln) for ln in lanes):
            tot.append(f"**${sum(e['cost'][ln]['spend'] for ln in lanes):,.0f}**")
        else:
            done = sum(1 for ln in lanes if e.get("cost_frozen", {}).get(ln))
            tot.append("—" if not done else f"partial ({done}/{len(lanes)} lanes)")
    out.append("| " + TOTAL + " $ | " + " | ".join(tot) + " |")
    return "\n".join(out)


# ── Reproduce gate ───────────────────────────────────────────────────────────
def verify_gate(con, inv, dss):
    """🔴 Reproduce the numbers the system already produced BEFORE trusting any extension.

    Reference computed 2026-08-25 for `_SHIP_2026-08-24`: OnTrac 1763 · FedEx Ground 648 ·
    FedEx 2Day 69 · UPS 20 · Total 2500. If this does not match, the query is wrong — not the
    reference. Note the reference is the SHIP-TIME (tag-basis) reading: that cohort had zero
    invoices when it was taken, so a later invoice-basis run legitimately moves the air row and
    is compared here against the tag basis only.
    """
    col = build_column(con, "_SHIP_2026-08-24", {}, dss)   # {} = no invoices → tag basis
    got = dict(col["counts"])
    got[TOTAL] = col["total"]
    bad = {k: (v, got.get(k)) for k, v in REFERENCE_0824.items() if got.get(k) != v}
    return (not bad), got, bad


# ── Main ─────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="Carrier Mix pivot (read-only)")
    ap.add_argument("--weeks", type=int, default=5)
    ap.add_argument("--verify-gate", action="store_true")
    ap.add_argument("--no-ledger", action="store_true", help="render only; write nothing")
    a = ap.parse_args(argv)

    con = connect_ro()
    try:
        inv, dss = _invoice_index(con), _delivery_service_index(con)
        print(f"delivery_status.service coverage: {len(dss)} rows populated "
              f"({'DEAD SIGNAL — tag is the sole service source' if not dss else 'live'})")

        ok, got, bad = verify_gate(con, inv, dss)
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
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
