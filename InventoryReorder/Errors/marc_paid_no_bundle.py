"""List paid AC-MARC orders that are NOT from BL-SDB bundle."""
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
    "fields": "id,name,tags,line_items,email,customer",
}
page = 0
results = []

while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        has_sdb = False
        marc_paid = []
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if sku == "BL-SDB" and qty > 0:
                has_sdb = True
            if sku == "AC-MARC" and qty > 0:
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if "_rc_bundle" not in prop_names:
                    marc_paid.append(qty)
        if marc_paid and not has_sdb:
            customer = o.get("customer", {}) or {}
            name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
            email = o.get("email") or customer.get("email", "")
            results.append({
                "order": o["name"],
                "qty": sum(marc_paid),
                "customer": name,
                "email": email,
            })
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"Paid AC-MARC (no BL-SDB bundle): {len(results)} orders, {sum(r['qty'] for r in results)} units\n")
print(f"{'Order':<12} {'Qty':>3}  {'Customer':<25} {'Email'}")
print("-" * 80)
for r in results:
    print(f"{r['order']:<12} {r['qty']:>3}  {r['customer']:<25} {r['email']}")
