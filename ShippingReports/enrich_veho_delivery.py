"""Enrich Veho shipments with delivery dates via Shopify fulfillments
(path c per VEHO_ENRICHMENT_DESIGN.md). Falls back to Parcel Panel if VH*
tracking numbers don't appear in Shopify.

Usage: python enrich_veho_delivery.py [--days 90]
"""
import argparse, json, os, re, sqlite3, sys, time
import requests
from datetime import datetime, timedelta, date

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
DB = r"C:\Users\Work\Claude Projects\AppyHour\ShippingReports\output\shipments.db"

with open(SETTINGS) as f:
    settings = json.load(f)
STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST = f"https://{STORE}.myshopify.com/admin/api/2026-04"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

VH_RX = re.compile(r'^VH', re.IGNORECASE)


def fetch_fulfilled_orders(after_date):
    url = f"{REST}/orders.json"
    params = {
        "status": "any",
        "fulfillment_status": "fulfilled",
        "limit": 250,
        "created_at_min": after_date,
        "fields": "id,name,fulfillments",
    }
    orders, page = [], 0
    while url:
        page += 1
        print(f"  page {page} ({len(orders)} orders so far)...", flush=True)
        resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
        if resp.status_code != 200:
            print(f"  ERROR: {resp.status_code} {resp.text[:200]}"); break
        orders.extend(resp.json().get("orders", []))
        link = resp.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                m = re.search(r'<([^>]+)>', part)
                if m: next_url = m.group(1)
        url = next_url
        params = None
        time.sleep(0.5)  # rate limit
    return orders


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    after = (datetime.now() - timedelta(days=args.days)).date().isoformat()
    print(f"Fetching Shopify fulfilled orders since {after}...")
    orders = fetch_fulfilled_orders(after)
    print(f"Got {len(orders)} fulfilled orders")

    # Build tracking -> (delivery_date, order_name) map for VH* trackings
    tracking_map = {}
    vh_count = 0
    for o in orders:
        for f in o.get("fulfillments", []):
            tn = f.get("tracking_number")
            if not tn or not VH_RX.match(tn):
                continue
            vh_count += 1
            # delivery date — try shipment_status events or updated_at
            deliv = None
            if f.get("shipment_status") == "delivered":
                # Use updated_at as proxy
                ua = f.get("updated_at")
                if ua:
                    deliv = ua[:10]
            tracking_map[tn] = (deliv, o.get("name", ""))

    print(f"VH* trackings in Shopify: {vh_count}")
    print(f"Distinct VH* with delivery date: {sum(1 for v in tracking_map.values() if v[0])}")

    if vh_count == 0:
        print("\n[!] No VH* trackings found in Shopify fulfillments.")
        print("    Path (c) DEAD — fall back to Parcel Panel (separate script).")
        return

    # Update DB
    c = sqlite3.connect(DB)
    patched = 0
    for tracking, (deliv, _name) in tracking_map.items():
        if not deliv:
            continue
        n = c.execute(
            "UPDATE shipments SET delivery_date=? WHERE carrier='Veho' AND tracking=? AND delivery_date IS NULL",
            (deliv, tracking)
        ).rowcount
        patched += n
    # Recompute transit_days
    c.execute("""
        UPDATE shipments SET transit_days = CAST(julianday(delivery_date) - julianday(ship_date) AS INTEGER)
        WHERE carrier='Veho' AND delivery_date IS NOT NULL AND ship_date IS NOT NULL AND transit_days IS NULL
    """)
    c.commit()
    print(f"Patched delivery_date on {patched} Veho rows")

    after = c.execute("SELECT COUNT(*) FROM shipments WHERE carrier='Veho' AND delivery_date IS NOT NULL").fetchone()[0]
    print(f"Veho rows with delivery_date now: {after}/4143")


if __name__ == "__main__":
    main()
