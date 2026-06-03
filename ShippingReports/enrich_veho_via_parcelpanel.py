"""Enrich Veho shipments with delivery dates via ParcelPanel API.
Path: Shopify → order_number+VH-tracking pairs → ParcelPanel lookup → POD date → DB update.
"""
import argparse, json, os, re, sqlite3, sys, time
from datetime import datetime, timedelta
import requests

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator")
from parcel_panel import ParcelPanelClient

DB = r"C:\Users\Work\Claude Projects\AppyHour\ShippingReports\output\shipments.db"
SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
ENV_FILE = r"C:\Users\Work\Claude Projects\AppyHour\.env"


def get_pp_key():
    for line in open(ENV_FILE):
        if line.strip().startswith("PARCEL_PANEL_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("PARCEL_PANEL_API_KEY not in .env")


def fetch_shopify_vh_pairs(days):
    """Pull Shopify fulfilled orders, return [(order_name, vh_tracking, ...)]."""
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
        print(f"  Shopify page {page} ({len(pairs)} VH pairs)...", flush=True)
        r = requests.get(url, headers=H, params=params, timeout=60)
        if r.status_code != 200:
            print(f"  ERR {r.status_code}"); break
        for o in r.json().get("orders", []):
            for f in o.get("fulfillments", []):
                tn = f.get("tracking_number", "")
                if tn and tn.upper().startswith("VH"):
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
    ap.add_argument("--limit", type=int, default=0, help="Probe limit (0=no limit)")
    args = ap.parse_args()

    print(f"Step 1: pull Shopify VH* tracking pairs (last {args.days}d)")
    pairs = fetch_shopify_vh_pairs(args.days)
    print(f"  Total VH pairs: {len(pairs)}")
    if args.limit:
        pairs = pairs[:args.limit]
        print(f"  Probe limit: {len(pairs)}")

    print(f"\nStep 2: ParcelPanel lookups")
    pp = ParcelPanelClient(get_pp_key())
    test = pp.test_connection()
    print(f"  PP connect: {test.get('success')} ({test.get('message')})")

    tracking_to_pod = {}
    for i, (order_name, vh_track) in enumerate(pairs, 1):
        order_num = order_name.lstrip("#")
        if i % 25 == 0 or i == 1:
            print(f"  [{i}/{len(pairs)}] querying {order_num} -> {vh_track}", flush=True)
        try:
            data = pp.get_order_tracking(order_number=order_num)
            shipments = (data.get("order") or {}).get("shipments", [])
            for ship in shipments:
                norm = pp._normalize_parcel(ship, order_num)
                if not norm: continue
                if norm.get("tracking_number") == vh_track and norm.get("delivery_date"):
                    tracking_to_pod[vh_track] = norm["delivery_date"]
        except Exception as e:
            pass
        time.sleep(0.3)

    print(f"\n  Got delivery_date for {len(tracking_to_pod)} VH trackings")

    print(f"\nStep 3: update DB")
    c = sqlite3.connect(DB)
    patched = 0
    for tn, pod in tracking_to_pod.items():
        pod_date = pod[:10] if isinstance(pod, str) else None
        if not pod_date: continue
        n = c.execute(
            "UPDATE shipments SET delivery_date=? WHERE carrier='Veho' AND tracking=? AND delivery_date IS NULL",
            (pod_date, tn)
        ).rowcount
        patched += n
    # TNT RULE (feedback_transit_calc_rule): record transit ONLY as final-mile
    # delivered − final-mile picked up. NEVER ship_date (Veho 'Tendered'/injection
    # timestamp — inflates TNT ~2.5d) and NEVER ParcelPanel's own transit field.
    # pickup_date + delivery_date are the final-mile PP scans in delivery_status.
    c.execute("""
        UPDATE shipments SET transit_days = CAST(julianday(delivery_date) - julianday((
            SELECT d.pickup_date FROM delivery_status d
            WHERE d.tracking_number = shipments.tracking AND d.pickup_date IS NOT NULL
            ORDER BY d.pickup_date LIMIT 1
        )) AS INTEGER)
        WHERE carrier='Veho' AND delivery_date IS NOT NULL
          AND EXISTS (SELECT 1 FROM delivery_status d
                      WHERE d.tracking_number = shipments.tracking AND d.pickup_date IS NOT NULL)
    """)
    c.commit()
    print(f"  patched {patched} Veho rows")
    after = c.execute("SELECT COUNT(*) FROM shipments WHERE carrier='Veho' AND delivery_date IS NOT NULL").fetchone()[0]
    print(f"  Veho rows w/ delivery_date now: {after}/4143")


if __name__ == "__main__":
    main()
