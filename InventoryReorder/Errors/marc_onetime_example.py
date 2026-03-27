"""Show an example one-time purchase AC-MARC order with all line items."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

# Fetch order #120989 (first standalone, no bundle, no curation)
resp = requests.get(f"{REST_BASE}/orders.json",
    headers=HEADERS,
    params={"name": "120989", "status": "any", "fields": "id,name,email,tags,line_items,customer"},
    timeout=30)
resp.raise_for_status()
orders = resp.json().get("orders", [])
if not orders:
    print("Order not found")
    exit()

o = orders[0]
customer = o.get("customer", {}) or {}
print(f"Order: {o['name']}")
print(f"Customer: {customer.get('first_name', '')} {customer.get('last_name', '')}")
print(f"Email: {o.get('email', '')}")
print(f"Tags: {o.get('tags', '')}")
print(f"\nLine Items:")
print(f"{'SKU':<20} {'Qty':>4} {'Price':>8} {'FQ':>4} {'Properties'}")
print("-" * 80)
for li in o.get("line_items", []):
    sku = (li.get("sku") or "").strip() or "(blank)"
    qty = li.get("quantity", 0)
    fq = li.get("fulfillable_quantity", qty)
    price = li.get("price", "0")
    props = li.get("properties", []) or []
    prop_str = ", ".join(f"{p.get('name')}={p.get('value','')[:30]}" for p in props if isinstance(p, dict) and not p.get("name","").startswith("_")) if props else "(none)"
    prop_hidden = [p.get("name") for p in props if isinstance(p, dict) and p.get("name","").startswith("_")]
    if prop_hidden:
        prop_str += f" [{', '.join(prop_hidden)}]"
    print(f"{sku:<20} {qty:>4} ${price:>7} {fq:>4}  {prop_str}")
