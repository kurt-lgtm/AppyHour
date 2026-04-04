# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Order lookup utility — check SKUs across a list of Shopify orders.

Usage:
    python order_lookup.py --sku CH-MCPC --orders 124137,124142,124143
    python order_lookup.py --sku CH-MCPC --orders-file orders.txt
    python order_lookup.py --sku CH-MCPC,MT-SOP --tag _SHIP_2026-03-30
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "InventoryReorder"))

from utils import get_shopify_auth
import requests

def fetch_order_by_name(base, headers, order_num):
    """Fetch a single order by #number."""
    url = f"{base}/orders.json?name=%23{order_num}&status=any&fields=id,name,line_items,tags"
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        return None
    return resp.json().get("orders", [])

def fetch_orders_by_tag(base, headers, tag, limit=250):
    """Fetch unfulfilled orders by tag."""
    all_orders = []
    url = f"{base}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": limit,
        "fields": "id,name,tags,line_items",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code != 200:
        return []
    for o in resp.json().get("orders", []):
        if tag in (o.get("tags") or ""):
            all_orders.append(o)
    return all_orders

def check_orders_for_skus(orders, target_skus):
    """Return orders that contain any of the target SKUs with qty > 0."""
    matches = []
    for o in orders:
        found_skus = []
        for li in o.get("line_items", []):
            sku = li.get("sku", "")
            qty = li.get("quantity", 0)
            if sku in target_skus and qty > 0:
                found_skus.append((sku, qty))
        if found_skus:
            matches.append({
                "name": o.get("name", "?"),
                "tags": o.get("tags", ""),
                "skus": found_skus,
            })
    return matches

def main():
    parser = argparse.ArgumentParser(description="Check Shopify orders for specific SKUs")
    parser.add_argument("--sku", required=True, help="SKU(s) to search for, comma-separated")
    parser.add_argument("--orders", help="Order numbers, comma-separated")
    parser.add_argument("--orders-file", help="File with order numbers (one per line)")
    parser.add_argument("--tag", help="Fetch unfulfilled orders by tag instead of order numbers")
    args = parser.parse_args()

    target_skus = set(s.strip() for s in args.sku.split(","))
    base, headers = get_shopify_auth()

    if args.tag:
        print(f"Fetching unfulfilled orders with tag: {args.tag}")
        orders = fetch_orders_by_tag(base, headers, args.tag)
        print(f"Found {len(orders)} orders")
    else:
        order_nums = []
        if args.orders:
            order_nums = [n.strip().lstrip("#") for n in args.orders.split(",") if n.strip()]
        elif args.orders_file:
            with open(args.orders_file) as f:
                order_nums = [line.strip().lstrip("#") for line in f if line.strip() and line.strip()[0].isdigit()]
        else:
            print("ERROR: Provide --orders, --orders-file, or --tag")
            sys.exit(1)

        order_nums = sorted(set(order_nums))
        print(f"Checking {len(order_nums)} orders for {target_skus}")

        orders = []
        for i, num in enumerate(order_nums):
            fetched = fetch_order_by_name(base, headers, num)
            if fetched:
                orders.extend(fetched)
            if (i + 1) % 10 == 0:
                time.sleep(0.5)

        print(f"Fetched {len(orders)} orders")

    matches = check_orders_for_skus(orders, target_skus)

    print(f"\nOrders with {target_skus}: {len(matches)}")
    for m in matches:
        sku_str = ", ".join(f"{s} x{q}" for s, q in m["skus"])
        print(f"  {m['name']:10s} {sku_str}")

    if not matches:
        print("  None found")

if __name__ == "__main__":
    main()
