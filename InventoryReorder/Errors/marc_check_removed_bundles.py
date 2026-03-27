"""Check how many 'standalone' AC-MARC orders have a BL- item at qty 0 (removed bundle)."""
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
    "fields": "id,name,tags,line_items,email,customer",
}
page = 0

has_removed_bl = []
has_active_bl = []  # shouldn't happen since we excluded these before
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

        # Check for AC-MARC with no curation props
        marc_paid = False
        for li in items:
            sku = (li.get("sku") or "").strip()
            if sku != "AC-MARC":
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" in prop_names:
                continue
            if "_parent_subscription_id" in prop_names:
                continue
            marc_paid = True

        if not marc_paid:
            continue

        # Skip if has active BL- or AHB-X
        has_active = any(
            ((li.get("sku") or "").strip().startswith("BL-") or
             (li.get("sku") or "").strip().startswith("AHB-X"))
            and li.get("fulfillable_quantity", li.get("quantity", 0)) > 0
            for li in items
        )
        if has_active:
            continue

        # Check for removed BL- or AHB-X (qty > 0 but fulfillable_quantity = 0, or just present)
        removed_bundles = []
        for li in items:
            sku = (li.get("sku") or "").strip()
            if sku.startswith("BL-") or sku.startswith("AHB-X"):
                qty = li.get("quantity", 0)
                fq = li.get("fulfillable_quantity", qty)
                price = li.get("price", "0")
                removed_bundles.append({
                    "sku": sku, "qty": qty, "fq": fq, "price": price
                })

        customer = o.get("customer", {}) or {}
        name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        info = {"order": o["name"], "customer": name, "email": o.get("email", "")}

        if removed_bundles:
            info["bundles"] = removed_bundles
            has_removed_bl.append(info)
        else:
            true_standalone.append(info)

    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"'Standalone' AC-MARC (no curation, no sub ID) breakdown:")
print(f"  Has removed/cancelled BL-/AHB-X: {len(has_removed_bl)}")
print(f"  True standalone (no bundle at all): {len(true_standalone)}")

if has_removed_bl:
    bundle_types = defaultdict(int)
    print(f"\n  Orders with removed bundles:")
    for o in has_removed_bl[:20]:
        bl_str = ", ".join(f"{b['sku']} qty={b['qty']} fq={b['fq']}" for b in o["bundles"])
        print(f"    {o['order']} {o['customer']:<25} -> {bl_str}")
        for b in o["bundles"]:
            bundle_types[b["sku"]] += 1
    if len(has_removed_bl) > 20:
        print(f"    ...+{len(has_removed_bl)-20} more")
    print(f"\n  By bundle SKU:")
    for sku, cnt in sorted(bundle_types.items(), key=lambda x: -x[1]):
        print(f"    {sku}: {cnt}")

if true_standalone:
    print(f"\n  True standalone orders (first 10):")
    for o in true_standalone[:10]:
        print(f"    {o['order']} {o['customer']:<25} {o['email']}")
    if len(true_standalone) > 10:
        print(f"    ...+{len(true_standalone)-10} more")
