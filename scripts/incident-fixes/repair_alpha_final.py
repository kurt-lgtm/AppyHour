"""Final CH-ALPHA repair — add back to all orders where it was removed."""

import time
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth, shopify_graphql

import requests

base, headers = get_shopify_auth()

# Look up CH-ALPHA $0 variant
data = shopify_graphql(base, headers, """
query {
  productVariants(first: 5, query: "sku:CH-ALPHA") {
    edges { node { id sku price } }
  }
}
""", {})
alpha_gid = None
for edge in data["productVariants"]["edges"]:
    node = edge["node"]
    if node["sku"] == "CH-ALPHA" and float(node["price"]) == 0:
        alpha_gid = node["id"]
        break
if not alpha_gid:
    alpha_gid = min(data["productVariants"]["edges"], key=lambda e: float(e["node"]["price"]))["node"]["id"]
print(f"CH-ALPHA variant GID: {alpha_gid}")

# Fetch ALL unfulfilled orders, find ones with CH-ALPHA at fq=0
all_orders = []
url = f"{base}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0
while url:
    page += 1
    resp = requests.get(url, headers=headers,
                        params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    orders = resp.json().get("orders", [])
    all_orders.extend(orders)
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            url = m.group(1)
    time.sleep(0.1)

# Find orders with CH-ALPHA removed (qty>0, fq=0) and no active CH-ALPHA
damaged = []
for o in all_orders:
    has_alpha_removed = False
    has_alpha_active = False
    for li in o.get("line_items", []):
        sku = li.get("sku", "") or ""
        if sku == "CH-ALPHA":
            fq = li.get("fulfillable_quantity", 0)
            qty = li.get("quantity", 0)
            if fq > 0:
                has_alpha_active = True
            elif qty > 0 and fq == 0:
                has_alpha_removed = True
    if has_alpha_removed and not has_alpha_active:
        damaged.append(o)

print(f"Orders needing CH-ALPHA repair: {len(damaged)}")

repaired = 0
failed = 0
for o in damaged:
    name = o.get("name", "")
    order_gid = f"gid://shopify/Order/{o['id']}"
    try:
        data = shopify_graphql(base, headers, """
            mutation orderEditBegin($id: ID!) {
                orderEditBegin(id: $id) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": order_gid})

        calc_order = data["orderEditBegin"]["calculatedOrder"]
        if not calc_order:
            errs = data["orderEditBegin"]["userErrors"]
            print(f"  {name}: beginEdit FAILED -- {errs}")
            failed += 1
            continue

        calc_id = calc_order["id"]

        data = shopify_graphql(base, headers, """
            mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
                orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "variantId": alpha_gid, "quantity": 1, "allowDuplicates": True})

        add_errs = data["orderEditAddVariant"]["userErrors"]
        if add_errs:
            print(f"  {name}: addVariant FAILED -- {add_errs}")
            failed += 1
            continue

        data = shopify_graphql(base, headers, """
            mutation orderEditCommit($id: ID!) {
                orderEditCommit(id: $id) {
                    order { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id})

        commit_errs = data["orderEditCommit"]["userErrors"]
        if commit_errs:
            print(f"  {name}: commitEdit FAILED -- {commit_errs}")
            failed += 1
            continue

        repaired += 1
        time.sleep(0.1)

    except Exception as e:
        print(f"  {name}: EXCEPTION -- {e}")
        failed += 1

print(f"\n=== DONE ===")
print(f"Repaired: {repaired}")
print(f"Failed: {failed}")
