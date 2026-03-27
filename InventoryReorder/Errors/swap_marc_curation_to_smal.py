"""Swap curation AC-MARC (default recipe, NOT customer-chosen) to AC-SMAL.

Only targets AC-MARC items with _rc_bundle property where the AHB- box does NOT
have box_contents mentioning marcona/AC-MARC (i.e., default recipe, swappable).

Usage:
    python swap_marc_curation_to_smal.py              # dry-run
    python swap_marc_curation_to_smal.py --commit     # apply
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
OLD_SKU = "AC-MARC"
NEW_SKU = "AC-SMAL"


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


def find_smal_variant():
    query = """
    {
      productVariants(first: 5, query: "sku:AC-SMAL") {
        edges {
          node { id sku title price product { title } }
        }
      }
    }
    """
    data = gql(query)
    variants = []
    for edge in data["productVariants"]["edges"]:
        node = edge["node"]
        if node["sku"] == NEW_SKU:
            variants.append(node)
            print(f"  Found {node['sku']}: ${node['price']} - {node['product']['title']} / {node['title']} ({node['id']})")
    variants.sort(key=lambda v: float(v["price"]))
    return variants[0]["id"] if variants else None


def fetch_curation_marc():
    """Find curation AC-MARC orders, then check box_contents via GQL to filter."""
    candidates = []
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
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if "_rc_bundle" not in prop_names:
                    continue  # only curation items
                candidates.append({
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

    print(f"  Found {len(candidates)} curation AC-MARC orders")

    # Check box_contents via GQL to exclude customer-chosen
    swappable = []
    customer_chosen = 0
    print(f"\n  Checking box_contents via GraphQL...")
    for o in candidates:
        query = """
        query ($id: ID!) {
          order(id: $id) {
            name
            lineItems(first: 30) {
              edges {
                node {
                  sku
                  customAttributes { key value }
                }
              }
            }
          }
        }
        """
        try:
            data = gql(query, {"id": o["order_gid"]})
            order_data = data.get("order", {})
            has_marc_in_bc = False
            for edge in order_data.get("lineItems", {}).get("edges", []):
                node = edge["node"]
                nsku = (node.get("sku") or "").strip()
                if nsku.startswith("AHB-"):
                    for attr in node.get("customAttributes", []):
                        if attr.get("key") == "box_contents":
                            bc = (attr.get("value") or "").lower()
                            if "marcona" in bc or "ac-marc" in bc:
                                has_marc_in_bc = True
                            break
                    break
            if has_marc_in_bc:
                customer_chosen += 1
            else:
                swappable.append(o)
        except Exception as e:
            print(f"    GQL error {o['order_name']}: {e}")
            # Skip on error — don't swap if uncertain
        time.sleep(0.3)

    print(f"  Customer-chosen (skip): {customer_chosen}")
    print(f"  Default recipe (swappable): {len(swappable)}")
    return swappable


def swap_item(order_info, smal_variant_gid):
    order_name = order_info["order_name"]
    order_gid = order_info["order_gid"]

    begin_query = """
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder {
          id
          lineItems(first: 50) {
            edges { node { id sku quantity } }
          }
        }
        userErrors { field message }
      }
    }
    """
    try:
        data = gql(begin_query, {"id": order_gid})
    except Exception as e:
        print(f"    FAILED begin: {e}")
        return False

    edit_result = data["orderEditBegin"]
    if edit_result["userErrors"]:
        print(f"    FAILED: {edit_result['userErrors']}")
        return False

    calc_order = edit_result["calculatedOrder"]
    calc_id = calc_order["id"]

    marc_li = None
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == OLD_SKU and node["quantity"] > 0:
            marc_li = node
            break

    if not marc_li:
        print(f"    {OLD_SKU} not found")
        return False

    time.sleep(0.3)

    # Remove AC-MARC
    remove_query = """
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
        calculatedOrder { id }
        userErrors { field message }
      }
    }
    """
    try:
        data = gql(remove_query, {"id": calc_id, "lineItemId": marc_li["id"], "quantity": 0})
        if data["orderEditSetQuantity"]["userErrors"]:
            print(f"    FAILED remove: {data['orderEditSetQuantity']['userErrors']}")
            return False
        print(f"    Removed {OLD_SKU} x{marc_li['quantity']}")
    except Exception as e:
        print(f"    FAILED remove: {e}")
        return False

    time.sleep(0.3)

    # Add AC-SMAL
    add_query = """
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
        calculatedLineItem { id }
        calculatedOrder { id }
        userErrors { field message }
      }
    }
    """
    try:
        data = gql(add_query, {"id": calc_id, "variantId": smal_variant_gid, "quantity": marc_li["quantity"]})
        if data["orderEditAddVariant"]["userErrors"]:
            print(f"    FAILED add: {data['orderEditAddVariant']['userErrors']}")
            return False
        print(f"    Added {NEW_SKU} x{marc_li['quantity']}")
    except Exception as e:
        print(f"    FAILED add: {e}")
        return False

    time.sleep(0.3)

    commit_query = """
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap AC-MARC -> AC-SMAL (default recipe, out of stock)") {
        order { id name }
        userErrors { field message }
      }
    }
    """
    try:
        data = gql(commit_query, {"id": calc_id})
        if data["orderEditCommit"]["userErrors"]:
            print(f"    COMMIT FAILED: {data['orderEditCommit']['userErrors']}")
            return False
        print(f"    COMMITTED {order_name}")
        return True
    except Exception as e:
        print(f"    COMMIT FAILED: {e}")
        return False


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Swap curation {OLD_SKU} -> {NEW_SKU} [{mode}]")
    print(f"{'='*60}\n")

    print("Looking up AC-SMAL variant...")
    smal_gid = find_smal_variant()
    if not smal_gid:
        print("  ERROR: Could not find AC-SMAL variant!")
        return

    print("\nFetching curation AC-MARC orders...")
    targets = fetch_curation_marc()

    if not targets:
        print("\nNo swappable orders found.")
        return

    print(f"\n{'Order':<12} {'Qty':>4}")
    print("-" * 20)
    for t in targets:
        print(f"{t['order_name']:<12} {t['qty']:>4}")

    if not COMMIT:
        print(f"\nDRY-RUN. {len(targets)} orders would be swapped.")
        print("Run with --commit to apply.")
        return

    success = 0
    failed = 0
    for t in targets:
        print(f"\n  Editing {t['order_name']}...")
        if swap_item(t, smal_gid):
            success += 1
        else:
            failed += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {success} swapped, {failed} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
