"""Find a customer-chosen CH-FOWC order example."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

url = f"{REST_BASE}/orders.json"
params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items,email,customer"}
page = 0
found = False
while url and not found:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        if found:
            break
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        has_fowc_curation = False
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if sku == "CH-FOWC" and fq > 0:
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if "_rc_bundle" in prop_names:
                    has_fowc_curation = True
        if not has_fowc_curation:
            continue
        # Check box_contents via GQL
        gid = f"gid://shopify/Order/{o['id']}"
        gql_resp = requests.post(GQL_URL, headers=HEADERS, json={"query": """
        query ($id: ID!) {
          order(id: $id) {
            lineItems(first: 30) {
              edges { node { sku customAttributes { key value } } }
            }
          }
        }""", "variables": {"id": gid}}, timeout=30)
        gql_data = gql_resp.json().get("data", {}).get("order", {})
        for edge in gql_data.get("lineItems", {}).get("edges", []):
            node = edge["node"]
            nsku = (node.get("sku") or "").strip()
            if nsku.startswith("AHB-"):
                for attr in node.get("customAttributes", []):
                    if attr.get("key") == "box_contents":
                        bc = attr.get("value", "")
                        if "clothbound" in bc.lower() or "fowc" in bc.lower():
                            # Found it
                            c = o.get("customer", {}) or {}
                            print(f"Order: {o['name']}")
                            print(f"Customer: {c.get('first_name','')} {c.get('last_name','')}")
                            print(f"Email: {o.get('email','')}")
                            print(f"Box: {nsku}")
                            print(f"\nbox_contents:")
                            for line in bc.split("\\n"):
                                print(f"  {line}")
                            print(f"\nLine Items:")
                            for li in o["line_items"]:
                                s = (li.get("sku") or "").strip() or "(blank)"
                                q = li.get("quantity", 0)
                                fq2 = li.get("fulfillable_quantity", q)
                                p = li.get("price", "0")
                                print(f"  {s:<18} {q:>2} ${p:>6} fq={fq2}")
                            found = True
                            break
        time.sleep(0.5)
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)
