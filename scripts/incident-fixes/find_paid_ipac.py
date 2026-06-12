"""Find orders where CH-IPAC was a paid item (no _rc_bundle property) that got swapped to CH-MCPC."""

import csv
import time
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth

import requests

base, headers = get_shopify_auth()

# Get IPAC swapped orders from CSV
swap_csv = "GelPackCalculator/swap_results_2026-04-10.csv"
ipac_orders = set()
with open(swap_csv) as f:
    for row in csv.DictReader(f):
        if "CH-IPAC->CH-MCPC" in row.get("swaps", ""):
            ipac_orders.add(row["order"])

print(f"CH-IPAC->CH-MCPC orders to check: {len(ipac_orders)}")

# Fetch all unfulfilled orders with full line_items + properties
all_orders = []
url = f"{base}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items,email",
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

paid_ipac = []
curation_ipac = []

for name in sorted(ipac_orders):
    o = order_map.get(name)
    if not o:
        continue

    for li in o.get("line_items", []):
        sku = li.get("sku", "") or ""
        if sku != "CH-IPAC":
            continue

        # Check properties for _rc_bundle
        props = li.get("properties", []) or []
        prop_names = [p.get("name", "") for p in props]
        is_rc = "_rc_bundle" in prop_names

        fq = li.get("fulfillable_quantity", 0)
        qty = li.get("quantity", 0)
        price = li.get("price", "0.00")

        if not is_rc:
            paid_ipac.append({
                "order": name,
                "email": o.get("email", ""),
                "price": price,
                "qty": qty,
                "fq": fq,
                "props": prop_names,
                "order_id": o["id"],
            })
        else:
            curation_ipac.append(name)

print(f"\n=== PAID CH-IPAC (should NOT have been swapped) ===")
print(f"Count: {len(paid_ipac)}")
for p in paid_ipac:
    print(f"  {p['order']} | {p['email']} | price={p['price']} | props={p['props']}")

print(f"\n=== Curation CH-IPAC (correctly swapped) ===")
print(f"Count: {len(curation_ipac)}")
