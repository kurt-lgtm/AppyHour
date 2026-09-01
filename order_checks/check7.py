"""Check 7: repeat items vs the customer's previous orders, and the swap list.

  python -m order_checks.check7 --tag RMFG_20260828 --ship _SHIP_2026-08-31 \
      --sheet x.xlsx --have "...\\Orders RMFG_<date>.csv"

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

from . import sheet as sheetmod
from .checks import write_blocked
from .fetch_gql import fetch_by_name

# The compact store answers every history question these checks ask, at 1/8th the size,
# and carries the customer map + the guardrail reads -- so recharge_id/customized no
# longer come from two other modules keyed on two different ids.
from .history_compact import DB, customized, ever_received, previous_orders, sku_first_seen

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
# Barred as SUBSTITUTES regardless of how well they rank or how deep the stock is --
# availability is not permission. Kurt 2026-08-28, each stated directly:
#   AC-RMC    "I have 600, but don't use it"
#   MT-IBRES  newest-first had put it in 164 rows
#   MT-BSS    "don't use MT-BSS anymore"
#   CH-MAFT   "we don't give them MAFT" -- also ASSIGNMENT_EXCLUDE in AppyHour/CLAUDE.md
#   AC-RBOL   "that's also something we can't give"
#   AC-BLUCAR is barred EXPLICITLY, not as a side effect. It used to inherit the bar from
#     DRAW_DOWN; emptying DRAW_DOWN on 2026-09-01 would have silently made it eligible as
#     a substitute again. Kurt removed it as a swap TARGET (stop swapping OUT of it), which
#     is not the same as clearing it to be swapped IN -- availability is not permission.
#     31 on hand against a RESERVE_FLOOR of 20 leaves 11 usable anyway.
NO_SUBSTITUTE = {"AC-RMC", "MT-IBRES", "MT-BSS", "CH-MAFT", "AC-RBOL", "AC-BLUCAR"}
# Never allocate a substitute below this many units remaining. Kurt 2026-08-28:
# "don't zero out blucar ... get it to 20 have left" -- a swap plan that drains a SKU
# to nothing leaves nothing for next week's cut or a short.
RESERVE_FLOOR = 20
# SKUs that must be DRAWN DOWN to the floor rather than merely capped -- the run
# already commits more than HAVE, so units have to come OUT of boxes. Kurt 2026-08-28:
# "KEEP BLUCAR TO 20 HAVE" -- AC-BLUCAR is 67 have against 68 committed, so 21 units
# must be swapped out to leave 20 on the shelf.
# EMPTY. Kurt 2026-09-01: "i'm removing blucar from the swap target" -- AC-BLUCAR is no
# longer drawn down. The 08-28 entry was scoped to that run's 67-have/68-committed
# squeeze; this week's HAVE says 31, so the squeeze is gone. Same dated-directive-
# outliving-its-run class as the AC-KETT HAVE_OVERRIDE.
DRAW_DOWN = {}
# Cap total USAGE of a SKU this run; the excess is swapped out. Kurt 2026-08-28:
# "i only want to use up about 400 sot today" -- CH-SOT is 501 on the sheet, so 101
# units come out. Distinct from DRAW_DOWN, which targets units LEFT rather than used.
# EMPTY on purpose. A usage cap is NOT permission to swap the excess out: Kurt
# 2026-08-28 asked for ~400 CH-SOT, then on being shown the 101-row swap list said
# "if you mean to swap them to get to 400, no." A cap is a planning target; swapping
# customers' items to hit it is a different and unwanted action.
USAGE_CAP = {}
# Forced one-for-one replacements, regardless of ranking. "change the RBOL TO FCEVOO".
FORCED_SWAP = {"AC-RBOL": "AC-FCEVOO"}
# 🔴 A SKU being DRAWN DOWN can never be a substitute -- the repeat pass would add back
# exactly what the draw-down removes. AC-BLUCAR is the case: 67 have, 0 left.
# USAGE_CAP is deliberately NOT included: a cap is a target for the draw-down pass, not
# a ban, and Kurt 2026-08-28 accepted the repeat pass pushing CH-SOT past it ("that's
# fine on the ch-sot") -- the draw-down pass reconciles it afterwards.
NO_SUBSTITUTE |= set(DRAW_DOWN) | set(FORCED_SWAP)
# Declared HAVE inventory -- the cut order's own corrected_inventory_path, NOT MCP
# get_calculated_inventory (which is wrong and must never be quoted as HAVE).
# 🔴 NO baked-in path. The HAVE file is a WEEKLY ingest artifact (wk0831's was
# "Orders RMFG_20260831 - Sheet154.csv": a SKU column + a "RMFG Have <date>" qty
# column); a dated literal here silently caps the NEXT week's swaps against LAST
# week's count. Pass it per run via --have; a missing path fails loud, never falls
# back (silent-stale class -- the 6/23 cut-order burn).
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




# Corrections applied ON TOP of the declared HAVE file, when Kurt states a number that
# the export does not carry. Each is his, never inferred.
# 🔴 EMPTY between runs. An override is a correction to ONE export, and it silently
# outlives it: "AC-KETT: 21" (Kurt 2026-08-28, against the RMFG_20260831 export saying
# 19) was still being applied to the RMFG_20260901 HAVE four days later, where it
# overwrites whatever this week's count actually says. Same silent-stale class the
# baked-in HAVE path was removed for. Add one only for the run in front of you, dated,
# and clear it when that run ships.
HAVE_OVERRIDE = {}


def load_have(path=None):
    """SKU -> on-hand qty from the declared HAVE file (.csv or .xlsx).

    RED FLAG: this is the cut order's corrected_inventory_path. NEVER substitute MCP
    get_calculated_inventory -- it is wrong and must not be quoted as HAVE. The file is
    a point-in-time count, so a swap proposed against it is only as fresh as the count:
    state the file date in any output built from it.
    """
    if not path:
        sys.exit("check7: no HAVE file given -- pass --have PATH (this week's declared "
                 "HAVE export, .csv or .xlsx). There is no fallback: a baked-in dated "
                 "path silently caps swaps against LAST week's inventory.")
    if not os.path.exists(path):
        sys.exit(f"check7: HAVE file not found: {path} -- --have must point at this "
                 "week's declared HAVE export (refusing to run with an empty HAVE: "
                 "every substitute would read 0 on hand).")
    import datetime
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    print(f"  HAVE: {path} (modified {mt:%Y-%m-%d %H:%M})"
          + (f"   overrides: {sorted(HAVE_OVERRIDE)}" if HAVE_OVERRIDE else ""))
    if path.lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = [tuple(r) for r in csv.reader(fh)]
    else:
        import openpyxl
        ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    hdr = [str(x or "").strip().lower() for x in rows[0]]
    i_sku = next((i for i, h in enumerate(hdr) if h == "sku"), None)
    # the column is "Qty" in the corrected-inventory workbook and "RMFG Have <date>"
    # in the cut-order CSV -- accept either rather than pinning one label
    i_qty = next((i for i, h in enumerate(hdr) if h == "qty" or "have" in h), None)
    body = rows[1:]
    if i_sku is None or i_qty is None:
        # 🔴 HEADERLESS two-column form: `MON_cut_order_v2_<date>.xlsx - Sheet1.csv`
        # starts straight at `CH-6COM,126`. Returning {} here was a SILENT ZERO -- the
        # file printed fine and every substitute read 0 on hand, which reads as
        # "nothing available" rather than "the parse failed".
        first = [str(x or "").strip() for x in rows[0]]
        if len(first) >= 2 and first[0].startswith(CHILD + ("PK-", "MR-", "BL-", "AHB-")) and first[1].replace(".", "").isdigit():
            i_sku, i_qty, body = 0, 1, rows
        else:
            sys.exit(f"check7: cannot find SKU/qty columns in {path}. Header was {hdr!r}. "
                     "Refusing to continue -- an empty HAVE reads every substitute as 0 "
                     "on hand and silently proposes nothing.")
    have = {}
    for r in body:
        if len(r) <= max(i_sku, i_qty):
            continue
        s = str(r[i_sku] or "").strip()
        try:
            q = int(float(r[i_qty]))
        except (TypeError, ValueError):
            continue
        if s:
            have[s] = q
    if not have:
        sys.exit(f"check7: HAVE parsed to ZERO skus from {path}. Refusing to continue -- "
                 "an empty HAVE is not 'nothing on hand', it is a failed parse, and it "
                 "would silently make every substitute look exhausted.")
    have.update(HAVE_OVERRIDE)
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


def sheet_demand(sheet):
    """SKU -> units the SHEET commits. The pick list is the right denominator for
    inventory, not the Shopify free-child count: it includes PAID children, which
    consume stock just the same, plus gifts and reships. It reads higher than Shopify
    on 64 SKUs in RMFG_20260828 -- CH-BRZ 229 vs 180, CH-MAFT 322 vs 278, MT-BSS 35 vs
    33 against 32 on hand. (Kurt 2026-08-28: "did you check against the vf sheet
    though?")
    """
    tot = collections.Counter()
    for row in sheet.values():
        for k, v in (row.get("columns_sku") or {}).items():
            tot[k] += v
    return tot


def run(orders, con, verbose=True, sheet=None, have_path=None):
    """-> (repeats, saturation, per_sku, swaps)."""
    first_seen = sku_first_seen(con)
    # candidate pool = free child SKUs circulating in this run
    in_run = collections.Counter()
    for o in orders.values():
        for li in _live(o):
            s = (li["sku"] or "").strip()
            if s.startswith(CHILD) and not _paid(li):
                in_run[s] += li["currentQuantity"]
    # but STOCK is drawn against the sheet's demand, which is the larger number
    committed = sheet_demand(sheet) if sheet else in_run

    repeats, skipped = [], collections.Counter()
    for oid, o in sorted(orders.items()):
        tags = o.get("tags") or []
        if RECUR_TAG not in tags:
            skipped["not a recurring subscription order"] += 1
            continue
        if any("reship" in t.lower() for t in tags):
            skipped["reship"] += 1
            continue
        blocked = write_blocked(o)      # PR box / Gift Redemption -> never written
        if blocked:
            skipped[blocked] += 1
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
        # 🔴 SCOPED TO THIS ORDER, not ever. Kurt 2026-09-01: "customize gate is for the
        # specific order" -- the question is whether the customer built THIS box, so the
        # window starts at their previous order. An ever-customized test would protect
        # every customer who ever touched the portal and empty the list; #178696 and
        # #178706 both read customized=True lifetime and False since their last order.
        was, why = customized(con, cust, since_iso=prev[0][1] if prev else None)
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
                        *swapped_today(), have=load_have(have_path),
                        crackers=build_cracker_set(orders), committed=committed)
    if verbose:
        print(f"  eligible orders: {n_scope}   flagged: {len(repeats)}")
        for k, v in skipped.most_common():
            print(f"    excluded {v:>5}  {k}")
    return repeats, saturation, per_sku, clears, swaps, skipped


def build_swaps(repeats, orders, con, in_run, first_seen,
                done_orders=frozenset(), done_skus=None, have=None,
                crackers=frozenset(), committed=None):
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
    committed = committed if committed is not None else in_run
    pool_rank = {s: (have.get(s, 0) - committed.get(s, 0), first_seen.get(s, ""))
                 for t in pool for s in pool[t]}
    for t in pool:
        pool[t].sort(key=lambda s: pool_rank[s], reverse=True)

    # Remaining stock = declared HAVE minus what this run already ships. A substitute
    # with no headroom is not a substitute, however well it ranks.
    remaining = {s: have.get(s, 0) - committed.get(s, 0)
                 for s in set(have) | set(committed)}
    rows = []
    for r in repeats:
        cust = r["customer"]
        allsk = [s for t in pool for s in pool[t]]
        ever = ever_received(con, cust, allsk)
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
                                 f" {committed.get(cd, 0)} committed)")
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check7")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--ship")
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--have", required=True, metavar="PATH",
                    help="this week's declared HAVE export (.csv/.xlsx) -- no fallback")
    ap.add_argument("--cache")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)

    sheet = sheetmod.load_sheet(a.sheet)
    orders = fetch_by_name(list(sheet), cache=a.cache)
    sheetmod.resolve_columns(sheet, orders)
    con = sqlite3.connect(DB)
    repeats, sat, per_sku, clears, swaps, _ = run(orders, con, sheet=sheet,
                                                  have_path=a.have)

    tier3 = [r for r in repeats if r["n_repeats"] >= 3]
    tierh = [r for r in repeats if r["box_size"] and r["n_repeats"] / r["box_size"] >= 0.5]
    print(f"\n  >=3 repeats            {len(tier3)}")
    print(f"  half the box or more   {len(tierh)}")
    print("\n  saturation (share of eligible orders carrying the SKU):")
    for s, pct in list(sat.items())[:10]:
        print(f"    {s:<12}{pct:>6}%")
    print("\n  per-SKU: how many orders repeat it / how many clear if ONLY it is swapped")
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
