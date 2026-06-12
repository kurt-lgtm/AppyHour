"""Swap MT-JAHH -> MT-PSS on orders with 'Subscription First Order' + 'RMFG_20260410'.
Protects CH-ALPHA: re-adds if removed during edit session."""

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

OLD_SKU = "MT-JAHH"
NEW_SKU = "MT-PSS"
REQUIRED_TAGS = {"Subscription First Order", "RMFG_20260410"}
EXCLUDE_TAGS = {"Reship", "BOX_CUSTOMIZED_POST_CHECKOUT"}

# Look up $0 variants
def lookup_zero_variant(sku):
    data = shopify_graphql(base, headers, """
    query($q: String!) {
      productVariants(first: 5, query: $q) {
        edges { node { id sku price } }
      }
    }
    """, {"q": f"sku:{sku}"})
    for edge in data["productVariants"]["edges"]:
        node = edge["node"]
        if node["sku"] == sku and float(node["price"]) == 0:
            return node["id"]
    return min(data["productVariants"]["edges"], key=lambda e: float(e["node"]["price"]))["node"]["id"]

pss_gid = lookup_zero_variant(NEW_SKU)
alpha_gid = lookup_zero_variant("CH-ALPHA")
print(f"MT-PSS GID: {pss_gid}")
print(f"CH-ALPHA GID: {alpha_gid}")

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
    all_orders.extend(resp.json().get("orders", []))
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            url = m.group(1)
    time.sleep(0.1)

# Filter: has both required tags, has MT-JAHH with fq>0 and _rc_bundle
targets = []
for o in all_orders:
    tags = {t.strip() for t in o.get("tags", "").split(",")}
    if not REQUIRED_TAGS.issubset(tags):
        continue
    if tags & EXCLUDE_TAGS:
        continue
    for li in o.get("line_items", []):
        sku = li.get("sku", "") or ""
        fq = li.get("fulfillable_quantity", 0)
        if sku == OLD_SKU and fq > 0:
            targets.append(o)
            break

print(f"\nOrders to swap: {len(targets)}")

if not targets:
    print("Nothing to swap.")
    sys.exit(0)

# Preview
for o in targets:
    print(f"  {o.get('name')}")

print(f"\nExecuting swaps...")

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

        # Snapshot all items for CH-ALPHA protection
        alpha_before = None
        jahh_li = None
        for edge in calc_order["lineItems"]["edges"]:
            node = edge["node"]
            sku = node.get("sku") or ""
            qty = node.get("quantity", 0)
            if sku == "CH-ALPHA" and qty > 0:
                alpha_before = (node["id"], qty)
            if sku == OLD_SKU and qty > 0:
                jahh_li = (node["id"], qty)

        if not jahh_li:
            errors.append({"order": name, "error": "No swappable MT-JAHH in calculated order"})
            continue

        li_id, qty = jahh_li

        # Remove MT-JAHH
        shopify_graphql(base, headers, """
            mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
                orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "lineItemId": li_id, "quantity": 0})

        # Add MT-PSS
        shopify_graphql(base, headers, """
            mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
                orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "variantId": pss_gid, "quantity": qty, "allowDuplicates": True})

        # Pre-commit: check if CH-ALPHA got removed
        verify_data = shopify_graphql(base, headers, """
            query($id: ID!) {
                node(id: $id) {
                    ... on CalculatedOrder {
                        lineItems(first: 50) {
                            edges { node { id quantity sku } }
                        }
                    }
                }
            }
        """, {"id": calc_id})

        alpha_after = None
        for edge in verify_data["node"]["lineItems"]["edges"]:
            node = edge["node"]
            if node.get("sku") == "CH-ALPHA":
                alpha_after = (node["id"], node.get("quantity", 0))

        # If CH-ALPHA was present before but now qty=0, re-add it
        alpha_repaired = False
        if alpha_before and alpha_before[1] > 0:
            if not alpha_after or alpha_after[1] == 0:
                shopify_graphql(base, headers, """
                    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
                        orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                            calculatedOrder { id }
                            userErrors { field message }
                        }
                    }
                """, {"id": calc_id, "variantId": alpha_gid, "quantity": alpha_before[1], "allowDuplicates": True})
                alpha_repaired = True

        # Commit
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

        suffix = " (+CH-ALPHA protected)" if alpha_repaired else ""
        print(f"  {name}: swapped OK{suffix}")
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
