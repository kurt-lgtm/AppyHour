"""Fix 6 orders with double MT-SOP: change the swapped one (no _rc_bundle) to MT-CAPO."""
import requests, json, sys, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

COMMIT = "--commit" in sys.argv
TARGET_ORDERS = ["120540", "120348", "120062", "119387", "119322", "110025"]


def gql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(GQL_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


# Find $0 MT-CAPO variant
print("Looking up MT-CAPO variant...")
vdata = gql('{ productVariants(first: 5, query: "sku:MT-CAPO") { edges { node { id sku price product { title } } } } }')
capo_variants = []
for edge in vdata["productVariants"]["edges"]:
    n = edge["node"]
    if n["sku"] == "MT-CAPO":
        capo_variants.append(n)
        print(f"  {n['sku']}: ${n['price']} - {n['product']['title']} ({n['id']})")
capo_variants.sort(key=lambda v: float(v["price"]))
CAPO_GID = capo_variants[0]["id"]

# Find the orders
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
import requests as req

targets = []
for order_num in TARGET_ORDERS:
    resp = req.get(f"{REST_BASE}/orders.json", headers=HEADERS,
                   params={"name": order_num, "status": "any", "fields": "id,name,line_items"}, timeout=30)
    orders = resp.json().get("orders", [])
    if not orders:
        print(f"  #{order_num} not found")
        continue
    o = orders[0]
    # Find the non-curation MT-SOP (the one we swapped from BRAS)
    for li in o["line_items"]:
        sku = (li.get("sku") or "").strip()
        if sku != "MT-SOP":
            continue
        fq = li.get("fulfillable_quantity", li.get("quantity", 0))
        if fq <= 0:
            continue
        props = li.get("properties", []) or []
        prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
        if "_rc_bundle" not in prop_names:
            targets.append({
                "order_id": o["id"],
                "order_name": o["name"],
                "order_gid": f"gid://shopify/Order/{o['id']}",
            })
            break
    time.sleep(0.5)

print(f"\nFound {len(targets)} orders to fix\n")

mode = "COMMIT" if COMMIT else "DRY-RUN"
if not COMMIT:
    for t in targets:
        print(f"  {t['order_name']}: MT-SOP (swapped) -> MT-CAPO")
    print(f"\nDRY-RUN. Run with --commit to apply.")
    sys.exit(0)

s, f = 0, 0
for t in targets:
    print(f"\n  {t['order_name']}...")
    data = gql("""
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder { id lineItems(first: 50) { edges { node { id sku quantity } } } }
        userErrors { field message }
      }
    }""", {"id": t["order_gid"]})
    if data["orderEditBegin"]["userErrors"]:
        print(f"    FAILED: {data['orderEditBegin']['userErrors']}")
        f += 1
        continue
    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]

    # Find the second MT-SOP (non-curation one — we can't tell from calculated order,
    # so find any MT-SOP and check if there are two, take the last one)
    sop_nodes = [e["node"] for e in calc["lineItems"]["edges"]
                 if (e["node"].get("sku") or "").strip() == "MT-SOP" and e["node"]["quantity"] > 0]
    if len(sop_nodes) < 2:
        print(f"    Only {len(sop_nodes)} MT-SOP found, skipping")
        f += 1
        continue
    # Take the last one (the swapped one)
    li_node = sop_nodes[-1]

    time.sleep(0.3)
    data = gql("""
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) { calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "lineItemId": li_node["id"], "quantity": 0})
    if data["orderEditSetQuantity"]["userErrors"]:
        print(f"    FAILED remove: {data['orderEditSetQuantity']['userErrors']}")
        f += 1
        continue

    time.sleep(0.3)
    data = gql("""
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) { calculatedLineItem { id } calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "variantId": CAPO_GID, "quantity": 1})
    if data["orderEditAddVariant"]["userErrors"]:
        print(f"    FAILED add: {data['orderEditAddVariant']['userErrors']}")
        f += 1
        continue

    time.sleep(0.3)
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Fix double MT-SOP: swap duplicate to MT-CAPO") { order { id name } userErrors { field message } }
    }""", {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        print(f"    COMMIT FAILED: {data['orderEditCommit']['userErrors']}")
        f += 1
        continue
    print(f"    OK {t['order_name']}")
    s += 1
    time.sleep(0.5)

print(f"\nDone: {s} fixed, {f} failed")
