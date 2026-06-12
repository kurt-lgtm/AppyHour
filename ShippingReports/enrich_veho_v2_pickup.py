"""Re-enrich Veho with PP pickup_date AND delivery_date for true Veho transit.
Veho transit = injection (pickup_date) -> delivery (delivery_date), not invoice ship_date -> delivery.

Adds DB cols if missing: veho_pickup_date, veho_transit_days
"""
import argparse, json, os, re, sqlite3, sys, time
from datetime import datetime, timedelta
import requests

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator")
from parcel_panel import ParcelPanelClient

DB = r"C:\Users\Work\Claude Projects\AppyHour\ShippingReports\output\shipments.db"
SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
ENV = r"C:\Users\Work\Claude Projects\AppyHour\.env"


def get_pp_key():
    return re.search(r'PARCEL_PANEL_API_KEY=(\S+)', open(ENV).read()).group(1)


def fetch_shopify_vh_pairs(days):
    s = json.load(open(SETTINGS))
    REST = f"https://{s['shopify_store_url']}.myshopify.com/admin/api/2026-04"
    H = {"X-Shopify-Access-Token": s['shopify_access_token']}
    after = (datetime.now() - timedelta(days=days)).date().isoformat()
    url = f"{REST}/orders.json"
    params = {"status": "any", "fulfillment_status": "fulfilled", "limit": 250,
              "created_at_min": after, "fields": "id,name,fulfillments"}
    pairs = []
    page = 0
    while url:
        page += 1
        if page % 5 == 0:
            print(f"  page {page} ({len(pairs)} pairs)...", flush=True)
        r = requests.get(url, headers=H, params=params, timeout=60)
        if r.status_code != 200:
            print(f"  ERR {r.status_code}"); break
        for o in r.json().get("orders", []):
            for f in o.get("fulfillments", []):
                tn = (f.get("tracking_number") or "")
                if tn.upper().startswith("VH"):
                    pairs.append((o["name"], tn))
        link = r.headers.get("Link", "")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                m = re.search(r'<([^>]+)>', part)
                if m: url = m.group(1)
        params = None
        time.sleep(0.4)
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    # Add veho_pickup_date column if missing
    c = sqlite3.connect(DB)
    cols = [r[1] for r in c.execute("PRAGMA table_info(shipments)")]
    if "veho_pickup_date" not in cols:
        c.execute("ALTER TABLE shipments ADD COLUMN veho_pickup_date TEXT")
        c.commit()
        print("Added veho_pickup_date column")

    print(f"Step 1: Shopify VH pairs (last {args.days}d)")
    pairs = fetch_shopify_vh_pairs(args.days)
    print(f"  {len(pairs)} VH tracking pairs")

    print(f"\nStep 2: PP lookups (capture pickup + delivery)")
    pp = ParcelPanelClient(get_pp_key())

    pickups = {}  # tracking -> (pickup, delivery)
    for i, (order_name, tn) in enumerate(pairs, 1):
        order_num = order_name.lstrip("#")
        if i % 100 == 0:
            print(f"  [{i}/{len(pairs)}] {order_num}", flush=True)
        try:
            data = pp.get_order_tracking(order_number=order_num)
            shipments = (data.get("order") or {}).get("shipments", [])
            for ship in shipments:
                if ship.get("tracking_number") == tn:
                    pu = ship.get("pickup_date")
                    dv = ship.get("delivery_date")
                    if pu and dv:
                        pickups[tn] = (pu[:10], dv[:10])
        except Exception:
            pass
        time.sleep(0.3)

    print(f"\n  Got pickup+delivery for {len(pickups)} VH trackings")

    print(f"\nStep 3: update DB with pickup_date + recompute transit")
    patched = 0
    for tn, (pu, dv) in pickups.items():
        n = c.execute("""
            UPDATE shipments
            SET veho_pickup_date = ?, delivery_date = ?,
                transit_days = CAST(julianday(?) - julianday(?) AS INTEGER)
            WHERE carrier='Veho' AND tracking=?
        """, (pu, dv, dv, pu, tn)).rowcount
        patched += n
    c.commit()
    print(f"  Patched {patched} Veho rows")

    after = c.execute("""
        SELECT COUNT(*), ROUND(AVG(transit_days),1),
               ROUND(100.0*SUM(CASE WHEN transit_days<=2 THEN 1 ELSE 0 END)/COUNT(*),0)
        FROM shipments WHERE carrier='Veho' AND veho_pickup_date IS NOT NULL
    """).fetchone()
    print(f"  Veho with pickup_date: {after[0]}, avg transit: {after[1]}d, 2-day rate: {after[2]}%")


if __name__ == "__main__":
    main()
