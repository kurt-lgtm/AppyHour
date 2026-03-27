"""Find an order with AC-PRPE, AC-MARC, and CH-ALP."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
H = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

TARGET = {"AC-PRPE", "AC-MARC", "CH-ALP"}
url = f"{BASE}/orders.json"
params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items"}
page = 0
found = []
while url:
    page += 1
    resp = requests.get(url, headers=H, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        skus = {(li.get("sku") or "").strip() for li in o.get("line_items", []) if li.get("fulfillable_quantity", li.get("quantity", 0)) > 0}
        hits = TARGET & skus
        if len(hits) >= 2:
            found.append((o["name"], hits))
        if len(hits) == 3:
            print(f"ALL THREE: {o['name']} -> {hits}")
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

if not any(len(h) == 3 for _, h in found):
    print("No single order has all three. Best matches (2 of 3):")
    for name, hits in found[:10]:
        print(f"  {name}: {hits}")
