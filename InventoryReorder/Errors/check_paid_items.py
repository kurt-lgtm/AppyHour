"""Check if shortage SKUs have paid (non-curation) orders in _SHIP_2026-03-23."""
import requests, json, time
from collections import defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

SHORTAGE_SKUS = {
    "AC-PRPE", "CH-FOWC", "AC-LFOLIVE", "AC-MARC", "CH-ALP",
    "MT-BRAS", "CH-ALPHA", "CH-BRIE", "AC-PBLINI", "CH-TOPR",
    "CH-SHADOW", "CH-GAOP",
}

# Track paid vs curation per SKU
paid = defaultdict(lambda: {"orders": [], "qty": 0})
curation = defaultdict(lambda: {"count": 0, "qty": 0})

url = f"{REST_BASE}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0
while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    batch = resp.json().get("orders", [])
    for o in batch:
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku not in SHORTAGE_SKUS:
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            is_curation = "_rc_bundle" in prop_names
            if is_curation:
                curation[sku]["count"] += 1
                curation[sku]["qty"] += qty
            else:
                paid[sku]["orders"].append(o["name"])
                paid[sku]["qty"] += qty
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"{'='*70}")
print(f"  Shortage SKUs: Paid vs Curation (_SHIP_2026-03-23)")
print(f"{'='*70}")
print(f"{'SKU':<16} {'Curation':>8} {'Paid':>6} {'Paid Orders'}")
print("-" * 70)
for sku in sorted(SHORTAGE_SKUS):
    c_qty = curation[sku]["qty"]
    p_qty = paid[sku]["qty"]
    p_orders = paid[sku]["orders"]
    if c_qty == 0 and p_qty == 0:
        continue
    order_list = ", ".join(p_orders[:10])
    if len(p_orders) > 10:
        order_list += f" ...+{len(p_orders)-10} more"
    print(f"{sku:<16} {c_qty:>8} {p_qty:>6}  {order_list}")
