"""Fix CH-ALPHA on Shopify _SHIP_2026-03-23: remove+refund if paid, swap to CH-HCGU if curation.

Usage:
    python fix_alpha.py              # dry-run
    python fix_alpha.py --commit     # apply
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
TARGET_SKU = "CH-ALPHA"
SWAP_SKU = "CH-HCGU"


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


def find_hcgu_variant():
    query = """
    {
      productVariants(first: 5, query: "sku:CH-HCGU") {
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
        if node["sku"] == SWAP_SKU:
            variants.append(node)
            print(f"  Found {node['sku']}: ${node['price']} - {node['product']['title']} / {node['title']} ({node['id']})")
    variants.sort(key=lambda v: float(v["price"]))
    return variants[0]["id"] if variants else None


def fetch_targets():
    paid = []
    curation = []
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
                if sku != TARGET_SKU:
                    continue
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty <= 0:
                    continue
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                is_curation = "_rc_bundle" in prop_names
                price = float(li.get("price", "0"))
                info = {
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                    "line_item_id": li["id"],
                    "qty": qty,
                    "price": price,
                }
                if is_curation:
                    curation.append(info)
                else:
                    paid.append(info)
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return paid, curation


def edit_order(order_info, action, hcgu_gid=None):
    """action: 'remove' or 'swap'"""
    order_name = order_info["order_name"]
    order_gid = order_info["order_gid"]

    data = gql("""
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
    """, {"id": order_gid})

    edit_result = data["orderEditBegin"]
    if edit_result["userErrors"]:
        print(f"    FAILED: {edit_result['userErrors']}")
        return False

    calc_order = edit_result["calculatedOrder"]
    calc_id = calc_order["id"]

    li_node = None
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == TARGET_SKU and node["quantity"] > 0:
            li_node = node
            break

    if not li_node:
        print(f"    {TARGET_SKU} not found")
        return False

    time.sleep(0.3)

    # Remove
    data = gql("""
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
        calculatedOrder { id }
        userErrors { field message }
      }
    }
    """, {"id": calc_id, "lineItemId": li_node["id"], "quantity": 0})
    if data["orderEditSetQuantity"]["userErrors"]:
        print(f"    FAILED remove: {data['orderEditSetQuantity']['userErrors']}")
        return False

    time.sleep(0.3)

    # Add replacement if swap
    if action == "swap" and hcgu_gid:
        data = gql("""
        mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
          orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
            calculatedLineItem { id }
            calculatedOrder { id }
            userErrors { field message }
          }
        }
        """, {"id": calc_id, "variantId": hcgu_gid, "quantity": li_node["quantity"]})
        if data["orderEditAddVariant"]["userErrors"]:
            print(f"    FAILED add: {data['orderEditAddVariant']['userErrors']}")
            return False
        time.sleep(0.3)

    staff_note = "Remove CH-ALPHA (paid, out of stock)" if action == "remove" else "Swap CH-ALPHA -> CH-HCGU (curation, out of stock)"
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "%s") {
        order { id name }
        userErrors { field message }
      }
    }
    """ % staff_note, {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        print(f"    COMMIT FAILED: {data['orderEditCommit']['userErrors']}")
        return False
    return True


def refund_order(order_info):
    """Issue refund for removed paid item."""
    order_id = order_info["order_id"]
    try:
        calc_resp = requests.post(
            f"{REST_BASE}/orders/{order_id}/refunds/calculate.json",
            headers=HEADERS,
            json={"refund": {"refund_line_items": [{"line_item_id": order_info["line_item_id"], "quantity": order_info["qty"]}]}},
            timeout=30)
        calc_resp.raise_for_status()
        transactions = calc_resp.json().get("refund", {}).get("transactions", [])
    except Exception as e:
        print(f"    REFUND CALC FAILED: {e}")
        return False

    time.sleep(0.3)

    try:
        ref_resp = requests.post(
            f"{REST_BASE}/orders/{order_id}/refunds.json",
            headers=HEADERS,
            json={"refund": {"notify": True, "refund_line_items": [{"line_item_id": order_info["line_item_id"], "quantity": order_info["qty"]}], "transactions": transactions}},
            timeout=30)
        ref_resp.raise_for_status()
        amt = sum(float(t.get("amount", 0)) for t in ref_resp.json().get("refund", {}).get("transactions", []))
        print(f"    REFUNDED ${amt:.2f}")
        return True
    except Exception as e:
        print(f"    REFUND FAILED: {e}")
        return False


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Fix {TARGET_SKU} [{mode}]")
    print(f"  Paid -> remove + refund")
    print(f"  Curation -> swap to {SWAP_SKU}")
    print(f"{'='*60}\n")

    print("Looking up CH-HCGU variant...")
    hcgu_gid = find_hcgu_variant()
    if not hcgu_gid:
        print("  ERROR: Could not find CH-HCGU variant!")
        return

    print("\nFetching orders...")
    paid, curation = fetch_targets()
    total_refund = sum(p["price"] * p["qty"] for p in paid)
    print(f"  Paid (remove+refund): {len(paid)} orders, ${total_refund:.2f}")
    print(f"  Curation (swap->HCGU): {len(curation)} orders")

    if paid:
        print(f"\n  Paid orders:")
        for p in paid:
            print(f"    {p['order_name']} x{p['qty']} ${p['price']:.2f}")

    if curation:
        print(f"\n  Curation orders:")
        for c in curation[:10]:
            print(f"    {c['order_name']} x{c['qty']}")
        if len(curation) > 10:
            print(f"    ...+{len(curation)-10} more")

    if not COMMIT:
        print(f"\nDRY-RUN. {len(paid)} remove+refund, {len(curation)} swap.")
        print("Run with --commit to apply.")
        return

    # Process paid: remove + refund
    print(f"\n--- Removing paid items ---")
    success_paid = 0
    for p in paid:
        print(f"\n  {p['order_name']}...")
        if edit_order(p, "remove"):
            print(f"    Removed {TARGET_SKU}")
            refund_order(p)
            success_paid += 1
        time.sleep(0.5)

    # Process curation: swap
    print(f"\n--- Swapping curation items ---")
    success_cur = 0
    for c in curation:
        print(f"\n  {c['order_name']}...")
        if edit_order(c, "swap", hcgu_gid):
            print(f"    Swapped {TARGET_SKU} -> {SWAP_SKU}")
            success_cur += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {success_paid}/{len(paid)} removed+refunded, {success_cur}/{len(curation)} swapped")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
