"""Swap 20 OWC curation CH-FOWC to CH-MCPC. Over-request to handle locked orders."""
import requests, json, sys, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

COMMIT = "--commit" in sys.argv
TARGET_SUCCESS = 20


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


print("Looking up CH-MCPC variant...")
vdata = gql('{ productVariants(first: 5, query: "sku:CH-MCPC") { edges { node { id sku price product { title } } } } }')
variants = [e["node"] for e in vdata["productVariants"]["edges"] if e["node"]["sku"] == "CH-MCPC"]
variants.sort(key=lambda v: float(v["price"]))
MCPC_GID = variants[0]["id"]
print(f"  CH-MCPC: {MCPC_GID} ${variants[0]['price']}")

# Fetch all OWC curation CH-FOWC
print("\nFetching OWC curation CH-FOWC orders...")
targets = []
url = f"{REST_BASE}/orders.json"
params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items"}
page = 0
while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    for o in resp.json().get("orders", []):
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        is_owc = False
        has_fowc = False
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if fq <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if (sku.startswith("AHB-MCUST") or sku.startswith("AHB-LCUST")) and sku.endswith("OWC"):
                is_owc = True
            if sku == "CH-FOWC" and "_rc_bundle" in prop_names:
                has_fowc = True
        if is_owc and has_fowc:
            targets.append({
                "order_gid": f"gid://shopify/Order/{o['id']}",
                "order_name": o["name"],
            })
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"  Found {len(targets)} remaining OWC CH-FOWC orders")

if not COMMIT:
    print(f"\nDRY-RUN. Will attempt up to {len(targets)} to get {TARGET_SUCCESS} successes.")
    print("Run with --commit to apply.")
    sys.exit(0)

s, f = 0, 0
for t in targets:
    if s >= TARGET_SUCCESS:
        break
    data = gql("""
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder { id lineItems(first: 50) { edges { node { id sku quantity } } } }
        userErrors { field message }
      }
    }""", {"id": t["order_gid"]})
    if data["orderEditBegin"]["userErrors"]:
        f += 1; continue
    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]
    li_node = None
    for edge in calc["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == "CH-FOWC" and node["quantity"] > 0:
            li_node = node; break
    if not li_node:
        f += 1; continue
    time.sleep(0.3)
    data = gql("""
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) { calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "lineItemId": li_node["id"], "quantity": 0})
    if data["orderEditSetQuantity"]["userErrors"]:
        f += 1; continue
    time.sleep(0.3)
    data = gql("""
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) { calculatedLineItem { id } calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "variantId": MCPC_GID, "quantity": 1})
    if data["orderEditAddVariant"]["userErrors"]:
        f += 1; continue
    time.sleep(0.3)
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap CH-FOWC -> CH-MCPC (OWC, out of stock)") { order { id name } userErrors { field message } }
    }""", {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        f += 1; continue
    print(f"    OK {t['order_name']}")
    s += 1
    time.sleep(0.5)

print(f"\nDone: {s} swapped, {f} failed (stopped at {TARGET_SUCCESS} successes)")
