"""Check if curated AC-MARC items were customer-chosen (box_contents) or default recipe."""
import requests, json, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

TARGET_SKU = "AC-MARC"

# Find curation AC-MARC orders (with _rc_bundle property)
print("Fetching orders...")
curation_orders = []
url = f"{REST_BASE}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0
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
            if sku != TARGET_SKU:
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" not in prop_names:
                continue  # skip paid items
            # Check for box_contents in REST properties
            box_contents = None
            for p in props:
                if isinstance(p, dict) and p.get("name") == "box_contents":
                    box_contents = p.get("value", "")
            curation_orders.append({
                "order_id": order_id if 'order_id' in dir() else o["id"],
                "order_name": o["name"],
                "gid": f"gid://shopify/Order/{o['id']}",
                "box_contents_rest": box_contents,
                "qty": qty,
            })
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"\nFound {len(curation_orders)} curated AC-MARC orders")

# Check box_contents via GraphQL for those without it in REST
customer_chosen = []
default_recipe = []

for o in curation_orders:
    if o["box_contents_rest"] and TARGET_SKU.lower() in o["box_contents_rest"].lower():
        customer_chosen.append(o["order_name"])
    elif o["box_contents_rest"] and "marcona" in o["box_contents_rest"].lower():
        customer_chosen.append(o["order_name"])
    elif o["box_contents_rest"]:
        # Has box_contents but MARC not in it — means default recipe added it
        default_recipe.append(o["order_name"])
    else:
        # No box_contents in REST — need to check GraphQL
        default_recipe.append(o["order_name"])

# For orders without box_contents, check via GraphQL (batch of 5)
needs_gql = [o for o in curation_orders if not o["box_contents_rest"]]
if needs_gql:
    print(f"\nChecking {len(needs_gql)} orders via GraphQL for box_contents...")
    customer_chosen_gql = []
    default_recipe_gql = []

    for o in needs_gql[:50]:  # limit to avoid rate limits
        query = """
        query ($id: ID!) {
          order(id: $id) {
            name
            lineItems(first: 30) {
              edges {
                node {
                  sku
                  customAttributes {
                    key
                    value
                  }
                }
              }
            }
          }
        }
        """
        try:
            data = requests.post(GQL_URL, headers=HEADERS,
                               json={"query": query, "variables": {"id": o["gid"]}}, timeout=30)
            result = data.json().get("data", {}).get("order", {})
            # Find box_contents on AHB- line item
            found_bc = False
            for edge in result.get("lineItems", {}).get("edges", []):
                node = edge["node"]
                sku = (node.get("sku") or "").strip()
                if sku.startswith("AHB-"):
                    attrs = node.get("customAttributes", [])
                    for a in attrs:
                        if a.get("key") == "box_contents":
                            bc = a.get("value", "")
                            if "marcona" in bc.lower() or "AC-MARC" in bc:
                                customer_chosen_gql.append(o["order_name"])
                                found_bc = True
                            else:
                                # box_contents exists but no MARC — customer didn't choose it
                                default_recipe_gql.append(o["order_name"])
                                found_bc = True
                            break
                    if found_bc:
                        break
            if not found_bc:
                # No box_contents at all — default recipe
                default_recipe_gql.append(o["order_name"])
        except Exception as e:
            print(f"  GQL error for {o['order_name']}: {e}")
            default_recipe_gql.append(o["order_name"])
        time.sleep(0.3)

    # Update totals
    # Remove GQL-checked orders from default_recipe and re-classify
    default_recipe = [n for n in default_recipe if n not in [o["order_name"] for o in needs_gql]]
    customer_chosen.extend(customer_chosen_gql)
    default_recipe.extend(default_recipe_gql)

print(f"\n{'='*50}")
print(f"  AC-MARC Curation Breakdown")
print(f"{'='*50}")
print(f"  Customer-chosen (box_contents): {len(customer_chosen)}")
print(f"  Default recipe (swappable):     {len(default_recipe)}")

if customer_chosen:
    print(f"\n  Customer-chosen orders:")
    for n in customer_chosen[:20]:
        print(f"    {n}")
    if len(customer_chosen) > 20:
        print(f"    ...+{len(customer_chosen)-20} more")
