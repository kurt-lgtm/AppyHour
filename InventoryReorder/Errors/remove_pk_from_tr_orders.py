"""Remove PK- line items from unfulfilled Shopify orders that contain TR- line items.

TR- and PK- are non-pickable SKU prefixes. Orders with TR- items should not
have PK- items (tasting guides).

Usage:
    python remove_pk_from_tr_orders.py              # dry-run: shows planned changes
    python remove_pk_from_tr_orders.py --commit     # applies changes via GraphQL order edits
    python remove_pk_from_tr_orders.py --single 12345  # dry-run single order by number
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
SINGLE = None
if "--single" in sys.argv:
    idx = sys.argv.index("--single")
    if idx + 1 < len(sys.argv):
        SINGLE = int(sys.argv[idx + 1])


def fetch_all_unfulfilled():
    """Fetch all unfulfilled orders via REST pagination."""
    orders = []
    url = f"{REST_BASE}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,created_at,line_items",
    }
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(
            url,
            headers=HEADERS,
            params=params if page == 1 else None,
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json().get("orders", [])
        orders.extend(batch)
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return orders


def gql(query, variables=None):
    """Execute a Shopify GraphQL query."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(GQL_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


def find_tr_orders_with_pk(orders):
    """Find orders that have TR- line items AND PK- line items to remove."""
    results = []
    for order in orders:
        order_name = order["name"]
        order_num_str = order["name"].replace("#", "")

        if SINGLE and order_num_str != str(SINGLE):
            continue

        line_items = order.get("line_items", [])

        has_tr = False
        pk_items = []

        for li in line_items:
            sku = (li.get("sku") or "").strip()
            fulfillable = li.get("fulfillable_quantity", li.get("quantity", 0))

            if fulfillable <= 0:
                continue

            if sku.startswith("TR-"):
                has_tr = True
            elif sku.startswith("PK-"):
                pk_items.append({
                    "sku": sku,
                    "title": li.get("title", ""),
                    "quantity": fulfillable,
                    "line_item_id": li["id"],
                })

        if has_tr and pk_items:
            results.append({
                "order_id": order["id"],
                "order_name": order_name,
                "order_gid": f"gid://shopify/Order/{order['id']}",
                "pk_items": pk_items,
            })

    return results


def remove_pk_items(order_info):
    """Remove PK- line items from an order via GraphQL order edit."""
    order_name = order_info["order_name"]
    order_gid = order_info["order_gid"]

    # Step 1: Begin order edit
    begin_query = """
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder {
          id
          lineItems(first: 50) {
            edges {
              node {
                id
                sku
                quantity
                title
              }
            }
          }
        }
        userErrors { field message }
      }
    }
    """
    try:
        data = gql(begin_query, {"id": order_gid})
    except Exception as e:
        print(f"    FAILED to begin edit: {e}")
        return False

    edit_result = data["orderEditBegin"]
    if edit_result["userErrors"]:
        print(f"    FAILED: {edit_result['userErrors']}")
        return False

    calc_order = edit_result["calculatedOrder"]
    calc_order_id = calc_order["id"]

    # Build map of calculated line items by SKU
    calc_pk_items = []
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        sku = (node.get("sku") or "").strip()
        if sku.startswith("PK-") and node["quantity"] > 0:
            calc_pk_items.append({
                "id": node["id"],
                "sku": sku,
                "qty": node["quantity"],
                "title": node["title"],
            })

    if not calc_pk_items:
        print(f"    No PK- items found in edit session for {order_name}, skipping")
        return False

    time.sleep(0.5)

    # Step 2: Set quantity to 0 for each PK- item
    edit_ok = True
    for item in calc_pk_items:
        remove_query = """
        mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
          orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
            calculatedOrder { id }
            userErrors { field message }
          }
        }
        """
        try:
            data = gql(remove_query, {
                "id": calc_order_id,
                "lineItemId": item["id"],
                "quantity": 0,
            })
            errors = data["orderEditSetQuantity"]["userErrors"]
            if errors:
                print(f"    FAILED to remove {item['sku']}: {errors}")
                edit_ok = False
            else:
                print(f"    Removed {item['sku']} x{item['qty']} ({item['title']})")
        except Exception as e:
            print(f"    FAILED to remove {item['sku']}: {e}")
            edit_ok = False

        time.sleep(0.3)

    # Step 3: Commit or cancel
    if edit_ok:
        commit_query = """
        mutation orderEditCommit($id: ID!) {
          orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Remove PK- tasting guide from TR- order") {
            order { id name }
            userErrors { field message }
          }
        }
        """
        try:
            data = gql(commit_query, {"id": calc_order_id})
            errors = data["orderEditCommit"]["userErrors"]
            if errors:
                print(f"    COMMIT FAILED: {errors}")
                return False
            else:
                print(f"    COMMITTED {order_name}")
                return True
        except Exception as e:
            print(f"    COMMIT FAILED: {e}")
            return False
    else:
        # Cancel the edit
        cancel_query = """
        mutation orderEditCommit($id: ID!) {
          orderEditCommit(id: $id, staffNote: "Cancelled - errors during PK removal") {
            order { id }
            userErrors { field message }
          }
        }
        """
        try:
            gql(cancel_query, {"id": calc_order_id})
            print(f"    CANCELLED edit for {order_name}")
        except Exception:
            pass
        return False


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Remove PK- items from TR- orders [{mode}]")
    print(f"{'='*60}\n")

    print("Fetching unfulfilled orders...")
    orders = fetch_all_unfulfilled()
    print(f"  Found {len(orders)} unfulfilled orders\n")

    print("Scanning for TR- orders with PK- items...")
    targets = find_tr_orders_with_pk(orders)
    print(f"  Found {len(targets)} orders with TR- items AND PK- items\n")

    if not targets:
        print("Nothing to do.")
        return

    # Display summary
    print(f"{'Order':<12} {'PK- Items to Remove'}")
    print("-" * 60)
    for t in targets:
        pk_desc = ", ".join(f"{p['sku']} x{p['quantity']}" for p in t["pk_items"])
        print(f"{t['order_name']:<12} {pk_desc}")
    print()

    if not COMMIT:
        print(f"DRY-RUN complete. {len(targets)} orders would be edited.")
        print("Run with --commit to apply changes.")
        return

    # Execute edits
    success = 0
    failed = 0
    for t in targets:
        print(f"\n  Editing {t['order_name']}...")
        if remove_pk_items(t):
            success += 1
        else:
            failed += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {success} edited, {failed} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
