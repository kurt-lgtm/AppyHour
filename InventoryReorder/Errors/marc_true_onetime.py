"""Find a true one-time purchase AC-MARC (no props at all) and show full order."""
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
found = None
while url and not found:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        # Skip orders with bundles
        has_bundle = any(
            ((li.get("sku") or "").strip().startswith("AHB-X") or
             (li.get("sku") or "").strip().startswith("BL-"))
            and li.get("fulfillable_quantity", li.get("quantity", 0)) > 0
            for li in o.get("line_items", [])
        )
        if has_bundle:
            continue
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku != "AC-MARC":
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            # True one-time: no _rc_bundle, no _parent_subscription_id
            if not prop_names or (prop_names - {"_SHIP", "_NEXT_BILLING_DATE"}):
                continue
            if "_rc_bundle" in prop_names or "_parent_subscription_id" in prop_names:
                continue
            # This is a true no-props one-time purchase
            found = o
            break
        if not found:
            # Also check for completely empty props
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                if sku != "AC-MARC":
                    continue
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty <= 0:
                    continue
                props = li.get("properties", []) or []
                if not props:
                    found = o
                    break
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

if found:
    o = found
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
        visible = [f"{p.get('name')}={str(p.get('value',''))[:30]}" for p in props if isinstance(p, dict) and not str(p.get("name","")).startswith("_")]
        hidden = [p.get("name") for p in props if isinstance(p, dict) and str(p.get("name","")).startswith("_")]
        prop_str = ", ".join(visible) if visible else "(none)"
        if hidden:
            prop_str += f" [{', '.join(hidden)}]"
        print(f"{sku:<20} {qty:>4} ${price:>7} {fq:>4}  {prop_str}")
else:
    print("No true one-time purchase AC-MARC found")
