"""Check ALL orders swapped today for unexpected item removals."""

import argparse
import csv
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth

import requests

DEFAULT_SWAP_CSV = "GelPackCalculator/swap_results_2026-04-10.csv"

# Expected swap targets (SKUs that SHOULD be removed)
EXPECTED_REMOVALS = {"AC-APMB", "MT-BRAS", "CH-LEON", "CH-BRIE", "CH-FONT", "CH-FOWC", "CH-IPAC"}


def main():
    parser = argparse.ArgumentParser(description="Check swapped orders for unexpected item removals")
    parser.add_argument("--swap-csv", default=DEFAULT_SWAP_CSV, help="CSV of swapped orders with 'order' column")
    args = parser.parse_args()

    base, headers = get_shopify_auth()

    # Read all swapped orders from today's CSV
    order_names = set()
    with open(args.swap_csv) as f:
        for row in csv.DictReader(f):
            order_names.add(row["order"])

    print(f"Checking {len(order_names)} unique orders from today's swaps...")

    # Fetch all unfulfilled orders
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

    order_map = {o["name"]: o for o in all_orders}

    issues = []
    for name in sorted(order_names):
        o = order_map.get(name)
        if not o:
            continue  # fulfilled or not found

        removed_skus = []
        for li in o.get("line_items", []):
            sku = li.get("sku", "") or ""
            qty = li.get("quantity", 0)
            fq = li.get("fulfillable_quantity", 0)
            if fq == 0 and qty > 0 and sku not in EXPECTED_REMOVALS:
                removed_skus.append(f"{sku}(qty={qty},fq=0)")

        if removed_skus:
            issues.append({"order": name, "unexpected_removed": removed_skus})

    print("\n=== RESULTS ===")
    print(f"Orders checked: {len(order_names)}")
    print(f"Orders with UNEXPECTED removals: {len(issues)}")
    for iss in issues:
        print(f"\n{iss['order']}:")
        for r in iss["unexpected_removed"]:
            print(f"  - {r}")

    if not issues:
        print("\nAll clean -- no unexpected removals.")


if __name__ == "__main__":
    main()
