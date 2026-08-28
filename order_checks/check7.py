"""Check 7: repeat items vs the customer's previous orders, and the swap list.

  python -m order_checks.check7 --tag RMFG_20260828 --ship _SHIP_2026-08-31 --sheet x.xlsx

Port of Dan's check78.py + check7_swaps.py onto the indexed history.

WHAT COUNTS AS A REPEAT
A free curation child the customer also received in their previous PREV_N orders.
Only "Subscription Recurring Order" boxes; trays are out (a tray box repeats by design).

EXCLUSIONS, all before a repeat becomes a swap:
  * mini jams -- customers can receive these repeatedly (AC-GBEF, AC-SCJ, AC-SRHUB)
  * brie -- part of the curation (the Free-Brie-for-a-Year wrapper adds one every box),
    so a brie repeating is by design, not a rotation miss (Kurt 2026-08-28)
  * anything PRICED separately: deliberately buying a second unit is not a curation error.
    🔴 A priced item is removed ON ITS OWN and never removes the rest of the box from the
    analysis -- an order may hold an exempt paid item while its free items still flag.
  * reships, and any order with no prior history
  * orders a HUMAN customized (recharge_gate) or tagged BOX_CUSTOMIZED_POST_CHECKOUT

🔴 A Recharge rotation is NOT an excuse. Rotation is the mechanism by which a repeat was
delivered, not a justification -- identifying what rotation re-sends to the same customer
is the entire point of this check.

REPORTING (docx): the headline count alone is misleading -- when one item ships in 78% of
boxes, three quarters of customers see it twice across three shipments. Report saturation
alongside it, then severity tiers, then a per-SKU table, then the swap list.

SUBSTITUTE RULE (Daniel 2026-08-18): same type, not already in the box, and the customer
must NEVER have received it in ANY past box -- not merely the last four. So a high-volume
SKU is a fine substitute; the constraint sits on the customer's history, not the item.
Ranking prefers the most recently introduced SKU (first-ever appearance in history), then
greatest volume, so new items work through the base instead of recycling evergreens.
"""
from __future__ import annotations
import argparse
import collections
import csv
import os
import sqlite3
import sys

from .customer_map import recharge_id
from .fetch_gql import fetch_by_name
from .history import DB, previous_orders
from .recharge_gate import customized
from . import sheet as sheetmod

CHILD = ("AC-", "MT-", "CH-", "TR-")
TYPES = ("AC-", "MT-", "CH-")
MINI_JAMS = {"AC-GBEF", "AC-SCJ", "AC-SRHUB"}
# 🔴 Brie is part of the curation, not a rotation miss (Kurt 2026-08-28). The
# "AppyHour Box + Free Brie for a Year" wrapper contributes a brie EVERY box, so a brie
# repeating is by design -- exactly like a mini jam. It was #4 by repeat count (63) and
# #4 by clears-alone (40), so leaving it in inflates both the headline and the swap list.
CURATION_FIXED = {"CH-BRIE"}
REPEAT_EXEMPT = MINI_JAMS | CURATION_FIXED
PREV_N = 2                      # docx: "their previous two orders"
HIST_N = 4                      # swap candidates check the FULL history; 4 is the fallback
RECUR_TAG = "Subscription Recurring Order"
BCPC_TAG = "BOX_CUSTOMIZED_POST_CHECKOUT"


def typ(s):
    for t in TYPES:
        if s.startswith(t):
            return t
    return None


def _live(node):
    return [e["node"] for e in node["lineItems"]["edges"] if (e["node"].get("currentQuantity") or 0) > 0]


def _paid(li):
    return float((li.get("discountedUnitPriceSet") or {}).get("shopMoney", {}).get("amount") or 0) > 0


def sku_first_seen(con):
    """SKU -> first-ever appearance date, for 'prefer the newest item' ranking."""
    return {s: d for s, d in con.execute(
        """SELECT i.sku, MIN(o.created_at) FROM items i JOIN orders o ON o.order_gid = i.order_gid
           WHERE i.qty > 0 GROUP BY i.sku""")}


def run(orders, con, verbose=True):
    """-> (repeats, saturation, per_sku, swaps)."""
    first_seen = sku_first_seen(con)
    in_run = collections.Counter()          # free child SKUs circulating in THIS run
    for o in orders.values():
        for li in _live(o):
            s = (li["sku"] or "").strip()
            if s.startswith(CHILD) and not _paid(li):
                in_run[s] += li["currentQuantity"]

    repeats, skipped = [], collections.Counter()
    for oid, o in sorted(orders.items()):
        tags = o.get("tags") or []
        if RECUR_TAG not in tags:
            skipped["not a recurring subscription order"] += 1
            continue
        if any("reship" in t.lower() for t in tags):
            skipped["reship"] += 1
            continue
        cust = (o.get("customer") or {}).get("id")
        if not cust:
            skipped["no customer"] += 1
            continue
        prev = previous_orders(con, cust, o["createdAt"], PREV_N)
        if not prev:
            skipped["no prior order history"] += 1
            continue
        if BCPC_TAG in tags:
            skipped["BOX_CUSTOMIZED_POST_CHECKOUT"] += 1
            continue
        rid = recharge_id(con, cust)
        was, why = customized(con, rid, since_iso=prev[0][1] if prev else None)
        if was:
            skipped["human customized (recharge events)"] += 1
            continue

        prev_skus = {s for _, _, skus in prev for s in skus}
        box = {(li["sku"] or "").strip() for li in _live(o)}
        hits = []
        for li in _live(o):
            s = (li["sku"] or "").strip()
            if not s.startswith(TYPES) or s in REPEAT_EXEMPT:
                continue
            if _paid(li):                   # exempt this ITEM only, never the whole order
                continue
            if s in prev_skus:
                hits.append(s)
        if hits:
            repeats.append({"order": oid, "customer": cust,
                            "repeats": sorted(hits), "n_repeats": len(hits),
                            "box_size": sum(1 for li in _live(o)
                                            if (li["sku"] or "").startswith(CHILD)),
                            "prev": [p[0] for p in prev], "box": box})

    n_scope = sum(1 for o in orders.values() if RECUR_TAG in (o.get("tags") or []))
    saturation = {s: round(100 * c / max(1, n_scope), 1) for s, c in in_run.most_common(20)}

    per_sku = collections.Counter()
    clears = collections.Counter()
    for r in repeats:
        for s in r["repeats"]:
            per_sku[s] += 1
            if len(r["repeats"]) == 1:      # swapping THIS sku alone clears the order
                clears[s] += 1

    swaps = build_swaps(repeats, orders, con, in_run, first_seen)
    if verbose:
        print(f"  eligible orders: {n_scope}   flagged: {len(repeats)}")
        for k, v in skipped.most_common():
            print(f"    excluded {v:>5}  {k}")
    return repeats, saturation, per_sku, clears, swaps, skipped


def build_swaps(repeats, orders, con, in_run, first_seen):
    """One row per repeated SKU: Order ID, SKU to Swap, Proposed Swap."""
    pool = collections.defaultdict(list)
    for s, vol in in_run.items():
        t = typ(s)
        if t and s not in REPEAT_EXEMPT:
            pool[t].append(s)
    for t in pool:                          # newest first, then greatest volume
        pool[t].sort(key=lambda s: (first_seen.get(s, ""), in_run[s]), reverse=True)

    rows = []
    for r in repeats:
        o = orders[r["order"]]
        cust = r["customer"]
        allsk = [s for t in pool for s in pool[t]]
        ever = _ever_received(con, cust, allsk)
        used = set(r["box"])
        for s in r["repeats"]:
            t = typ(s)
            cand = next((c for c in pool.get(t, [])
                         if c not in ever and c not in used), None)
            if cand:
                used.add(cand)
            rows.append({"order": r["order"], "sku_to_swap": s,
                         "proposed_swap": cand or "UNFILLED",
                         "type": t, "note": "" if cand else
                         "no same-type SKU in this run the customer has never received"})
    return rows


def _ever_received(con, customer_gid, skus):
    if not skus:
        return set()
    marks = ",".join("?" * len(skus))
    return {r[0] for r in con.execute(
        f"""SELECT DISTINCT i.sku FROM items i JOIN orders o ON o.order_gid = i.order_gid
            WHERE o.customer_id = ? AND i.qty > 0 AND i.sku IN ({marks})""",
        [customer_gid, *skus])}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check7")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--ship")
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--cache")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)

    sheet = sheetmod.load_sheet(a.sheet)
    orders = fetch_by_name(list(sheet), cache=a.cache)
    con = sqlite3.connect(DB)
    repeats, sat, per_sku, clears, swaps, _ = run(orders, con)

    tier3 = [r for r in repeats if r["n_repeats"] >= 3]
    tierh = [r for r in repeats if r["box_size"] and r["n_repeats"] / r["box_size"] >= 0.5]
    print(f"\n  >=3 repeats            {len(tier3)}")
    print(f"  half the box or more   {len(tierh)}")
    print("\n  saturation (share of eligible orders carrying the SKU):")
    for s, pct in list(sat.items())[:10]:
        print(f"    {s:<12}{pct:>6}%")
    print(f"\n  per-SKU: how many orders repeat it / how many clear if ONLY it is swapped")
    for s, n in per_sku.most_common(12):
        print(f"    {s:<12}{n:>5}{clears.get(s, 0):>7}")

    def dump(name, rows, cols=None):
        if not rows:
            return
        p = os.path.join(a.out, name)
        with open(p, "w", newline="", encoding="utf8") as fh:
            w = csv.DictWriter(fh, cols or list(rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"  -> {p} ({len(rows)})")

    dump(f"check7_repeats_{a.tag}.csv",
         [{k: (",".join(v) if isinstance(v, (list, set)) else v)
           for k, v in r.items() if k != "box"} for r in repeats])
    dump(f"check7_swaps_{a.tag}.csv", swaps)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
