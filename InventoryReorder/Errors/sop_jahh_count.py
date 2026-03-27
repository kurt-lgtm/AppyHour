"""Count orders with both curated MT-SOP and curated MT-JAHH."""
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
both = []
sop_only = 0
jahh_only = 0

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        has_sop = False
        has_jahh = False
        curation = ""
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if fq <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if sku == "MT-SOP" and "_rc_bundle" in prop_names:
                has_sop = True
            if sku == "MT-JAHH" and "_rc_bundle" in prop_names:
                has_jahh = True
            if sku.startswith("AHB-MCUST") or sku.startswith("AHB-LCUST"):
                curation = sku.split("-")[-1]
        if has_sop and has_jahh:
            both.append({"order": o["name"], "curation": curation})
        elif has_sop:
            sop_only += 1
        elif has_jahh:
            jahh_only += 1
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

from collections import Counter
cur_counts = Counter(b["curation"] for b in both)

print(f"Curated orders with BOTH MT-SOP + MT-JAHH: {len(both)}")
print(f"MT-SOP only (curated): {sop_only}")
print(f"MT-JAHH only (curated): {jahh_only}")
print(f"\nBy curation:")
for cur, cnt in cur_counts.most_common():
    print(f"  {cur or '(unknown)'}: {cnt}")
