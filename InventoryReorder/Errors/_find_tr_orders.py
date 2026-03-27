"""Quick scan: find all unfulfilled orders with TR- SKUs."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

orders = []
url = f"{REST_BASE}/orders.json"
params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250,
          "fields": "id,name,customer,email,line_items"}
page = 0
while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    orders.extend(resp.json().get("orders", []))
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

found = 0
for o in orders:
    lis = o.get("line_items", [])
    tr = [l.get("sku", "") for l in lis if (l.get("sku") or "").startswith("TR-")]
    if tr:
        found += 1
        c = o.get("customer") or {}
        nm = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        skus = ", ".join(
            (l.get("sku") or "(blank)") + " x" + str(l.get("quantity", 1))
            for l in lis
        )
        print(f"{o.get('name', '')} | {nm} | {skus}")

print(f"---")
print(f"Total unfulfilled: {len(orders)}, with TR-: {found}")
