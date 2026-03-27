"""Calculate average daily sales for a SKU from recent Shopify orders."""
import requests, json, time, datetime, sys
from collections import defaultdict

SKU = sys.argv[1] if len(sys.argv) > 1 else "CH-ALP"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 60

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

since = (datetime.date.today() - datetime.timedelta(days=DAYS)).isoformat()

# Fetch all orders (any status) created since cutoff
daily_qty = defaultdict(int)
total_qty = 0
order_count = 0
url = f"{REST_BASE}/orders.json"
params = {
    "status": "any",
    "created_at_min": f"{since}T00:00:00-05:00",
    "limit": 250,
    "fields": "id,name,created_at,line_items",
}
page = 0
while url:
    page += 1
    print(f"  Fetching page {page}...")
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    batch = resp.json().get("orders", [])
    for o in batch:
        created = o.get("created_at", "")[:10]
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            qty = li.get("quantity", 0)
            if sku == SKU and qty > 0:
                daily_qty[created] += qty
                total_qty += qty
                order_count += 1
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"\n{'='*50}")
print(f"  {SKU} — Last {DAYS} days (since {since})")
print(f"{'='*50}")
print(f"  Total units sold: {total_qty}")
print(f"  Orders containing {SKU}: {order_count}")
print(f"  Days with sales: {len(daily_qty)}")
print(f"  Average per day (over {DAYS}d): {total_qty / DAYS:.1f}")
if daily_qty:
    active_days = len(daily_qty)
    print(f"  Average per active day: {total_qty / active_days:.1f}")

# Show daily breakdown (last 14 days)
print(f"\n  Daily breakdown (last 14 days):")
print(f"  {'Date':<12} {'Qty':>5}")
print(f"  {'-'*18}")
today = datetime.date.today()
for i in range(14):
    d = (today - datetime.timedelta(days=i)).isoformat()
    qty = daily_qty.get(d, 0)
    bar = "#" * qty if qty <= 40 else "#" * 40 + f"...({qty})"
    print(f"  {d:<12} {qty:>5}  {bar}")
