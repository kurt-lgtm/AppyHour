"""Check where CH-TTBRIE demand comes from."""
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
params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items,email,customer"}
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
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku != "CH-TTBRIE":
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            price = li.get("price", "0")
            # Find box/bundle SKUs on this order
            boxes = []
            for li2 in o.get("line_items", []):
                s2 = (li2.get("sku") or "").strip()
                if s2.startswith("AHB-") or s2.startswith("BL-"):
                    boxes.append(s2)
            customer = o.get("customer", {}) or {}
            name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
            results.append({
                "order": o["name"],
                "customer": name,
                "qty": qty,
                "price": price,
                "curation": "_rc_bundle" in prop_names,
                "sub": "_parent_subscription_id" in prop_names,
                "boxes": boxes,
                "props": list(prop_names),
            })
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"CH-TTBRIE orders: {len(results)}\n")
print(f"{'Order':<12} {'Customer':<25} {'Qty':>3} {'Price':>7} {'Source':<15} {'Boxes'}")
print("-" * 90)
for r in results:
    src = "curation" if r["curation"] else "sub-addon" if r["sub"] else "one-time"
    print(f"{r['order']:<12} {r['customer']:<25} {r['qty']:>3} ${r['price']:>6} {src:<15} {', '.join(r['boxes'])}")
