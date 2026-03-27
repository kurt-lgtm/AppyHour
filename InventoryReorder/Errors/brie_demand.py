"""Check demand for all BRIE SKUs on _SHIP_2026-03-23."""
import requests, json, time
from collections import defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

demand = defaultdict(lambda: {"curation": 0, "paid": 0, "total": 0})
url = f"{REST_BASE}/orders.json"
params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items"}
page = 0
while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if "BRIE" not in sku.upper() and "CAM" not in sku.upper():
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" in prop_names:
                demand[sku]["curation"] += qty
            else:
                demand[sku]["paid"] += qty
            demand[sku]["total"] += qty
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"{'SKU':<16} {'Total':>6} {'Curation':>8} {'Paid':>6}")
print("-" * 40)
for sku in sorted(demand.keys()):
    d = demand[sku]
    print(f"{sku:<16} {d['total']:>6} {d['curation']:>8} {d['paid']:>6}")
