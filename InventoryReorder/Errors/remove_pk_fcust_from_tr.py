"""Remove PK-FCUST line items from unfulfilled orders that contain TR- SKUs.

Usage:
    python remove_pk_fcust_from_tr.py              # dry-run: shows planned changes
    python remove_pk_fcust_from_tr.py --commit     # applies changes via GraphQL order edits
    python remove_pk_fcust_from_tr.py --single 12345  # dry-run single order
"""
import requests, json, sys, time, re

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}


def fetch_all_unfulfilled():
    """Fetch all unfulfilled orders via REST pagination."""
    orders = []
    url = f"{REST_BASE}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,created_at,customer,email,tags,line_items",
    }
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
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


def find_orders_to_fix(orders):
    """Find unfulfilled orders that have TR- SKUs AND PK-FCUST."""
    results = []
    for order in orders:
        line_items = order.get("line_items", [])
        if not line_items:
            continue

        has_tr = False
        pk_fcust_lines = []

        for li in line_items:
            sku = (li.get("sku") or "").strip()
            if sku.startswith("TR-"):
                has_tr = True
            if sku == "PK-FCUST":
                pk_fcust_lines.append({
                    "id": li["id"],
                    "sku": sku,
                    "qty": li.get("quantity", 1),
                    "title": li.get("title", ""),
                })

        if has_tr and pk_fcust_lines:
            customer = order.get("customer") or {}
            cust_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()

            # Collect all SKUs for context
            all_skus = [(li.get("sku") or "(blank)").strip() for li in line_items]

            results.append({
                "order_id": order["id"],
                "order_name": order.get("name", ""),
                "customer": cust_name,
                "email": order.get("email", ""),
                "pk_fcust_lines": pk_fcust_lines,
                "all_skus": all_skus,
            })

    return results


def print_plan(results):
    """Print dry-run summary."""
    print(f"\n{'='*80}")
    print(f"ORDERS WITH TR- AND PK-FCUST: {len(results)} found")
    print(f"{'='*80}\n")

    for r in results:
        print(f"Order {r['order_name']} | {r['customer']} | {r['email']}")
        print(f"  All SKUs: {', '.join(r['all_skus'])}")
        for pk in r['pk_fcust_lines']:
            print(f"  REMOVE: {pk['sku']} x{pk['qty']} — {pk['title']}")
        print()


def apply_changes(results):
    """Remove PK-FCUST via Shopify GraphQL order edit mutations."""
    success = 0
    failed = 0

    for r in results:
        order_name = r['order_name']
        order_gid = f"gid://shopify/Order/{r['order_id']}"

        print(f"\n  Editing {order_name}...")

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
            userErrors {
              field
              message
            }
          }
        }
        """
        try:
            data = gql(begin_query, {"id": order_gid})
        except Exception as e:
            print(f"    FAILED to begin edit: {e}")
            failed += 1
            continue

        edit_result = data["orderEditBegin"]
        if edit_result["userErrors"]:
            print(f"    FAILED: {edit_result['userErrors']}")
            failed += 1
            continue

        calc_order = edit_result["calculatedOrder"]
        calc_order_id = calc_order["id"]

        # Find PK-FCUST calculated line items
        pk_fcust_calc_items = []
        for edge in calc_order["lineItems"]["edges"]:
            node = edge["node"]
            if node["sku"] == "PK-FCUST" and node["quantity"] > 0:
                pk_fcust_calc_items.append({
                    "id": node["id"],
                    "qty": node["quantity"],
                    "title": node["title"],
                })

        if not pk_fcust_calc_items:
            print(f"    No PK-FCUST with qty>0 found in calculated order, skipping")
            failed += 1
            continue

        time.sleep(0.5)

        # Step 2: Set quantity to 0 for each PK-FCUST line
        edit_ok = True
        for calc_li in pk_fcust_calc_items:
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
                    "lineItemId": calc_li["id"],
                    "quantity": 0,
                })
                errors = data["orderEditSetQuantity"]["userErrors"]
                if errors:
                    print(f"    FAILED to remove PK-FCUST: {errors}")
                    edit_ok = False
                else:
                    print(f"    Removed PK-FCUST x{calc_li['qty']} — {calc_li['title']}")
            except Exception as e:
                print(f"    FAILED to remove PK-FCUST: {e}")
                edit_ok = False

            time.sleep(0.3)

        # Step 3: Commit or report failure
        if edit_ok:
            commit_query = """
            mutation orderEditCommit($id: ID!) {
              orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Remove PK-FCUST from TR- order") {
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
                    failed += 1
                else:
                    print(f"    COMMITTED {order_name}")
                    success += 1
            except Exception as e:
                print(f"    COMMIT FAILED: {e}")
                failed += 1
        else:
            print(f"    CANCELLED edit for {order_name} due to errors")
            failed += 1

        time.sleep(0.5)

    print(f"\n{'='*40}")
    print(f"RESULTS: {success} succeeded, {failed} failed")
    print(f"{'='*40}")


def main():
    commit = "--commit" in sys.argv
    single_order = None
    if "--single" in sys.argv:
        idx = sys.argv.index("--single")
        single_order = int(sys.argv[idx + 1])

    print("Fetching unfulfilled Shopify orders...")
    orders = fetch_all_unfulfilled()
    print(f"Fetched {len(orders)} unfulfilled orders")

    print("\nSearching for orders with TR- SKUs and PK-FCUST...")
    results = find_orders_to_fix(orders)

    if single_order:
        results = [r for r in results if r["order_name"] == f"#{single_order}"]

    print_plan(results)

    if not results:
        print("No matching orders found.")
        return

    if not commit:
        print("DRY RUN — no changes made. Use --commit to apply changes.")
        return

    print(f"\nApplying changes to {len(results)} orders...")
    confirm = input("Type 'yes' to confirm: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return

    apply_changes(results)


if __name__ == "__main__":
    main()
