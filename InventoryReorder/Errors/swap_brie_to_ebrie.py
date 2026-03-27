"""Swap all CH-BRIE to CH-EBRIE on _SHIP_2026-03-23 orders.

Usage:
    python swap_brie_to_ebrie.py              # dry-run
    python swap_brie_to_ebrie.py --commit     # apply
"""
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
OLD_SKU = "CH-BRIE"
NEW_SKU = "CH-EBRIE"


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


def find_variant():
    query = """{ productVariants(first: 5, query: "sku:CH-EBRIE") { edges { node { id sku title price product { title } } } } }"""
    data = gql(query)
    variants = []
    for edge in data["productVariants"]["edges"]:
        node = edge["node"]
        if node["sku"] == NEW_SKU:
            variants.append(node)
            print(f"  Found {node['sku']}: ${node['price']} - {node['product']['title']} ({node['id']})")
    variants.sort(key=lambda v: float(v["price"]))
    return variants[0]["id"] if variants else None


def fetch_targets():
    targets = []
    url = f"{REST_BASE}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250, "fields": "id,name,tags,line_items"}
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if "_SHIP_2026-03-23" not in tags:
                continue
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                if sku != OLD_SKU:
                    continue
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty <= 0:
                    continue
                targets.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
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
    return targets


def swap_item(order_info, variant_gid):
    order_gid = order_info["order_gid"]
    data = gql("""
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder { id lineItems(first: 50) { edges { node { id sku quantity } } } }
        userErrors { field message }
      }
    }""", {"id": order_gid})
    if data["orderEditBegin"]["userErrors"]:
        print(f"    FAILED: {data['orderEditBegin']['userErrors']}")
        return False
    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]
    li_node = None
    for edge in calc["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == OLD_SKU and node["quantity"] > 0:
            li_node = node
            break
    if not li_node:
        return False
    time.sleep(0.3)
    data = gql("""
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) { calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "lineItemId": li_node["id"], "quantity": 0})
    if data["orderEditSetQuantity"]["userErrors"]:
        return False
    time.sleep(0.3)
    data = gql("""
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) { calculatedLineItem { id } calculatedOrder { id } userErrors { field message } }
    }""", {"id": calc_id, "variantId": variant_gid, "quantity": li_node["quantity"]})
    if data["orderEditAddVariant"]["userErrors"]:
        return False
    time.sleep(0.3)
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap CH-BRIE -> CH-EBRIE (out of stock)") { order { id name } userErrors { field message } }
    }""", {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        return False
    print(f"    OK {order_info['order_name']}")
    return True


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Swap {OLD_SKU} -> {NEW_SKU} [{mode}]")
    print(f"{'='*60}\n")
    print("Looking up CH-EBRIE variant...")
    vgid = find_variant()
    if not vgid:
        print("  ERROR: variant not found!")
        return
    print("\nFetching orders...")
    targets = fetch_targets()
    print(f"  Found {len(targets)} orders\n")
    if not COMMIT:
        print(f"DRY-RUN. {len(targets)} would be swapped.")
        print("Run with --commit to apply.")
        return
    s, f = 0, 0
    for t in targets:
        if swap_item(t, vgid):
            s += 1
        else:
            f += 1
        time.sleep(0.5)
    print(f"\n{'='*60}")
    print(f"  Done: {s} swapped, {f} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
