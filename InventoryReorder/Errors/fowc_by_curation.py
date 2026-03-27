"""Break down remaining CH-FOWC curation orders by curation type."""
import requests, json, time
from collections import Counter

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

url = f"{REST_BASE}/orders.json"
params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items"}
page = 0
curations = Counter()

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        has_fowc = False
        curation = ""
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if fq <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if sku == "CH-FOWC" and "_rc_bundle" in prop_names:
                has_fowc = True
            if sku.startswith("AHB-MCUST") or sku.startswith("AHB-LCUST"):
                curation = sku.split("-")[-1]
        if has_fowc:
            curations[curation or "(unknown)"] += 1
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"Remaining curation CH-FOWC by curation:")
for cur, cnt in curations.most_common():
    print(f"  {cur}: {cnt}")
print(f"  TOTAL: {sum(curations.values())}")
