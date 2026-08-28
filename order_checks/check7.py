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
import json
import os
import re
import sqlite3
import sys

from .customer_map import recharge_id
from .fetch_gql import fetch_by_name
from .history import DB, previous_orders
from .recharge_gate import customized
from . import sheet as sheetmod

CHILD = ("AC-", "MT-", "CH-", "TR-")
TYPES = ("AC-", "MT-", "CH-")
# AC-MFJ "Mini Fig Jam" is a mini jam by name but is NOT in Dan's set; without it the
# substitute bar missed 107 rows (Kurt 2026-08-28).
MINI_JAMS = {"AC-GBEF", "AC-SCJ", "AC-SRHUB", "AC-MFJ"}
# 🔴 Brie is part of the curation, not a rotation miss (Kurt 2026-08-28). The
# "AppyHour Box + Free Brie for a Year" wrapper contributes a brie EVERY box, so a brie
# repeating is by design -- exactly like a mini jam. It was #4 by repeat count (63) and
# #4 by clears-alone (40), so leaving it in inflates both the headline and the swap list.
CURATION_FIXED = {"CH-BRIE", "CH-EBRIE", "CH-PBRIE"}   # every brie (Kurt 2026-08-28)
REPEAT_EXEMPT = MINI_JAMS | CURATION_FIXED
# Never PROPOSE these as a substitute (Kurt 2026-08-28). Newest-SKU-first ranking put
# AC-RMC into 190 rows and MT-IBRES into 164 -- "newest" is not "wanted", and a ranking
# with no declared pool pours whatever is new across the entire run.
NO_SUBSTITUTE = {"AC-RMC", "MT-IBRES"}
# Never allocate a substitute below this many units remaining. Kurt 2026-08-28:
# "don't zero out blucar ... get it to 20 have left" -- a swap plan that drains a SKU
# to nothing leaves nothing for next week's cut or a short.
RESERVE_FLOOR = 20
# Declared HAVE inventory -- the cut order's own corrected_inventory_path, NOT MCP
# get_calculated_inventory (which is wrong and must never be quoted as HAVE).
HAVE_XLSX = r"C:\Users\Work\Downloads\Corrected_Inventory_08-25.xlsx"
PREV_N = 2                      # docx: "their previous two orders"
HIST_N = 4                      # swap candidates check the FULL history; 4 is the fallback
AUDIT_LOG = r"C:\Users\Work\Claude Projects\_outputs\logs\swap_audit.jsonl"
RECUR_TAG = "Subscription Recurring Order"
BCPC_TAG = "BOX_CUSTOMIZED_POST_CHECKOUT"


# A CRACKER is its own type, not an interchangeable AC-. Kurt 2026-08-28: AC-FCFIGO was
# being proposed for AC-MISS (figs) and AC-QUIC (nuts) -- "we can't do AC-FCFIGO, because
# those are crackers." Derived from product titles rather than hardcoded, plus AC-TOK
# (Toketti) which Kurt declared a cracker and whose title carries no cracker word.
CRACKER_TITLE = re.compile(r"cracker|crisp|flatbread|pretzel|blini|toast", re.I)
CRACKER_EXTRA = {"AC-TOK"}


def build_cracker_set(orders):
    """-> set of AC- SKUs that are crackers, read off the run's own product titles."""
    out = set(CRACKER_EXTRA)
    for o in orders.values():
        for e in o["lineItems"]["edges"]:
            n = e["node"]
            s = (n["sku"] or "").strip()
            if s.startswith("AC-") and CRACKER_TITLE.search(n.get("title") or ""):
                out.add(s)
    return out


def typ(s, crackers=frozenset()):
    """Swap type. A cracker only ever swaps for another cracker."""
    if s in crackers:
        return "CRACKER"
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


def load_have(path=None):
    """SKU -> on-hand qty from the declared HAVE workbook.

    RED FLAG: this is the cut order's corrected_inventory_path. NEVER substitute MCP
    get_calculated_inventory -- it is wrong and must not be quoted as HAVE. The file is
    a point-in-time count, so a swap proposed against it is only as fresh as the count:
    state the file date in any output built from it.
    """
    import openpyxl
    path = path or HAVE_XLSX
    if not os.path.exists(path):
        return {}
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(x or "").strip().lower() for x in rows[0]]
    try:
        i_sku = hdr.index("sku")
        i_qty = hdr.index("qty")
    except ValueError:
        return {}
    have = {}
    for r in rows[1:]:
        s = str(r[i_sku] or "").strip()
        if s and isinstance(r[i_qty], (int, float)):
            have[s] = int(r[i_qty])
    return have


def swapped_today(audit_path=None, day=None):
    """Orders + SKUs already swapped, so we never propose a second swap on them.

    Kurt 2026-08-28: "we did a bunch of swaps today right? let's avoid those."

    RED FLAG: the audit log is INCOMPLETE and appyhour_swap_order_skus returns
    success:False WITHOUT raising, so this is a FLOOR, never the authority -- confirm
    with whoever ran them. Rows whose result is "intent" were logged BEFORE the write
    and may not have landed. Swaps done via Matrixify, a manual Shopify edit, or
    Recharge never appear here at all.
    """
    import datetime
    audit_path = audit_path or AUDIT_LOG
    day = day or datetime.date.today().isoformat()
    orders_hit, sku_hit = set(), collections.defaultdict(set)
    if not os.path.exists(audit_path):
        return orders_hit, sku_hit
    for line in open(audit_path, encoding="utf8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not str(r.get("ts", "")).startswith(day):
            continue
        gid = r.get("order_gid") or ""
        num = gid.rsplit("/", 1)[-1] if gid else ""
        name = (r.get("order_name") or "").lstrip("#")
        skus = [s.split("(")[0].split("->")[0] for s in (r.get("swaps") or [])]
        if r.get("old_sku"):
            skus.append(r["old_sku"])
        for key in filter(None, (num, name)):
            orders_hit.add(key)
            sku_hit[key].update(skus)
    return orders_hit, sku_hit


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

    swaps = build_swaps(repeats, orders, con, in_run, first_seen,
                        *swapped_today(), have=load_have(),
                        crackers=build_cracker_set(orders))
    if verbose:
        print(f"  eligible orders: {n_scope}   flagged: {len(repeats)}")
        for k, v in skipped.most_common():
            print(f"    excluded {v:>5}  {k}")
    return repeats, saturation, per_sku, clears, swaps, skipped


def build_swaps(repeats, orders, con, in_run, first_seen,
                done_orders=frozenset(), done_skus=None, have=None,
                crackers=frozenset()):
    """One row per repeated SKU: Order ID, SKU to Swap, Proposed Swap."""
    have = have or {}
    done_skus = done_skus or {}
    pool = collections.defaultdict(list)
    for s, vol in in_run.items():
        t = typ(s, crackers)
        # 🔴 A mini jam is exempt from repeat DETECTION (customers may receive them
        # repeatedly) but is also barred as a SUBSTITUTE -- "its not enough" (Kurt
        # 2026-08-28): a mini jam does not replace a full accompaniment. AC-MFJ was
        # being proposed 107 times before this.
        if t and s not in REPEAT_EXEMPT and s not in NO_SUBSTITUTE and s not in MINI_JAMS:
            pool[t].append(s)
    # Rank by REMAINING HEADROOM (have - committed), then by how new the SKU is.
    # 🔴 Newest-first alone is wrong: it buried AC-BRJA (2,284 on hand, 60 committed --
    # the substitute Kurt's own declared list uses 175 times) behind AC-CARM and AC-MFJ
    # purely because those are newer, and poured one new SKU across the whole run.
    # Headroom-first spreads load the way the declared list does and keeps a
    # nearly-exhausted SKU (AC-BLUCAR: 67 have, 68 committed) out of the pool entirely.
    pool_rank = {s: (have.get(s, 0) - in_run.get(s, 0), first_seen.get(s, ""))
                 for t in pool for s in pool[t]}
    for t in pool:
        pool[t].sort(key=lambda s: pool_rank[s], reverse=True)

    # Remaining stock = declared HAVE minus what this run already ships. A substitute
    # with no headroom is not a substitute, however well it ranks.
    remaining = {s: have.get(s, 0) - in_run.get(s, 0) for s in set(have) | set(in_run)}
    rows = []
    for r in repeats:
        cust = r["customer"]
        allsk = [s for t in pool for s in pool[t]]
        ever = _ever_received(con, cust, allsk)
        used = set(r["box"])
        already = done_skus.get(r["order"], set())
        for s in r["repeats"]:
            t = typ(s, crackers)
            if r["order"] in done_orders and s in already:
                rows.append({"order": r["order"], "sku_to_swap": s, "proposed_swap": "",
                             "type": t, "flag": "SKIP - already swapped today",
                             "note": "audit log is a floor, not the authority"})
                continue
            # walk candidates in rank order and RECORD why each was rejected, so an
            # UNFILLABLE row says what was tried instead of just failing silently
            tried, cand = [], None
            for cd in pool.get(t, []):
                if cd in ever:
                    tried.append(f"{cd}:customer had it")
                elif cd in used:
                    tried.append(f"{cd}:already in this box")
                elif remaining.get(cd, 0) <= RESERVE_FLOOR:
                    tried.append(f"{cd}:at the {RESERVE_FLOOR}-unit floor"
                                 f" ({have.get(cd, 0)} have,"
                                 f" {in_run.get(cd, 0)} committed)")
                else:
                    cand = cd
                    break
            if cand:
                used.add(cand)
                remaining[cand] = remaining.get(cand, 0) - 1
            rows.append({"order": r["order"], "sku_to_swap": s,
                         "proposed_swap": cand or "",
                         "type": t,
                         "flag": "" if cand else "UNFILLABLE - " + "; ".join(tried[:6]),
                         "note": ""})
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
