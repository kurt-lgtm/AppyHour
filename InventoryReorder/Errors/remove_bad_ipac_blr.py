# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Remove wrong line items from 41 orders (curation _rc_bundle, no refund).

- 17 orders: remove 1x CH-IPAC + 1x AC-RPJ
- 24 orders: remove 1x CH-BLR + 1x AC-BLBALS

Usage:
    python remove_bad_ipac_blr.py             # dry-run
    python remove_bad_ipac_blr.py --commit    # apply
"""
from __future__ import annotations
import sys, time, json
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

BASE, HEADERS = get_shopify_auth()
GQL = f"{BASE}/graphql.json"
REST = BASE
COMMIT = "--commit" in sys.argv

# 17 orders: CH-IPAC + AC-RPJ
IPAC_ORDERS = [
    "139672","139676","140066","140174","140395","140451","140458",
    "141544","141592","141600","141644","141723","141770","141821",
    "141855","141857","141885",
]
# 24 orders: CH-BLR + AC-BLBALS
BLR_ORDERS = [
    "129523","140203","140495","140626","140651","140655","140821","140897",
    "140953","141080","141088","141232","141264","141270","141304","141362",
    "141484","141495","141547","141679","141702","141850","141898","141919",
]

JOBS = (
    [(o, ["CH-IPAC","AC-RPJ"]) for o in IPAC_ORDERS] +
    [(o, ["CH-BLR","AC-BLBALS"]) for o in BLR_ORDERS]
)


def gql(query: str, variables: dict | None = None) -> dict:
    r = requests.post(GQL, headers=HEADERS, json={"query": query, "variables": variables or {}}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(json.dumps(d["errors"], indent=2))
    return d["data"]


def find_order_gid(name: str) -> str | None:
    q = '''
    query($q: String!) {
      orders(first: 5, query: $q) {
        edges { node { id name tags } }
      }
    }
    '''
    d = gql(q, {"q": f"name:#{name}"})
    for e in d["orders"]["edges"]:
        n = e["node"]
        if n["name"].lstrip("#") == name:
            return n["id"]
    return None


def begin_edit(order_gid: str) -> tuple[str, list[dict]]:
    q = '''
    mutation($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder {
          id
          lineItems(first: 100) {
            edges { node { id sku quantity title customAttributes { key value } } }
          }
        }
        userErrors { field message }
      }
    }
    '''
    d = gql(q, {"id": order_gid})
    res = d["orderEditBegin"]
    if res["userErrors"]:
        raise RuntimeError(f"begin: {res['userErrors']}")
    calc = res["calculatedOrder"]
    items = [e["node"] for e in calc["lineItems"]["edges"]]
    return calc["id"], items


def set_qty(calc_id: str, li_id: str, qty: int) -> None:
    q = '''
    mutation($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity, restock: false) {
        userErrors { field message }
      }
    }
    '''
    d = gql(q, {"id": calc_id, "lineItemId": li_id, "quantity": qty})
    errs = d["orderEditSetQuantity"]["userErrors"]
    if errs:
        raise RuntimeError(f"setQty: {errs}")


def commit_edit(calc_id: str, note: str) -> None:
    q = '''
    mutation($id: ID!, $note: String!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: $note) {
        order { id name }
        userErrors { field message }
      }
    }
    '''
    d = gql(q, {"id": calc_id, "note": note})
    errs = d["orderEditCommit"]["userErrors"]
    if errs:
        raise RuntimeError(f"commit: {errs}")


def is_bundle(item: dict) -> bool:
    for a in item.get("customAttributes") or []:
        if a.get("key") == "_rc_bundle":
            return True
    return False


def process(order_name: str, skus_to_remove: list[str], dry: bool) -> dict:
    out = {"order": order_name, "removed": [], "skipped": [], "errors": []}
    gid = find_order_gid(order_name)
    if not gid:
        out["errors"].append("order not found"); return out
    calc_id, items = begin_edit(gid)

    plan = []  # (li_id, new_qty, sku, cur_qty, is_bundle)
    for sku in skus_to_remove:
        matches = [i for i in items if (i.get("sku") or "").strip() == sku and i["quantity"] > 0 and is_bundle(i)]
        if not matches:
            # fall back: any qty>0 (warn)
            any_match = [i for i in items if (i.get("sku") or "").strip() == sku and i["quantity"] > 0]
            if any_match:
                out["errors"].append(f"{sku} found but not _rc_bundle (skipped)")
            else:
                out["skipped"].append(f"{sku} not present")
            continue
        # remove 1x: pick first match, set qty = qty-1
        m = matches[0]
        plan.append((m["id"], m["quantity"] - 1, sku, m["quantity"]))

    if not plan:
        return out

    for li_id, new_qty, sku, cur in plan:
        action = f"{sku}: {cur}→{new_qty}"
        if dry:
            out["removed"].append(action + " (dry)")
        else:
            set_qty(calc_id, li_id, new_qty)
            out["removed"].append(action)
            time.sleep(0.2)

    if not dry and plan:
        commit_edit(calc_id, f"Remove bad bundle items: {', '.join(p[2] for p in plan)}")
    return out


def main() -> None:
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n=== Remove bad IPAC/BLR bundle items [{mode}] — {len(JOBS)} orders ===\n")
    ok = 0; fail = 0
    for name, skus in JOBS:
        try:
            r = process(name, skus, dry=not COMMIT)
            status = "OK" if r["removed"] and not r["errors"] else ("ERR" if r["errors"] else "NOOP")
            if status == "OK": ok += 1
            else: fail += 1
            print(f"  #{name} [{status}] removed={r['removed']} skipped={r['skipped']} errors={r['errors']}")
        except Exception as e:
            fail += 1
            print(f"  #{name} [EXC] {e}")
        time.sleep(0.4)
    print(f"\n=== {mode} done: ok={ok} fail/noop={fail} ===")


if __name__ == "__main__":
    main()
