"""remove_line_items.py — canonical cohort line-item removal (constraints SSOT: REMOVE_ITEMS_RULES.md).

Remove a SKU's line item(s) from every matching order on a _SHIP_ cohort.

    remove_line_items.py SHIP_TAG --sku SKU [--only-if-tagged TAG] [--allow-paid] [--apply]

Dry-run by default. Execution delegates to the EXISTING GraphQL order-edit machinery in
AppyHourMCP/tools/order_edit.py (begin/setQuantity/commit + its guards) — zero new edit code.
Paid lines REFUSED unless --allow-paid (and actual-paid printed either way; refunds are
refund_batch.py's job). Targeting via utils.active_line_items only. JSONL delta log appended
to _outputs/logs/remove_line_items.jsonl. Verify phase re-fetches per-order REST and proves
the read path with a control order (zero-is-a-claim).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

def _utf8_stdout() -> None:
    # cp1252 stdout kills a live-write script mid-batch — wrap before any output.
    # Called from main() only, so importing this module (e.g. under pytest) never
    # touches pytest's captured stdout.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


MCP_DIR = r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP"
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

DELTA_LOG = r"C:\Users\Work\Claude Projects\_outputs\logs\remove_line_items.jsonl"


# ---------------------------------------------------------------- pure logic (offline-testable)

def plan_targets(orders: list[dict], ship_tag: str, sku: str,
                 only_if_tagged: str = "") -> list[dict]:
    """Select target orders. Targeting via active_line_items ONLY (rule 3)."""
    from utils import active_line_items  # deferred so tests can stub sys.modules["utils"]
    out = []
    for o in orders:
        if o.get("cancelled_at"):
            continue
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if ship_tag not in tags:
            continue
        if only_if_tagged and only_if_tagged not in tags:
            continue
        qty = sum(
            li.get("fulfillable_quantity", 0) or 0
            for li in active_line_items(o)
            if (li.get("sku") or "") == sku and (li.get("fulfillable_quantity", 0) or 0) > 0
        )
        if qty > 0:
            email = (o.get("customer") or {}).get("email") or o.get("email") or ""
            out.append({"id": o["id"], "name": o.get("name", ""), "email": email,
                        "qty": qty, "tags": tags})
    out.sort(key=lambda t: t["name"])
    return out


def cohort_skus(orders: list[dict], ship_tag: str) -> set[str]:
    """Union of live SKUs across the cohort — same extraction as plan_targets (rule 3)."""
    from utils import active_line_items  # deferred so tests can stub sys.modules["utils"]
    present: set[str] = set()
    for o in orders:
        if o.get("cancelled_at"):
            continue
        if ship_tag not in [t.strip() for t in (o.get("tags") or "").split(",")]:
            continue
        present |= {(li.get("sku") or "") for li in active_line_items(o)}
    present.discard("")
    return present


def near_miss_candidates(sku: str, present: set[str]) -> list[str]:
    """Prefix-overlap candidates for a SKU that matched nothing (PR-CJAM -> PR-CJAM-GEN)."""
    return sorted(p for p in present if p.startswith(sku) or sku.startswith(p))[:5]


def absent_sku_refusal(sku: str, present: set[str], cohort_n: int, ship_tag: str) -> str | None:
    """🔴 Join-zero guard (2026-08-08 live smoke test). `--sku PR-CJAM` on a cohort whose real
    SKU is PR-CJAM-GEN matched ZERO orders and reported "nothing to do", exit 0 — a wrong SKU
    reading as an empty result. Matching is EXACT: prove the SKU exists on the cohort at all
    before reporting emptiness. Returns a refusal message, or None when the SKU is present."""
    if sku in present:
        return None
    cands = near_miss_candidates(sku, present)
    return (f"🔴 REFUSED: SKU {sku} appears on ZERO of {cohort_n} orders on {ship_tag} — "
            f"that is a wrong/typo'd SKU, not an empty result.\n"
            f"  did you mean: {cands or '(no similar SKU in cohort)'}\n"
            f"  Re-run with --allow-absent-sku only if you truly expect it to be absent.")


def paid_gate(targets: list[dict], paid_by_order: dict[str, list[float]],
              allow_paid: bool) -> tuple[list[dict], list[dict], bool]:
    """Split targets into (go, paid_rows); refuse=True when paid rows exist without --allow-paid.

    paid_by_order: order name -> list of actual-paid amounts for the SKU (from
    order_edit._paid_skus_on_order). Rule 2: paid removal is money-adjacent.
    """
    paid_rows = []
    for t in targets:
        amts = paid_by_order.get(t["name"]) or []
        if amts:
            paid_rows.append({**t, "paid": amts, "paid_total": round(sum(amts), 2)})
    if paid_rows and not allow_paid:
        return [], paid_rows, True
    return targets, paid_rows, False


def verify_diff(plan: list[dict], observed: dict[str, int | None],
                control_ok: bool) -> list[str]:
    """Compare planned removals vs re-fetched fulfillable counts. Returns problem strings."""
    problems = []
    if not control_ok:
        problems.append("CONTROL order read-back returned no fulfillable lines — "
                        "fetch path unproven, all zeros INCONCLUSIVE")
    for t in plan:
        got = observed.get(t["name"])
        if got is None:
            problems.append(f"{t['name']}: read-back inconclusive")
        elif got != 0:
            problems.append(f"{t['name']}: SKU still fulfillable qty={got} (expected 0)")
    return problems


# ---------------------------------------------------------------- live plumbing

def _delta(row: dict) -> None:
    os.makedirs(os.path.dirname(DELTA_LOG), exist_ok=True)
    with open(DELTA_LOG, "a", encoding="utf-8") as f:  # append-only, never truncate (rule 4)
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **row}) + "\n")


def _remove_sku_on_order(base, headers, order: dict, sku: str, note: str) -> dict:
    """Zero every live line of `sku` on one order via order_edit's begin/setQty/verify/commit.

    Mirrors order_edit._swap_order_skus guards (snapshot + unexpected-change abort) minus the
    add-variant leg; audits to the shared swap_audit.jsonl via order_edit._audit.
    """
    from utils import shopify_graphql
    from tools.order_edit import _audit

    gid = f"gid://shopify/Order/{order['id']}"
    b = shopify_graphql(base, headers, """
        mutation($id: ID!) {
          orderEditBegin(id: $id) {
            calculatedOrder { id lineItems(first: 100) { edges { node { id quantity sku } } } }
            userErrors { field message }
          }
        }""", {"id": gid})
    calc = b["orderEditBegin"]["calculatedOrder"]
    if not calc:
        raise RuntimeError(f"beginEdit failed: {b['orderEditBegin']['userErrors']}")
    calc_id = calc["id"]

    snapshot: dict[str, tuple[str, int]] = {}
    targets: list[tuple[str, int]] = []
    for e in calc["lineItems"]["edges"]:
        n = e["node"]
        if n.get("quantity", 0) > 0:
            snapshot[n["id"]] = (n.get("sku") or "", n["quantity"])
            if (n.get("sku") or "") == sku:
                targets.append((n["id"], n["quantity"]))
    if not targets:
        raise RuntimeError(f"no live {sku} line in calculated order (already removed?)")

    removed_qty = 0
    touched: set[str] = set()
    for li_id, qty in targets:
        r = shopify_graphql(base, headers, """
            mutation($calcId: ID!, $li: ID!, $q: Int!) {
              orderEditSetQuantity(id: $calcId, lineItemId: $li, quantity: $q, restock: true) {
                calculatedOrder { id } userErrors { field message }
              }
            }""", {"calcId": calc_id, "li": li_id, "q": 0})
        if r["orderEditSetQuantity"]["userErrors"]:
            raise RuntimeError(f"setQuantity errors: {r['orderEditSetQuantity']['userErrors']}")
        touched.add(li_id)
        removed_qty += qty

    # Pre-commit verification: no NON-target line changed (order_edit pattern).
    v = shopify_graphql(base, headers, """
        query($id: ID!) { node(id: $id) { ... on CalculatedOrder {
          lineItems(first: 100) { edges { node { id quantity sku } } } } } }""", {"id": calc_id})
    post = {e["node"]["id"]: e["node"].get("quantity", 0)
            for e in v["node"]["lineItems"]["edges"]}
    unexpected = [f"{s}: {q}->{post[li]}" for li, (s, q) in snapshot.items()
                  if li not in touched and li in post and post[li] != q]
    if unexpected:
        _audit({"order_gid": gid, "sku": sku, "result": f"ABORT:unexpected:{unexpected}"})
        raise RuntimeError(f"ABORT: unexpected item changes: {unexpected}")

    c = shopify_graphql(base, headers, """
        mutation($id: ID!, $note: String!) {
          orderEditCommit(id: $id, notifyCustomer: false, staffNote: $note) {
            order { id } userErrors { field message }
          }
        }""", {"id": calc_id, "note": note})
    if c["orderEditCommit"]["userErrors"]:
        _audit({"order_gid": gid, "sku": sku, "result": f"FAIL:commit:{c['orderEditCommit']['userErrors']}"})
        raise RuntimeError(f"commitEdit failed: {c['orderEditCommit']['userErrors']}")

    _audit({"order_gid": gid, "sku": sku, "removed_qty": removed_qty,
            "result": "OK:remove_line_items"})
    return {"order": order.get("name", ""), "removed_qty": removed_qty}


def _read_back_fulfillable(base, headers, order_name: str, sku: str | None) -> int | None:
    """Per-order REST read-back (GraphQL aggregate is eventually consistent post-edit).

    sku=None -> total fulfillable across ALL lines (control-order probe)."""
    import urllib.request
    num = order_name.lstrip("#")
    try:
        req = urllib.request.Request(
            f"{base}/orders.json?name={num}&status=any",
            headers={"X-Shopify-Access-Token": headers["X-Shopify-Access-Token"]})
        data = json.load(urllib.request.urlopen(req))
    except Exception as e:  # noqa: BLE001 — inconclusive, surfaced by verify_diff
        print(f"    read-back error {order_name}: {e}")
        return None
    for o in data.get("orders", []):
        if o.get("name") != f"#{num}":
            continue
        return sum(li.get("fulfillable_quantity", 0) or 0 for li in o.get("line_items", [])
                   if sku is None or li.get("sku") == sku)
    return None


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ship_tag")
    ap.add_argument("--sku", required=True)
    ap.add_argument("--only-if-tagged", default="")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--allow-absent-sku", action="store_true",
                    help="permit a --sku that appears on ZERO cohort orders (default: refuse)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    from utils import get_shopify_auth, shopify_paginate
    from tools.order_edit import _paid_skus_on_order

    base, headers = get_shopify_auth()
    orders = shopify_paginate(
        f"{base}/orders.json", headers,
        params={"status": "open", "fulfillment_status": "unfulfilled", "limit": 250,
                "fields": "id,name,tags,line_items,refunds,customer,email,cancelled_at"},
        resource="orders-live" if args.apply else "")

    if not args.allow_absent_sku:
        present = cohort_skus(orders, args.ship_tag)
        refusal = absent_sku_refusal(args.sku, present, len(orders), args.ship_tag)
        if refusal:
            print(refusal)
            return 2

    targets = plan_targets(orders, args.ship_tag, args.sku, args.only_if_tagged)
    if not targets:
        print(f"no orders on {args.ship_tag} with live {args.sku} — nothing to do")
        return 0

    # Paid detection via the existing order_edit detector (actual-paid OR catalog > 0).
    paid_by_order: dict[str, list[float]] = {}
    for t in targets:
        hits = _paid_skus_on_order(base, headers, f"gid://shopify/Order/{t['id']}", {args.sku})
        if hits.get(args.sku):
            paid_by_order[t["name"]] = hits[args.sku]
        time.sleep(0.05)

    go, paid_rows, refuse = paid_gate(targets, paid_by_order, args.allow_paid)

    print(f"\n{args.ship_tag} — remove {args.sku}"
          + (f" (also tagged {args.only_if_tagged})" if args.only_if_tagged else ""))
    print(f"{'order':<10} {'qty':>3} {'paid $':>8}  email")
    for t in targets:
        amts = paid_by_order.get(t["name"]) or []
        print(f"{t['name']:<10} {t['qty']:>3} {sum(amts):>8.2f}  {t['email']}")
    print(f"\n{len(targets)} orders, {sum(t['qty'] for t in targets)} units; "
          f"{len(paid_rows)} PAID (${sum(r['paid_total'] for r in paid_rows):.2f})")

    if refuse:
        print("\n🔴 REFUSED: paid line(s) present and --allow-paid not set. "
              "Paid removal is money-adjacent (wk0720 CH-IPRW). If Kurt OKs it, re-run with "
              "--allow-paid and pair with scripts/refund_batch.py for the amounts above.")
        return 2
    if paid_rows and args.allow_paid:
        print("⚠️  --allow-paid: the amounts above are refunds OWED — run refund_batch.py same session.")

    if not args.apply:
        print("\nDRY-RUN (default). Re-run with --apply to execute.")
        return 0

    note = f"{args.sku} removed via remove_line_items.py ({args.ship_tag})"
    by_id = {o["id"]: o for o in orders}
    results, failures = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_remove_sku_on_order, base, headers, by_id[t["id"]],
                            args.sku, note): t for t in go}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                _delta({"action": "remove", "ship_tag": args.ship_tag, "sku": args.sku,
                        "order": t["name"], "email": t["email"], "qty": r["removed_qty"],
                        "paid": paid_by_order.get(t["name"]) or []})
                print(f"  {t['name']}: removed qty={r['removed_qty']} ✅")
            except Exception as e:  # noqa: BLE001 — per-order failure, batch continues
                failures.append({"order": t["name"], "error": str(e)})
                _delta({"action": "remove-FAILED", "ship_tag": args.ship_tag,
                        "sku": args.sku, "order": t["name"], "error": str(e)})
                print(f"  {t['name']}: FAILED — {e}")

    # ---- verify phase (rule 5): control probe + per-order read-back
    time.sleep(1)
    control = next((o for o in orders
                    if args.ship_tag in [x.strip() for x in (o.get("tags") or "").split(",")]
                    and o["id"] not in {t["id"] for t in go}), None)
    control_ok = bool(control and (_read_back_fulfillable(base, headers,
                                                          control.get("name", ""), None) or 0) > 0)
    done_names = {r["order"] for r in results}
    observed = {t["name"]: _read_back_fulfillable(base, headers, t["name"], args.sku)
                for t in go if t["name"] in done_names}
    problems = verify_diff([t for t in go if t["name"] in done_names], observed, control_ok)
    problems += [f"{f['order']}: apply FAILED — {f['error']}" for f in failures]

    if problems:
        print("\n❌ VERIFY problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"\n✅ verified: {len(results)} orders, {args.sku} fulfillable=0 on each "
          f"(control {control.get('name') if control else '?'} read-back nonzero). "
          f"Delta log: {DELTA_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
