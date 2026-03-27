"""Check how many orders have 2+ MT-SOP after the BRAS->SOP swap."""
import requests, json, time

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
doubles = []

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        sop_count = 0
        has_bras_removed = False
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if sku == "MT-SOP" and fq > 0:
                sop_count += fq
            if sku == "MT-BRAS" and fq == 0 and li.get("quantity", 0) > 0:
                has_bras_removed = True
        if sop_count >= 2 and has_bras_removed:
            doubles.append({"order": o["name"], "sop_count": sop_count})
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"Orders with 2+ MT-SOP (from BRAS->SOP swap): {len(doubles)}")
for d in doubles:
    print(f"  {d['order']} x{d['sop_count']}")
