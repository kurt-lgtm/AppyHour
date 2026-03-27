"""Check if the 105 paid AC-MARC add-ons come from any bundle (BL- SKU or _bundle_id property)."""
import requests, json, time
from collections import defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

BUNDLE_PROPS = {"_bundle_id", "_bundled_by", "_bundle_product_id"}

url = f"{REST_BASE}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0
from_bundle = []
standalone = []

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue

        # Collect all BL- SKUs on the order
        bl_skus = []
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if sku.startswith("BL-") and qty > 0 and sku != "BL-SDB":
                bl_skus.append(sku)

        # Check AC-MARC items
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku != "AC-MARC":
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" in prop_names:
                continue  # skip curation

            # Check if this order has BL-SDB
            has_sdb = any(
                (li2.get("sku") or "").strip() == "BL-SDB"
                and li2.get("fulfillable_quantity", li2.get("quantity", 0)) > 0
                for li2 in o.get("line_items", [])
            )
            if has_sdb:
                continue  # already counted as BL-SDB

            # Check for bundle properties on the AC-MARC line item
            is_bundled = bool(BUNDLE_PROPS & prop_names)
            # Check for _parent_subscription_id
            has_parent = "_parent_subscription_id" in prop_names

            # Get all properties for inspection
            prop_dict = {p.get("name", ""): p.get("value", "") for p in props if isinstance(p, dict)}

            info = {
                "order": o["name"],
                "qty": qty,
                "price": li.get("price", "0"),
                "bundled": is_bundled,
                "has_parent_sub": has_parent,
                "bl_skus": bl_skus,
                "props": prop_dict,
            }

            if is_bundled or bl_skus:
                from_bundle.append(info)
            else:
                standalone.append(info)
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"Paid AC-MARC (non-SDB) breakdown:")
print(f"  From other bundles (BL- or _bundle_id): {len(from_bundle)}")
print(f"  Standalone paid add-ons: {len(standalone)}")

if from_bundle:
    print(f"\nBundle orders:")
    bundle_sources = defaultdict(int)
    for b in from_bundle:
        src = ", ".join(b["bl_skus"]) if b["bl_skus"] else "bundle props"
        bundle_sources[src] += 1
        print(f"  {b['order']} BL={b['bl_skus']} bundled={b['bundled']} props={b['props']}")
    print(f"\n  By bundle SKU:")
    for src, cnt in sorted(bundle_sources.items(), key=lambda x: -x[1]):
        print(f"    {src}: {cnt}")

if standalone:
    # Check what properties standalone items have
    prop_patterns = defaultdict(int)
    for s in standalone:
        key = str(sorted(s["props"].keys())) if s["props"] else "(no props)"
        prop_patterns[key] += 1
    print(f"\n  Standalone property patterns:")
    for pattern, cnt in sorted(prop_patterns.items(), key=lambda x: -x[1]):
        print(f"    {pattern}: {cnt}")
