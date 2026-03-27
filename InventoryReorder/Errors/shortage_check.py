"""Check shortages for _SHIP_2026-03-23 orders against current inventory."""
import requests, json, time
from collections import defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

# Build inventory from settings (live data from app)
_inv = settings.get("inventory", {})
inventory = {}
PICKABLE = ("CH-", "MT-", "AC-")
for sku, info in _inv.items():
    if any(sku.startswith(p) for p in PICKABLE):
        inventory[sku] = info.get("qty", 0) if isinstance(info, dict) else int(info)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

# Fetch all unfulfilled orders, filter by _SHIP_2026-03-23 tag
orders = []
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
    print(f"  Fetching page {page}...")
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    batch = resp.json().get("orders", [])
    for o in batch:
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" in tags:
            orders.append(o)
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"\nOrders with _SHIP_2026-03-23: {len(orders)}")

# Tally demand by SKU (only pickable: CH-, MT-, AC-)
demand = defaultdict(int)
for o in orders:
    for li in o.get("line_items", []):
        sku = (li.get("sku") or "").strip()
        qty = li.get("fulfillable_quantity", li.get("quantity", 0))
        if qty > 0 and any(sku.startswith(p) for p in PICKABLE):
            demand[sku] += qty

# Available = current inventory from settings
available = dict(inventory)

# Include open POs from settings if available
open_pos = settings.get("open_pos", [])
po = {}
for p in open_pos:
    if p.get("status", "").lower() in ("open", "ordered", "pending"):
        sku = p.get("sku", "")
        qty = p.get("qty", 0)
        if sku and qty > 0:
            po[sku] = po.get(sku, 0) + qty
            available[sku] = available.get(sku, 0) + qty

if po:
    print(f"Open POs included: {len(po)} SKUs, {sum(po.values())} units")

# Calculate shortages
shortages = []
ok_skus = []
for sku in sorted(set(demand.keys())):
    dmd = demand[sku]
    avail = available.get(sku, 0)
    gap = avail - dmd
    if gap < 0:
        shortages.append((sku, dmd, avail, gap))
    else:
        ok_skus.append((sku, dmd, avail, gap))

print(f"\nTotal pickable SKUs demanded: {len(demand)}")
print(f"Total units demanded: {sum(demand.values())}")

if shortages:
    print(f"\n{'='*60}")
    print(f"  SHORTAGES ({len(shortages)} SKUs)")
    print(f"{'='*60}")
    print(f"{'SKU':<16} {'Demand':>7} {'Avail':>7} {'Short':>7}")
    print("-" * 45)
    shortages.sort(key=lambda x: x[3])
    for sku, dmd, avail, gap in shortages:
        po_note = f"  (incl PO +{po[sku]})" if sku in po else ""
        print(f"{sku:<16} {dmd:>7} {avail:>7} {gap:>7}{po_note}")
else:
    print("\nNo shortages - all demand covered!")

print(f"\n{'='*60}")
print(f"  COVERED ({len(ok_skus)} SKUs)")
print(f"{'='*60}")
print(f"{'SKU':<16} {'Demand':>7} {'Avail':>7} {'Buffer':>7}")
print("-" * 45)
for sku, dmd, avail, gap in sorted(ok_skus, key=lambda x: x[3]):
    po_note = f"  (incl PO +{po[sku]})" if sku in po else ""
    print(f"{sku:<16} {dmd:>7} {avail:>7} {gap:>7}{po_note}")
