"""Check all MT-BRAS swapped orders for unexpected item removal (CH-ALPHA etc)."""

import csv
import json
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth

base, headers = get_shopify_auth()

# Orders from MT-BRAS swap results
swap_csv = "GelPackCalculator/swap_results_2026-04-10.csv"
order_names = []
with open(swap_csv) as f:
    for row in csv.DictReader(f):
        if "MT-BRAS->MT-SBRES" in row.get("swaps", ""):
            order_names.append(row["order"])

# Also check AC-APMB swapped orders that overlap
# For now just MT-BRAS batch
print(f"Checking {len(order_names)} MT-BRAS swapped orders...")

# Fetch all unfulfilled orders with _SHIP_2026-04-13
import requests

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
        import re
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            url = m.group(1)
    time.sleep(0.1)

# Index by order name
order_map = {o["name"]: o for o in all_orders}

# Check each swapped order
issues = []
for name in order_names:
    o = order_map.get(name)
    if not o:
        issues.append({"order": name, "issue": "ORDER NOT FOUND in unfulfilled"})
        continue

    items = o.get("line_items", [])
    active_skus = []
    removed_skus = []
    for li in items:
        sku = li.get("sku", "") or ""
        qty = li.get("quantity", 0)
        fq = li.get("fulfillable_quantity", 0)
        name_li = li.get("name", "")
        if fq == 0 and qty > 0:
            removed_skus.append(f"{sku}(qty={qty},fq=0)")
        elif fq > 0:
            active_skus.append(sku)

    # Check for CH-ALPHA removed
    has_alpha_removed = any("CH-ALPHA" in s for s in removed_skus)
    # Check if MT-SBRES is present (swap target)
    has_sbres = "MT-SBRES" in active_skus
    # Check if MT-BRAS still active (should be removed)
    has_bras_active = "MT-BRAS" in active_skus
    has_bras_removed = any("MT-BRAS" in s for s in removed_skus)

    order_issues = []
    if has_alpha_removed:
        order_issues.append(f"CH-ALPHA REMOVED: {[s for s in removed_skus if 'CH-ALPHA' in s]}")
    if not has_sbres:
        order_issues.append("MT-SBRES MISSING (swap target not found)")
    if has_bras_active:
        order_issues.append("MT-BRAS still active (should be removed)")

    # Report all removed SKUs for visibility
    non_bras_removed = [s for s in removed_skus if "MT-BRAS" not in s]
    if non_bras_removed:
        order_issues.append(f"Other removed items: {non_bras_removed}")

    if order_issues:
        issues.append({"order": name, "issues": order_issues, "active": active_skus, "removed": removed_skus})

print(f"\n=== RESULTS ===")
print(f"Orders checked: {len(order_names)}")
print(f"Orders with issues: {len(issues)}")
for iss in issues:
    print(f"\n{iss['order']}:")
    if "issue" in iss:
        print(f"  {iss['issue']}")
    else:
        for i in iss["issues"]:
            print(f"  - {i}")

if not issues:
    print("\nAll clean — no unexpected removals found.")
