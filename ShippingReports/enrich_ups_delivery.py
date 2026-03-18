"""Enrich UPS shipments with delivery dates from Shopify fulfillment data.

UPS invoices don't include delivery dates. This script:
1. Parses UPS invoices
2. Looks up tracking numbers in Shopify fulfilled orders
3. Extracts delivery dates from fulfillment events
4. Reports transit time analysis
"""
import json
import os
import re
import sys
import time
import requests
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parsers.ups import parse_ups_csv

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS) as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

HOT_STATES = {"FL", "TX", "AZ", "GA", "SC", "NC", "AL", "MS", "LA", "CA", "NV", "NM", "OK", "AR"}


def fetch_fulfilled_orders(after_date):
    """Fetch fulfilled orders with tracking data."""
    url = f"{REST}/orders.json"
    params = {
        "status": "any",
        "fulfillment_status": "fulfilled",
        "limit": 250,
        "created_at_min": after_date,
        "fields": "id,name,tags,fulfillments",
    }
    orders = []
    page = 0
    while url:
        page += 1
        print(f"  Shopify page {page} ({len(orders)} orders)...", flush=True)
        resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
        if resp.status_code != 200:
            print(f"  ERROR: {resp.status_code}")
            break
        data = resp.json()
        orders.extend(data.get("orders", []))
        url = None
        params = None
        link = resp.headers.get("Link", "")
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
        time.sleep(0.3)
    return orders


def build_tracking_map(orders):
    """Build tracking_number -> delivery_date map from Shopify fulfillments."""
    tracking_map = {}
    for order in orders:
        for ful in order.get("fulfillments", []):
            status = ful.get("shipment_status", "")
            # delivered or in_transit with updated_at
            tracking = ful.get("tracking_number", "")
            if not tracking:
                continue
            # Use updated_at as proxy for delivery if status is delivered
            if status == "delivered":
                delivered_at = ful.get("updated_at", "")
                if delivered_at:
                    try:
                        dt = datetime.fromisoformat(delivered_at.replace("Z", "+00:00"))
                        tracking_map[tracking] = dt.date()
                    except (ValueError, AttributeError):
                        pass
    return tracking_map


def main():
    invoices = [
        ("02/28", "GelPackCalculator/Invoices/Invoice_000000C411H4096_022826.csv"),
        ("03/07", "GelPackCalculator/Invoices/Invoice_000000C411H4106_030726.csv"),
        ("03/14", "GelPackCalculator/Invoices/Invoice_000000C411H4116_031426.csv"),
    ]

    base = r"C:\Users\Work\Claude Projects\AppyHour"
    all_ships = []
    for label, path in invoices:
        full = os.path.join(base, path)
        ships = parse_ups_csv(full)
        all_ships.extend(ships)
        print(f"UPS {label}: {len(ships)} shipments")

    print(f"\nTotal: {len(all_ships)} UPS shipments")

    # Fetch Shopify fulfilled orders
    print("\nFetching Shopify fulfilled orders...")
    after = (datetime.now() - timedelta(days=30)).isoformat()
    orders = fetch_fulfilled_orders(after)
    print(f"  {len(orders)} fulfilled orders")

    tracking_map = build_tracking_map(orders)
    print(f"  {len(tracking_map)} tracking numbers with delivery dates")

    # Enrich UPS shipments
    matched = 0
    for s in all_ships:
        if s.tracking in tracking_map:
            s.delivery_date = tracking_map[s.tracking]
            if s.ship_date:
                s.transit_days = (s.delivery_date - s.ship_date).days
            matched += 1

    print(f"  Matched: {matched}/{len(all_ships)} ({matched/len(all_ships)*100:.0f}%)")

    # Transit analysis
    with_transit = [s for s in all_ships if s.transit_days is not None and s.transit_days >= 0]
    print(f"\n{'='*70}")
    print(f"UPS TRANSIT ANALYSIS ({len(with_transit)} shipments with delivery data)")
    print(f"{'='*70}")

    if not with_transit:
        print("No transit data available.")
        return

    # Distribution
    td_counts = Counter(s.transit_days for s in with_transit)
    print("\nTransit day distribution:")
    for d in sorted(td_counts.keys()):
        print(f"  {d} days: {td_counts[d]}")

    # By state
    print(f"\nTop states:")
    for st, ct in Counter(s.state for s in with_transit).most_common(15):
        td_list = [s.transit_days for s in with_transit if s.state == st]
        avg = sum(td_list) / len(td_list)
        mx = max(td_list)
        h = " HOT" if st in HOT_STATES else ""
        print(f"  {st}: {ct} ships, avg {avg:.1f}d, max {mx}d{h}")

    # Problem zips
    print(f"\nPROBLEM ZIPS (hot 3+d, cold 4+d):")
    by_zip = defaultdict(list)
    for s in with_transit:
        by_zip[s.zip_code[:3]].append(s)

    problems = []
    for prefix, ships in by_zip.items():
        state = ships[0].state
        is_hot = state in HOT_STATES
        threshold = 3 if is_hot else 4
        bad = [s for s in ships if s.transit_days >= threshold]
        if not bad:
            continue
        problems.append((prefix, state, is_hot, len(bad), len(ships),
                         max(s.transit_days for s in ships), bad[0].city))

    problems.sort(key=lambda x: -x[3] / x[4])
    for prefix, state, is_hot, bad_ct, total, max_td, city in problems[:25]:
        pct = bad_ct / total * 100
        h = "HOT " if is_hot else ""
        print(f"  {prefix}xx {state} {h}{bad_ct}/{total} ({pct:.0f}%) max {max_td}d - {city}")

    # Slow deliveries
    slow = sorted([s for s in with_transit if s.transit_days > 5],
                  key=lambda x: -x.transit_days)
    print(f"\nVery slow UPS (>5d): {len(slow)}")
    for s in slow[:10]:
        print(f"  {s.transit_days}d {s.state}/{s.city} {s.zip_code} ${s.cost:.2f} "
              f"{s.service} {s.hub} {s.ship_date}->{s.delivery_date}")


if __name__ == "__main__":
    main()
