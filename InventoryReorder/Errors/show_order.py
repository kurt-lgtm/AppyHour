"""Show full order details by order number."""
import requests, json, sys, time

ORDER_NUM = sys.argv[1] if len(sys.argv) > 1 else "119860"

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
H = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

resp = requests.get(f"{BASE}/orders.json", headers=H,
    params={"name": ORDER_NUM, "status": "any", "fields": "id,name,email,tags,line_items,customer"},
    timeout=30)
orders = resp.json().get("orders", [])
if not orders:
    print(f"Order #{ORDER_NUM} not found")
    exit()

o = orders[0]
c = o.get("customer", {}) or {}
print(f"Order: {o['name']}")
print(f"Customer: {c.get('first_name', '')} {c.get('last_name', '')}")
print(f"Email: {o.get('email', '')}")
print(f"Tags: {o.get('tags', '')}")
print(f"\n{'SKU':<20} {'Qty':>3} {'Price':>8} {'FQ':>3}  Properties")
print("-" * 75)
for li in o["line_items"]:
    sku = (li.get("sku") or "").strip() or "(blank)"
    qty = li.get("quantity", 0)
    fq = li.get("fulfillable_quantity", qty)
    price = li.get("price", "0")
    props = li.get("properties", []) or []
    visible = [f"{p['name']}={str(p.get('value',''))[:25]}" for p in props if isinstance(p, dict) and not str(p.get('name','')).startswith('_')]
    hidden = [p['name'] for p in props if isinstance(p, dict) and str(p.get('name','')).startswith('_')]
    pstr = ", ".join(visible) if visible else "(none)"
    if hidden:
        pstr += f" [{', '.join(hidden)}]"
    print(f"{sku:<20} {qty:>3} ${price:>7} {fq:>3}  {pstr}")
