"""Find orders tagged _SHIP_2026-03-23 with duplicate CH-LOU line items."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
H = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

TAG = "_SHIP_2026-03-23"
SKU = "CH-LOU"

print(f"Scanning orders tagged {TAG} for duplicate {SKU}...")

dupes = []
page_info = None
page = 0

while True:
    params = {
        "tag": TAG,
        "status": "any",
        "fields": "id,name,email,tags,line_items,refunds",
        "limit": 250,
    }
    if page_info:
        params = {"page_info": page_info, "limit": 250,
                  "fields": "id,name,email,tags,line_items,refunds"}

    resp = requests.get(f"{BASE}/orders.json", headers=H, params=params, timeout=30)
    resp.raise_for_status()
    orders = resp.json().get("orders", [])
    page += 1
    print(f"  Page {page}: {len(orders)} orders", end="\r")

    for o in orders:
        # Build refunded qty map
        refunded = {}
        for refund in o.get("refunds", []):
            for rli in refund.get("refund_line_items", []):
                lid = rli["line_item_id"]
                refunded[lid] = refunded.get(lid, 0) + rli["quantity"]

        sku_counts = {}
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku == SKU:
                net = li.get("quantity", 0) - refunded.get(li["id"], 0)
                if net > 0:
                    sku_counts[SKU] = sku_counts.get(SKU, 0) + net
        if sku_counts.get(SKU, 0) > 1:
            dupes.append({
                "order": o["name"],
                "id": o["id"],
                "email": o.get("email", ""),
                "qty": sku_counts[SKU],
            })

    # Pagination
    link = resp.headers.get("Link", "")
    if 'rel="next"' in link:
        import re
        m = re.search(r'page_info=([^&>]+)[^>]*>;\s*rel="next"', link)
        page_info = m.group(1) if m else None
    else:
        page_info = None

    if not orders or not page_info:
        break
    time.sleep(0.1)

print(f"\nDone. Scanned {page} page(s).")
print(f"\nOrders with duplicate {SKU}: {len(dupes)}")
for d in dupes:
    print(f"  {d['order']}  qty={d['qty']}  {d['email']}  (id={d['id']})")
