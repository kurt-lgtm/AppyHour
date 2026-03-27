"""Check how many AC-MARC are from BL-SDB (Spring Dessert Bundle) orders."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

url = f"{REST_BASE}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0
bundle_marc = []
non_bundle_marc = []

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        # Check if order has BL-SDB
        skus = {}
        has_sdb = False
        marc_items = []
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if sku == "BL-SDB" and qty > 0:
                has_sdb = True
            if sku == "AC-MARC" and qty > 0:
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                marc_items.append({
                    "order": o["name"],
                    "qty": qty,
                    "is_curation": "_rc_bundle" in prop_names,
                    "price": li.get("price", "0"),
                })
        if marc_items and has_sdb:
            bundle_marc.extend(marc_items)
        elif marc_items and not has_sdb:
            non_bundle_marc.extend(marc_items)
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

bundle_qty = sum(m["qty"] for m in bundle_marc)
non_bundle_qty = sum(m["qty"] for m in non_bundle_marc)

print(f"AC-MARC from BL-SDB orders: {bundle_qty} ({len(bundle_marc)} line items)")
for m in bundle_marc[:10]:
    print(f"  {m['order']} x{m['qty']} curation={m['is_curation']} ${m['price']}")
if len(bundle_marc) > 10:
    print(f"  ...+{len(bundle_marc)-10} more")

print(f"\nAC-MARC NOT from BL-SDB: {non_bundle_qty} ({len(non_bundle_marc)} line items)")
