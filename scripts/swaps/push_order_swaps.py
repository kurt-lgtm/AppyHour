"""push_order_swaps.py — apply a PER-ORDER swap list to Shopify.

    python scripts/swaps/push_order_swaps.py swaps.csv [--apply]

csv columns: Order ID,SKU to Swap,Proposed Swap   (order_checks.run_all's output shape)

WHY THIS EXISTS, and when NOT to use it
`shorts_pass.py` is the canonical weekly tool and stays canonical for SKU-level work: its
pairs.csv is `old_sku,new_sku,count` and it CHOOSES the orders itself. That is the wrong
shape for an order-checks list, where the same old SKU takes a DIFFERENT substitute per
order because the substitute rule is per customer history (RMFG_20260901: AC-MISS ->
AC-BRJA on #178696 but AC-LFOLIVE on #178706). Expressing that as two pairs rows would
let either substitute land on either order and hand a customer an item they already had.
`appyhour_swap_order_skus` is cohort-shaped for the same reason.

So: use shorts_pass for "we are short N units of X". Use this ONLY for a list that names
the order AND its specific substitute.

🔴 Executes through `order_edit._swap_order_skus`, the same module shorts_pass applies
through — the wk0810 "never hand-roll" burn was a loop around the lower-level
`execute_swap`, which returns success:False without raising and produced 34 phantom
successes. The rule that came out of it is not "never call the module", it is:

  * SUCCESS IS NEVER CALL-COUNT. Every run re-fetches the orders afterwards and re-counts
    fulfillable quantities against the plan. Nonzero exit on any mismatch.
  * qty_limits={old: 1} on every leg. Without it EVERY matching line swaps: an order
    carrying the old SKU on two lines gets both converted to the one target, which
    overshot a declared 1-unit swap on #178510 (2x AC-PBLINI both became AC-BRJA).
  * rc_bundle_only=True and allow_paid NEVER passed. allow_paid disables the catalog-price
    half of the paid guard, which exists because a Recharge ONETIME collects money on the
    Recharge charge and pushes the Shopify line at $0 (#163709, a $9 salami swapped away).
  * write_blocked first: PR box never, reship/gift never. A gift order is edit-LOCKED in
    Shopify anyway -- the call fails, it is not a policy preference.

Dry-run is the default. --apply is required for writes.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_APPYHOUR = _HERE.parent.parent
for p in (_APPYHOUR, _APPYHOUR / "AppyHourMCP", _APPYHOUR / "AppyHourMCP" / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import order_edit  # noqa: E402

from order_checks.checks import write_blocked  # noqa: E402
from order_checks.fetch_gql import fetch_by_name  # noqa: E402

LOG_DIR = Path(r"C:\Users\Work\Claude Projects\_outputs\logs")


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = {c.lower().replace(" ", "_"): c for c in rd.fieldnames or []}
        oc = cols.get("order_id") or cols.get("order")
        a = cols.get("sku_to_swap") or cols.get("old_sku")
        b = cols.get("proposed_swap") or cols.get("new_sku")
        if not (oc and a and b):
            sys.exit(f"need Order ID/SKU to Swap/Proposed Swap columns; got {rd.fieldnames}")
        return [{"order": str(r[oc]).strip().lstrip("#"),
                 "old": r[a].strip(), "new": r[b].strip()}
                for r in rd if str(r[oc]).strip() and r[b].strip()]


def plan(rows, orders):
    """-> (ok_rows, refused). Precondition check per leg, before anything is written."""
    ok, refused = [], []
    for r in rows:
        o = orders.get(r["order"])
        if not o:
            refused.append({**r, "why": "order not found in Shopify"})
            continue
        blocked = write_blocked(o)
        if blocked:
            refused.append({**r, "why": blocked})
            continue
        live = [e["node"] for e in o["lineItems"]["edges"]
                if (e["node"].get("currentQuantity") or 0) > 0]
        by = {}
        for li in live:
            by.setdefault((li["sku"] or "").strip(), []).append(li)
        if r["old"] not in by:
            refused.append({**r, "why": f"{r['old']} not live on the order"})
            continue
        # 🔴 line PRICE, not the variant's catalog price -- but a >$0 line is refused here
        # regardless, and the deeper catalog-price guard still runs inside order_edit.
        paid = [li for li in by[r["old"]]
                if float((li.get("discountedUnitPriceSet") or {})
                         .get("shopMoney", {}).get("amount") or 0) > 0]
        if paid:
            refused.append({**r, "why": f"{r['old']} is a PAID line ({paid[0]})"})
            continue
        if r["new"] in by:
            refused.append({**r, "why": f"{r['new']} already on the order (would dupe)"})
            continue
        ok.append({**r, "order_gid": o["id"], "n_lines": len(by[r["old"]])})
    return ok, refused


def verify(rows, before, after):
    """Re-count live quantities per (order, sku) and diff against the plan."""
    def counts(orders, oid):
        c = {}
        for e in orders[oid]["lineItems"]["edges"]:
            n = e["node"]
            q = n.get("currentQuantity") or 0
            if q > 0:
                c[(n["sku"] or "").strip()] = c.get((n["sku"] or "").strip(), 0) + q
        return c

    bad = []
    for r in rows:
        oid = r["order"]
        b, a = counts(before, oid), counts(after, oid)
        want_old = b.get(r["old"], 0) - 1
        want_new = b.get(r["new"], 0) + 1
        got_old, got_new = a.get(r["old"], 0), a.get(r["new"], 0)
        if got_old != want_old or got_new != want_new:
            bad.append({**r, "old_expected": want_old, "old_actual": got_old,
                        "new_expected": want_new, "new_actual": got_new})
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(prog="push_order_swaps")
    ap.add_argument("csv")
    ap.add_argument("--apply", action="store_true", help="execute writes (default: dry-run)")
    ap.add_argument("--allow-no-rc-bundle", action="store_true",
                    help="drop the _rc_bundle fence for legs PROVEN free on BOTH price "
                         "signals. Needs Kurt's OK per run; refuses any line with a "
                         "nonzero paid OR catalog price.")
    a = ap.parse_args(argv)

    rows = load(a.csv)
    ids = sorted({r["order"] for r in rows})
    print(f"\n=== push_order_swaps: {len(rows)} legs across {len(ids)} orders ===")
    before = fetch_by_name(ids, verbose=False)
    ok, refused = plan(rows, before)

    for r in refused:
        print(f"  REFUSED  #{r['order']}  {r['old']} -> {r['new']}   {r['why']}")
    for r in ok:
        print(f"  PLAN     #{r['order']}  {r['old']} -> {r['new']}"
              + (f"   🔴 {r['n_lines']} lines carry {r['old']}, capping at 1" if r["n_lines"] > 1 else ""))
    if not ok:
        print("\n  nothing to do")
        return 1 if refused else 0
    if not a.apply:
        print(f"\n  DRY RUN. {len(ok)} legs would be written. Re-run with --apply.")
        return 0

    base, headers = order_edit_auth()
    gids = order_edit._lookup_variant_gids(base, headers, {r["new"] for r in ok})
    if a.allow_no_rc_bundle:
        # 🔴 Dropping the fence is only safe on a line that is free on BOTH signals.
        # paid==0 alone is NOT enough: a Recharge ONETIME collects on the Recharge charge
        # and pushes the Shopify line at $0, and only the variant's CATALOG price shows it
        # (#163709, a $9 salami swapped away). Proven per LINE here, not assumed per run.
        free = _free_on_both(base, headers, ok)
        for r in list(ok):
            if not free.get((r["order"], r["old"])):
                print(f"  REFUSED  #{r['order']} {r['old']}: fence drop needs paid==0 AND "
                      f"catalog==0; got {free.get((r['order'], r['old']), 'unknown')}")
                ok.remove(r)
        if not ok:
            print("\n  nothing left after the fence-drop check")
            return 1
    results = []
    for r in ok:
        try:
            sw = order_edit._swap_order_skus(
                base, headers, r["order_gid"], {r["old"]: r["new"]}, gids,
                rc_bundle_only=not a.allow_no_rc_bundle, qty_limits={r["old"]: 1})
            results.append({**r, "swapped": sw, "ok": bool(sw)})
        except Exception as e:                                   # noqa: BLE001
            results.append({**r, "error": str(e), "ok": False})
        print(f"  {'OK  ' if results[-1]['ok'] else 'FAIL'} #{r['order']} {r['old']} -> {r['new']}"
              + (f"  [{results[-1].get('error', '')}]" if not results[-1]["ok"] else ""))

    print("\n[verify] re-fetching and re-counting -- success is never call-count")
    time.sleep(3)
    after = fetch_by_name(ids, verbose=False)
    bad = verify(ok, before, after)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"push_order_swaps_{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
    with open(log, "w", encoding="utf8") as fh:
        for r in results:
            fh.write(json.dumps({**r, "verified": r not in bad}) + "\n")
    if bad:
        print(f"  🔴 {len(bad)} MISMATCH -- Shopify does not match the plan:")
        for b in bad:
            print(f"     #{b['order']} {b['old']} {b['old_actual']}/{b['old_expected']}"
                  f"  {b['new']} {b['new_actual']}/{b['new_expected']}")
        print(f"  log -> {log}")
        return 1
    print(f"  all {len(ok)} legs verified against Shopify")
    print(f"  log -> {log}")
    return 0


def _free_on_both(base, headers, rows):
    """-> {(order, sku): True} only where the LIVE line is $0 paid AND $0 catalog."""
    q = """query($id:ID!){order(id:$id){lineItems(first:100){nodes{
             sku currentQuantity
             discountedUnitPriceAfterAllDiscountsSet{shopMoney{amount}}
             variant{price}}}}}"""
    out = {}
    for gid in {r["order_gid"] for r in rows}:
        d = order_edit.shopify_graphql(base, headers, q, {"id": gid})
        oid = next(r["order"] for r in rows if r["order_gid"] == gid)
        for n in d["order"]["lineItems"]["nodes"]:
            if (n.get("currentQuantity") or 0) <= 0:
                continue
            sku = (n["sku"] or "").strip()
            paid = float(n["discountedUnitPriceAfterAllDiscountsSet"]["shopMoney"]["amount"] or 0)
            cat = float((n.get("variant") or {}).get("price") or 0)
            out[(oid, sku)] = (paid == 0 and cat == 0) or f"paid={paid} catalog={cat}"
    return out


def order_edit_auth():
    """order_edit expects the REST base + headers, not the GraphQL URL."""
    sys.path.insert(0, str(_APPYHOUR))
    from appyhour_lib.credentials import get_shopify_auth
    return get_shopify_auth()


if __name__ == "__main__":
    sys.exit(main())
