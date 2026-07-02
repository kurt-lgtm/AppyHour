"""Inspect Shopify TR-AAB orders (tag _SHIP_2026-06-29) to pick a 'customized'
signal + find customer id/email for login join. Read-only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "InventoryReorder" / "fulfillment_web"))
from appyhour_lib.credentials import get_shopify_credentials  # noqa: E402
from shopify_bulk import _gql_post  # noqa: E402

STORE, TOKEN = get_shopify_credentials()
SHIP_TAG = "_SHIP_2026-06-29"

gql = """
query($cursor:String,$q:String!){
  orders(first:60, after:$cursor, query:$q){
    edges{ node{
      id name email createdAt
      tags
      customer{ id email numberOfOrders }
      customAttributes{ key value }
      lineItems(first:50){ edges{ node{
        sku quantity title
        customAttributes{ key value }
      }}}
    }}
    pageInfo{ hasNextPage endCursor }
  }
}
"""

q = f"(tag:{SHIP_TAG})"
data = _gql_post(STORE, TOKEN, gql, {"cursor": None, "q": q})
edges = (data.get("orders") or {}).get("edges", [])

# find first few orders containing TR-AAB
shown = 0
counts = {"total_seen": 0, "with_aab": 0}
for e in edges:
    n = e["node"]
    counts["total_seen"] += 1
    lis = [li["node"] for li in n["lineItems"]["edges"]]
    aab = [li for li in lis if (li.get("sku") or "").upper() == "TR-AAB" and (li.get("quantity") or 0) > 0]
    if not aab:
        continue
    counts["with_aab"] += 1
    if shown < 3:
        shown += 1
        print(f"\n===== ORDER {n['name']} =====")
        print("customer:", json.dumps(n.get("customer"), default=str))
        print("order customAttributes:", json.dumps(n.get("customAttributes"), default=str))
        print("tags:", n.get("tags"))
        print("--- line items (qty>0) ---")
        for li in lis:
            if (li.get("quantity") or 0) <= 0:
                continue
            ca = li.get("customAttributes") or []
            ca_keys = [a.get("key") for a in ca]
            print(f"  {li.get('sku'):<14} q{li.get('quantity')}  {li.get('title')[:30]:<30} attrs={ca_keys}")
        # dump full attrs of the AAB-bundle-related lines
        print("--- raw customAttributes per line ---")
        for li in lis:
            if (li.get("quantity") or 0) <= 0:
                continue
            print(f"  {li.get('sku')}: {json.dumps(li.get('customAttributes'), default=str)[:300]}")

print("\ncounts:", counts)
