"""Remove incorrectly-added CH-MCPC from paid CH-IPAC orders."""

import csv
import time
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth, shopify_graphql

import requests

base, headers = get_shopify_auth()

TARGET_NAMES = {"#127148", "#127691", "#127877", "#128674", "#128932", "#128951", "#129001"}

# Fetch all unfulfilled orders
all_orders = []
url = f"{base}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name",
}
page = 0
while url:
    page += 1
    resp = requests.get(url, headers=headers,
                        params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    all_orders.extend(resp.json().get("orders", []))
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

for name in sorted(TARGET_NAMES):
    o = order_map.get(name)
    if not o:
        print(f"  {name}: NOT FOUND -- skipping")
        failed += 1
        continue

    order_gid = f"gid://shopify/Order/{o['id']}"
    try:
        data = shopify_graphql(base, headers, """
            mutation orderEditBegin($id: ID!) {
                orderEditBegin(id: $id) {
                    calculatedOrder {
                        id
                        lineItems(first: 50) {
                            edges { node { id quantity sku } }
                        }
                    }
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

        # Find CH-MCPC (the $0 one we incorrectly added)
        mcpc_li = None
        for edge in calc_order["lineItems"]["edges"]:
            node = edge["node"]
            if node.get("sku") == "CH-MCPC" and node.get("quantity", 0) > 0:
                mcpc_li = (node["id"], node["quantity"])
                break

        if not mcpc_li:
            print(f"  {name}: no active CH-MCPC found -- skipping")
            failed += 1
            continue

        li_id, qty = mcpc_li

        shopify_graphql(base, headers, """
            mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
                orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "lineItemId": li_id, "quantity": 0})

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

        print(f"  {name}: CH-MCPC removed OK")
        repaired += 1
        time.sleep(0.1)

    except Exception as e:
        print(f"  {name}: EXCEPTION -- {e}")
        failed += 1

print(f"\n=== DONE ===")
print(f"Repaired: {repaired}")
print(f"Failed: {failed}")
