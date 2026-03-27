"""Check how many paid AC-MARC are from AHB-X specialty boxes."""
import requests, json, time
from collections import defaultdict

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

ahbx_orders = []
bl_orders = []
true_standalone = []

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue

        items = o.get("line_items", [])
        # Find all special SKUs on the order
        ahbx_skus = []
        bl_skus = []
        has_marc_paid = False
        marc_qty = 0

        for li in items:
            sku = (li.get("sku") or "").strip()
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            if sku.startswith("AHB-X"):
                ahbx_skus.append(sku)
            if sku.startswith("BL-"):
                bl_skus.append(sku)
            if sku == "AC-MARC":
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if "_rc_bundle" not in prop_names:
                    has_marc_paid = True
                    marc_qty += qty

        if not has_marc_paid:
            continue

        if ahbx_skus:
            ahbx_orders.append({"order": o["name"], "ahbx": ahbx_skus, "bl": bl_skus, "qty": marc_qty})
        elif bl_skus:
            bl_orders.append({"order": o["name"], "bl": bl_skus, "qty": marc_qty})
        else:
            true_standalone.append({"order": o["name"], "qty": marc_qty})

    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"Paid AC-MARC source breakdown (all, including BL-SDB):")
print(f"  From AHB-X specialty boxes: {len(ahbx_orders)} ({sum(o['qty'] for o in ahbx_orders)} units)")
print(f"  From BL- bundles (incl SDB): {len(bl_orders)} ({sum(o['qty'] for o in bl_orders)} units)")
print(f"  True standalone add-ons: {len(true_standalone)} ({sum(o['qty'] for o in true_standalone)} units)")

if ahbx_orders:
    ahbx_counts = defaultdict(int)
    print(f"\n  AHB-X orders:")
    for o in ahbx_orders:
        for sku in o["ahbx"]:
            ahbx_counts[sku] += o["qty"]
        print(f"    {o['order']} AHB-X={o['ahbx']}")
    print(f"\n  By AHB-X SKU:")
    for sku, cnt in sorted(ahbx_counts.items(), key=lambda x: -x[1]):
        print(f"    {sku}: {cnt}")

if bl_orders:
    bl_counts = defaultdict(int)
    for o in bl_orders:
        for sku in o["bl"]:
            bl_counts[sku] += o["qty"]
    print(f"\n  By BL- SKU:")
    for sku, cnt in sorted(bl_counts.items(), key=lambda x: -x[1]):
        print(f"    {sku}: {cnt}")

if true_standalone:
    print(f"\n  First 10 true standalone orders:")
    for o in true_standalone[:10]:
        print(f"    {o['order']} x{o['qty']}")
