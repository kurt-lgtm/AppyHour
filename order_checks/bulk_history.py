"""Check 7 substrate: deep customer order history via Shopify BULK operation.

  python -m order_checks.bulk_history start
  python -m order_checks.bulk_history poll     (repeat until it downloads)
  python -m order_checks.bulk_history status

Port of Dan's bulk_hist_deep.py (RUN_2026-08-25). Read-only.

🔴 ONE bulk operation at a time per shop. `start` refuses if one is already running
rather than cancelling it -- another session's job is not ours to kill. Dan chains
lanes first, then deep history, for the same reason.

Check 7's "must not have received it in ANY past box" constraint cannot be evaluated
without this: it needs order contents back to 2023, which is far past what a paginated
REST pull can reach in reasonable time.
"""
from __future__ import annotations
import json
import os
import sys

import requests

API = "2025-01"
SINCE = "2023-01-01"
OUT_DEFAULT = r"C:\Users\Work\Claude Projects\_outputs\cache"

BULK = """
{
  orders(query: "created_at:>%s") {
    edges { node {
      id name createdAt tags
      customer { id }
      lineItems { edges { node { sku currentQuantity
        originalUnitPriceSet { shopMoney { amount } }
        discountedUnitPriceSet { shopMoney { amount } } } } }
    } }
  }
}""" % SINCE


def _auth():
    for p in (r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP",
              r"C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator"):
        if p not in sys.path:
            sys.path.insert(0, p)
    from utils import get_shopify_auth
    base, hdr = get_shopify_auth()
    dom = base.split("//", 1)[1].split("/", 1)[0]
    return f"https://{dom}/admin/api/{API}/graphql.json", hdr


def _gql(q, v=None):
    url, hdr = _auth()
    r = requests.post(url, headers={**hdr, "Content-Type": "application/json"},
                      json={"query": q, "variables": v or {}}, timeout=90)
    r.raise_for_status()
    return r.json()


def _current():
    d = _gql("{currentBulkOperation(type: QUERY){id status errorCode objectCount url createdAt}}")
    return (d.get("data") or {}).get("currentBulkOperation")


def status():
    op = _current()
    print(json.dumps({k: v for k, v in (op or {}).items() if k != "url"}, indent=1)
          if op else "no bulk operation on record")
    return op


def start():
    op = _current()
    if op and op.get("status") in ("CREATED", "RUNNING"):
        # Never cancel: another session's bulk op is not ours to kill.
        print(f"REFUSING - a bulk operation is already {op['status']} "
              f"({op['id']}, objects so far {op.get('objectCount')}). Poll it instead.")
        return
    m = """mutation($q:String!){bulkOperationRunQuery(query:$q){
             bulkOperation{id status} userErrors{field message}}}"""
    print(json.dumps(_gql(m, {"q": BULK}), indent=1))


def poll(out_dir=OUT_DEFAULT):
    op = _current()
    if not op:
        print("no bulk operation on record")
        return
    print(json.dumps({k: v for k, v in op.items() if k != "url"}, indent=1))
    if op["status"] == "COMPLETED" and op.get("url"):
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "hist_deep_raw.jsonl")
        with requests.get(op["url"], stream=True, timeout=900) as r, open(p, "wb") as f:
            for c in r.iter_content(1 << 20):
                f.write(c)
        print("downloaded ->", p, os.path.getsize(p))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"start": start, "poll": poll, "status": status}[cmd]()
