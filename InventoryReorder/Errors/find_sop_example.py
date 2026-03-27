"""Find a simple MT-SOP curation order example."""
import requests, json, time

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
found = 0
while url and found < 3:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        if found >= 3:
            break
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        has_sop_curation = False
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku != "MT-SOP":
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" in prop_names:
                has_sop_curation = True
        if not has_sop_curation:
            continue
        # Skip orders we already showed
        if o["name"] in ("#120467",):
            continue
        found += 1
        c = o.get("customer", {}) or {}
        print(f"Order: {o['name']}")
        print(f"Customer: {c.get('first_name','')} {c.get('last_name','')}")
        print(f"Email: {o.get('email','')}")
        print(f"Tags: {o.get('tags','')}")
        print()
        for li in o["line_items"]:
            sku = (li.get("sku") or "").strip() or "(blank)"
            qty = li.get("quantity", 0)
            fq = li.get("fulfillable_quantity", qty)
            price = li.get("price", "0")
            props = li.get("properties", []) or []
            hidden = [p["name"] for p in props if isinstance(p, dict) and str(p.get("name","")).startswith("_")]
            pstr = ", ".join(hidden) if hidden else "(none)"
            print(f"  {sku:<18} {qty:>2} ${price:>6} fq={fq}  {pstr}")
        print()
        break  # just one
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)
