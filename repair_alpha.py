"""Repair: add CH-ALPHA $0 variant back to 23 orders where it was incorrectly removed."""

import time
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth, shopify_graphql

import requests

base, headers = get_shopify_auth()

# 23 damaged orders from check_swap_damage.py
DAMAGED_ORDERS = [
    "#128945", "#128960", "#128939", "#128781", "#128824",
    "#128716", "#128702", "#128662", "#128601", "#128596",
    "#128548", "#128406", "#128680", "#128324", "#128270",
    "#128012", "#128314", "#127906", "#127890", "#127725",
    "#127053", "#127043", "#126995",
]

# Look up CH-ALPHA $0 variant
data = shopify_graphql(base, headers, """
query {
  productVariants(first: 5, query: "sku:CH-ALPHA") {
    edges { node { id sku price } }
  }
}
""", {})
alpha_variants = data["productVariants"]["edges"]
alpha_gid = None
for edge in alpha_variants:
    node = edge["node"]
    if node["sku"] == "CH-ALPHA" and float(node["price"]) == 0:
        alpha_gid = node["id"]
        break
if not alpha_gid:
    # Fallback: cheapest
    alpha_gid = min(alpha_variants, key=lambda e: float(e["node"]["price"]))["node"]["id"]

print(f"CH-ALPHA variant GID: {alpha_gid}")
print(f"Repairing {len(DAMAGED_ORDERS)} orders...\n")

# Fetch all unfulfilled to get order IDs
all_orders = []
url = f"{base}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags",
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

order_map = {o["name"]: o for o in all_orders}

repaired = 0
failed = 0

for name in DAMAGED_ORDERS:
    o = order_map.get(name)
    if not o:
        print(f"  {name}: NOT FOUND — skipping")
        failed += 1
        continue

    order_gid = f"gid://shopify/Order/{o['id']}"

    try:
        # beginEdit
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
            errors = data["orderEditBegin"]["userErrors"]
            print(f"  {name}: beginEdit FAILED — {errors}")
            failed += 1
            continue

        calc_id = calc_order["id"]

        # addVariant (CH-ALPHA $0, allowDuplicates in case remnant exists)
        data = shopify_graphql(base, headers, """
            mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
                orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "variantId": alpha_gid, "quantity": 1, "allowDuplicates": True})

        add_errors = data["orderEditAddVariant"]["userErrors"]
        if add_errors:
            print(f"  {name}: addVariant FAILED — {add_errors}")
            failed += 1
            continue

        # commitEdit
        data = shopify_graphql(base, headers, """
            mutation orderEditCommit($id: ID!) {
                orderEditCommit(id: $id) {
                    order { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id})

        commit_errors = data["orderEditCommit"]["userErrors"]
        if commit_errors:
            print(f"  {name}: commitEdit FAILED — {commit_errors}")
            failed += 1
            continue

        print(f"  {name}: REPAIRED OK")
        repaired += 1
        time.sleep(0.1)

    except Exception as e:
        print(f"  {name}: EXCEPTION — {e}")
        failed += 1

print(f"\n=== DONE ===")
print(f"Repaired: {repaired}")
print(f"Failed: {failed}")
