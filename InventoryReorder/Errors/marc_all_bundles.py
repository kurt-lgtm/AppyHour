"""Re-scan 'standalone' AC-MARC checking for ALL AHB- and BL- SKUs on order."""
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

bundled = []
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

        # Check for paid AC-MARC (no _rc_bundle, no _parent_subscription_id)
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
            if "_rc_bundle" in prop_names or "_parent_subscription_id" in prop_names:
                continue
            marc_paid = True

        if not marc_paid:
            continue

        # Find ALL box/bundle SKUs on the order (any AHB- or BL-, active or removed)
        box_skus = []
        for li in items:
            sku = (li.get("sku") or "").strip()
            qty = li.get("quantity", 0)
            fq = li.get("fulfillable_quantity", qty)
            if not sku:
                continue
            # Regular subscription boxes (AHB-MCUST, AHB-LCUST, AHB-MED, AHB-LGE) — skip
            if sku.startswith("AHB-MCUST") or sku.startswith("AHB-LCUST"):
                continue
            if sku in ("AHB-MED", "AHB-LGE", "AHB-CMED"):
                continue
            # Specialty/bundle SKUs
            if sku.startswith("AHB-") or sku.startswith("BL-"):
                box_skus.append({"sku": sku, "qty": qty, "fq": fq})

        customer = o.get("customer", {}) or {}
        name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        info = {"order": o["name"], "customer": name, "email": o.get("email", "")}

        if box_skus:
            info["boxes"] = box_skus
            bundled.append(info)
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

print(f"Paid AC-MARC (no curation, no sub ID):")
print(f"  From specialty boxes / bundles: {len(bundled)}")
print(f"  True standalone (no box/bundle): {len(true_standalone)}")

if bundled:
    box_counts = defaultdict(int)
    print(f"\n  Orders with specialty boxes/bundles:")
    for o in bundled:
        box_str = ", ".join(f"{b['sku']} (qty={b['qty']} fq={b['fq']})" for b in o["boxes"])
        print(f"    {o['order']:<12} {o['customer']:<25} {box_str}")
        for b in o["boxes"]:
            box_counts[b["sku"]] += 1
    print(f"\n  By box/bundle SKU:")
    for sku, cnt in sorted(box_counts.items(), key=lambda x: -x[1]):
        print(f"    {sku}: {cnt}")

if true_standalone:
    print(f"\n  True standalone orders (first 10):")
    for o in true_standalone[:10]:
        print(f"    {o['order']:<12} {o['customer']:<25} {o['email']}")
    if len(true_standalone) > 10:
        print(f"    ...+{len(true_standalone)-10} more")
