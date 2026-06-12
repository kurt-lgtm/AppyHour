"""Swap MT-CAPO -> MT-PSS on _SHIP_2026-04-13, excluding Reship orders."""

import time
import sys
import os
import re
import csv
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth, shopify_graphql

import requests

base, headers = get_shopify_auth()

SHIP_TAG = "_SHIP_2026-04-13"
OLD_SKU = "MT-CAPO"
NEW_SKU = "MT-PSS"
EXCLUDE_TAG = "Reship"

# Look up $0 variant for MT-PSS
data = shopify_graphql(base, headers, """
query {
  productVariants(first: 5, query: "sku:MT-PSS") {
    edges { node { id sku price } }
  }
}
""", {})
pss_gid = None
for edge in data["productVariants"]["edges"]:
    node = edge["node"]
    if node["sku"] == NEW_SKU and float(node["price"]) == 0:
        pss_gid = node["id"]
        break
if not pss_gid:
    pss_gid = min(data["productVariants"]["edges"], key=lambda e: float(e["node"]["price"]))["node"]["id"]
print(f"MT-PSS variant GID: {pss_gid}")

# Fetch all unfulfilled orders
all_orders = []
url = f"{base}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items,customer,email",
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

# Filter: has ship tag, has MT-CAPO with fq>0, no Reship tag
targets = []
skipped_reship = []
for o in all_orders:
    tags = [t.strip() for t in o.get("tags", "").split(",")]
    if SHIP_TAG not in tags:
        continue
    if EXCLUDE_TAG in tags:
        # Check if it even has MT-CAPO before counting as skipped
        has_capo = any(
            li.get("sku") == OLD_SKU and li.get("fulfillable_quantity", 0) > 0
            for li in o.get("line_items", [])
        )
        if has_capo:
            skipped_reship.append(o.get("name"))
        continue
    has_capo = any(
        li.get("sku") == OLD_SKU and li.get("fulfillable_quantity", 0) > 0
        for li in o.get("line_items", [])
    )
    if has_capo:
        targets.append(o)

print(f"Orders to swap: {len(targets)}")
print(f"Skipped (Reship): {len(skipped_reship)} — {skipped_reship}")

# Execute swaps sequentially
results = []
errors = []
for o in targets:
    oid = o["id"]
    name = o.get("name", "")
    order_gid = f"gid://shopify/Order/{oid}"
    email = ""
    cust = o.get("customer")
    if cust:
        email = cust.get("email", "") or ""
    if not email:
        email = o.get("email", "") or ""

    try:
        # beginEdit
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
            errors.append({"order": name, "error": f"beginEdit failed: {errs}"})
            continue

        calc_id = calc_order["id"]

        # Find MT-CAPO line item
        capo_li = None
        for edge in calc_order["lineItems"]["edges"]:
            node = edge["node"]
            if node.get("sku") == OLD_SKU and node.get("quantity", 0) > 0:
                capo_li = (node["id"], node["quantity"])
                break

        if not capo_li:
            errors.append({"order": name, "error": "No swappable MT-CAPO in calculated order"})
            continue

        li_id, qty = capo_li

        # setQuantity 0
        shopify_graphql(base, headers, """
            mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
                orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "lineItemId": li_id, "quantity": 0})

        # addVariant
        shopify_graphql(base, headers, """
            mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
                orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "variantId": pss_gid, "quantity": qty, "allowDuplicates": True})

        # commit
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
            errors.append({"order": name, "error": f"commitEdit failed: {commit_errs}"})
            continue

        results.append({"order": name, "email": email, "swap": f"{OLD_SKU}->{NEW_SKU}(qty={qty})"})
        time.sleep(0.1)

    except Exception as e:
        errors.append({"order": name, "error": str(e)})

# Append to CSV
csv_path = "GelPackCalculator/swap_results_2026-04-10.csv"
with open(csv_path, "a", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["order", "email", "swaps"])
    for r in results:
        writer.writerow({"order": r["order"], "email": r["email"], "swaps": r["swap"]})

print(f"\n=== DONE ===")
print(f"Swapped: {len(results)}")
print(f"Failed: {len(errors)}")
for e in errors:
    print(f"  {e['order']}: {e['error']}")
