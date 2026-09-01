"""ONE command for a production run: every check, then the swap list, gated.

  python -m order_checks.run_all --tag RMFG_20260828 --ship _SHIP_2026-08-31 \
      --sheet "...\\AHB_WeeklyProductionQuery_08-31-26_vF.xlsx" \
      --have "...\\Orders RMFG_<date>.csv" --out <dir>

Order is deliberate and is the lesson of the wk0831 run:

  1. fetch          GraphQL, Dan's shape -- never REST, or the price/title semantics shift
  2. checks 1/2/3/5/6/8   the count model
  3. cracker + bare-CEX + Fixed_Route
  4. 🔴 BOTH guardrail halves BEFORE any swap list exists
  5. check 7 repeats -> swap list, minus protected customers
  6. validate every row against write_blocked
  7. print what a human must decide; write nothing

🔴 THIS TOOL NEVER WRITES. It emits CSVs. Sheet edits go through
`ShipRouting/scripts/vf_edit.py` (validated, ledgered, atomic) and Shopify edits follow the
sheet, never the other way round.
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import sqlite3
import sys

from . import sheet as sheetmod
from .categorize import categorize
from .check7 import run as check7_run
from .checks import bare_cex_check, cracker_check, fixed_route_check, validate_swap_list
from .dan_checks import load_rules
from .dan_checks import run as dan_run
from .fetch_gql import fetch_by_name
from .history_compact import DB
from .login_gate import protected as login_protected

DEFAULT_RULESET = os.path.expanduser(r"~\Downloads\ALLFULFILLMENTS_RuleSet_OrderMatching.xlsx")


def _rest_shape(node):
    """GraphQL node -> the dict shape the older per-order checks expect."""
    return {"tags": ",".join(node.get("tags") or []),
            "line_items": [{**e["node"], "sku": e["node"]["sku"],
                            "current_quantity": e["node"]["currentQuantity"],
                            "quantity": e["node"]["quantity"],
                            "price": (e["node"].get("originalUnitPriceSet") or {})
                            .get("shopMoney", {}).get("amount", "0"),
                            "total_discount": "0"}
                           for e in node["lineItems"]["edges"]],
            # 🔴 GraphQL gives customer.tags as a LIST, REST as a comma string. The
            # per-order checks expect the string; passing the list through raised
            # 'list has no attribute lower' inside fixed_route_check.
            "customer": {**(node.get("customer") or {}),
                         "tags": ", ".join((node.get("customer") or {}).get("tags") or [])
                         if isinstance((node.get("customer") or {}).get("tags"), list)
                         else ((node.get("customer") or {}).get("tags") or "")}}


def dump(path, rows, cols=None):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf8") as fh:
        w = csv.DictWriter(fh, cols or list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"    -> {os.path.basename(path)} ({len(rows)})")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="order_checks.run_all")
    ap.add_argument("--tag", required=True, help="production tag, e.g. RMFG_20260828 or 8_24")
    ap.add_argument("--ship", required=True)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--have", metavar="PATH",
                    help="this week's declared HAVE export (.csv/.xlsx) for check 7's "
                         "swap caps -- REQUIRED, no baked-in fallback (a dated literal "
                         "silently capped swaps against LAST week's count)")
    ap.add_argument("--no-swaps", action="store_true",
                    help="run the CHECK half only, for when this week's HAVE export does "
                         "not exist yet. The counts, slot checks and BOTH guardrail halves "
                         "need no inventory. Reaching for last week's HAVE instead is "
                         "exactly the failure --have exists to prevent.")
    ap.add_argument("--ruleset", default=DEFAULT_RULESET)
    ap.add_argument("--cache")
    ap.add_argument("--out", default=".")
    ap.add_argument("--max-per-order", type=int, default=2,
                    help="cap across the COMBINED list; passes stack (#176908 hit 3)")
    a = ap.parse_args(argv)
    if not a.have and not a.no_swaps:
        ap.error("--have is required (or --no-swaps to run the check half only)")

    os.makedirs(a.out, exist_ok=True)
    print(f"\n=== {a.tag} / {a.ship} ===")
    sheet = sheetmod.load_sheet(a.sheet)
    orders = fetch_by_name(list(sheet), cache=a.cache)
    cs, unmatched = sheetmod.resolve_columns(sheet, orders)
    print(f"  sheet {len(sheet)} rows · {len(cs)} columns resolved"
          + (f" · 🔴 UNMATCHED {unmatched}" if unmatched else ""))

    print("\n-- counts (checks 1/2/3/5/6/8) --")
    R = dan_run(orders, sheet, load_rules(a.ruleset), a.tag, a.ship)
    for k in ("scope", "c1_fail", "c1_unresolved", "c1_norule", "c1_noparent", "c1_ahbx",
              "c2", "c3", "c5", "c6", "c8", "reship_excluded", "xbl_excluded"):
        print(f"    {k:<18}{len(R.get(k, []))}")

    cats = categorize(R, orders)
    real = [r for r in cats if r["category"].startswith("F")]
    print(f"    {'F. REAL EXCEPTION':<18}{len(real)} of {len(cats)} categorised")
    dump(os.path.join(a.out, f"check1_{a.tag}.csv"), cats)

    print("\n-- slot checks --")
    rest = {k: _rest_shape(v) for k, v in orders.items()}
    for name, fn in (("cracker", cracker_check), ("bare CEX-EC", bare_cex_check),
                     ("Fixed_Route", fixed_route_check)):
        hits = [{"order": k, "issue": fn(o)} for k, o in rest.items() if fn(o)]
        print(f"    {name:<14}{len(hits)}")
        dump(os.path.join(a.out, f"{name.split()[0].lower()}_{a.tag}.csv"), hits)

    # 🔴 BOTH halves of the guardrail, BEFORE the swap list exists
    print("\n-- guardrail (login OR customize) --")
    con = sqlite3.connect(DB)
    prot = login_protected(orders, con)
    dump(os.path.join(a.out, f"login_protected_{a.tag}.csv"),
         [{"Order ID": k, "email": v[0], "login_at": v[1]} for k, v in prot.items()])

    if a.no_swaps:
        print("\n-- check 7 SKIPPED (--no-swaps): no HAVE export for this cohort yet --")
        print(f"\n  DECIDE: {len(real)} real count exceptions · swaps NOT computed")
        con.close()
        return 0

    print("\n-- check 7 repeats --")
    repeats, sat, per_sku, clears, swap_rows, _ = check7_run(orders, con, sheet=sheet,
                                                             have_path=a.have)
    print(f"    flagged {len(repeats)}   swap candidates {len(swap_rows)}")

    kept, per = [], collections.Counter()
    for r in swap_rows:
        oid = r["order"].lstrip("#")
        if not r.get("proposed_swap"):
            continue
        if oid in prot:                       # protected: logged in since last order
            continue
        if per[oid] >= a.max_per_order:
            continue
        per[oid] += 1
        kept.append({"Order ID": oid, "SKU to Swap": r["sku_to_swap"],
                     "Proposed Swap": r["proposed_swap"], "Flag": ""})
    print(f"    minus {len(prot)} protected, capped {a.max_per_order}/order -> {len(kept)}")

    blocked = validate_swap_list(kept, rest)
    if blocked:
        print(f"    🔴 {len(blocked)} rows target a blocked order - REMOVED")
        bad = {(b["Order ID"], b["SKU to Swap"]) for b in blocked}
        kept = [r for r in kept if (r["Order ID"], r["SKU to Swap"]) not in bad]
        dump(os.path.join(a.out, f"blocked_{a.tag}.csv"), blocked)
    dump(os.path.join(a.out, f"swaps_{a.tag}.csv"), kept)

    print(f"\n  DECIDE: {len(real)} real count exceptions · {len(kept)} swaps ready")
    print("  Nothing was written. Sheet edits go through ShipRouting/scripts/vf_edit.py;")
    print("  Shopify follows the sheet, never the reverse.")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
