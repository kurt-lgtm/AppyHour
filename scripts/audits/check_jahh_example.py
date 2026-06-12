"""Show example MT-JAHH line item from a Subscription First Order."""
import sys, os, re, time, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "AppyHourMCP"))
from utils import get_shopify_auth

base, headers = get_shopify_auth()

# Grab first matching order
all_orders = []
url = f"{base}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0
while url:
    page += 1
    resp = requests.get(url, headers=headers, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    all_orders.extend(resp.json().get("orders", []))
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if m:
            url = m.group(1)
    time.sleep(0.1)

for o in all_orders:
    tags = {t.strip() for t in o.get("tags", "").split(",")}
    if "Subscription First Order" in tags and "RMFG_20260410" in tags:
        for li in o.get("line_items", []):
            if li.get("sku") == "MT-JAHH":
                print(f"Order: {o['name']}")
                print(f"SKU: {li['sku']}")
                print(f"Title: {li['name']}")
                print(f"Price: ${li['price']}")
                print(f"Qty: {li['quantity']} | FQ: {li['fulfillable_quantity']}")
                print(f"Properties: {li.get('properties', [])}")
                print(f"Variant ID: {li.get('variant_id')}")
                print()
                # Also show all line items for context
                print("All line items on this order:")
                for li2 in o["line_items"]:
                    sku2 = li2.get("sku", "")
                    props2 = [p.get("name","") for p in (li2.get("properties") or [])]
                    print(f"  {sku2:20s} ${li2['price']:>6s} fq={li2['fulfillable_quantity']} props={props2}")
                break
        break
