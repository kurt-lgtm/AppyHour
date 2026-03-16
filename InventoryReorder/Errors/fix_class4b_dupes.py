"""Fix Class 4B duplicate curation items — remove one copy of each duplicated food SKU.

These are orders where the Recharge curation tool wrote items twice (double-curation-write bug).
Only removes duplicates where BOTH copies have _rc_bundle property (true curation dupes).
Leaves one-time add-on purchases alone.

Usage:
    python fix_class4b_dupes.py              # dry-run
    python fix_class4b_dupes.py --commit     # apply changes
    python fix_class4b_dupes.py --single 117330  # one order only
"""
import requests, json, sys, time, re
from collections import Counter

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

FOOD_PREFIXES = ("CH-", "MT-", "AC-", "CEX-")


def fetch_all_unfulfilled():
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
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(GQL_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


def find_4b_orders(orders):
    results = []
    for order in orders:
        tags = order.get("tags", "")
        if "reship" in tags.lower():
            continue

        line_items = order.get("line_items", [])
        if not line_items:
            continue

        # Skip Class 2/3 (blank-SKU promo product)
        has_blank_promo = any(
            not (li.get("sku") or "").strip()
            and ("appyhour box" in (li.get("title") or "").lower() or "appy hour" in (li.get("title") or "").lower())
            for li in line_items
        )
        if has_blank_promo:
            continue

        # Collect food items with curation flag
        food_by_sku = {}  # sku -> list of {id, qty, is_curation}
        for li in line_items:
            sku = (li.get("sku") or "").strip()
            qty = li.get("quantity", 1)
            if qty == 0:
                continue
            if not sku.startswith(FOOD_PREFIXES):
                continue
            props = li.get("properties") or []
            is_curation = "_rc_bundle" in {p.get("name", "") for p in props}
            if sku not in food_by_sku:
                food_by_sku[sku] = []
            food_by_sku[sku].append({"id": li["id"], "qty": qty, "is_curation": is_curation})

        # Find curation-only duplicates
        to_remove = []
        for sku, items in food_by_sku.items():
            curation_items = [i for i in items if i["is_curation"]]
            non_curation_items = [i for i in items if not i["is_curation"]]

            # Only flag if 2+ curation copies and no non-curation copies
            # OR 2+ curation copies regardless (the extras are dupes)
            if len(curation_items) >= 2:
                # Keep the first curation copy, remove the rest
                for item in curation_items[1:]:
                    to_remove.append({"id": item["id"], "sku": sku, "qty": item["qty"]})

        if not to_remove:
            continue

        customer = order.get("customer") or {}
        cust_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()

        # Get box SKU for display
        box_sku = ""
        for li in line_items:
            s = (li.get("sku") or "").strip()
            if s.startswith("AHB-"):
                box_sku = s
                break

        results.append({
            "order_id": order["id"],
            "order_name": order.get("name", ""),
            "customer": cust_name,
            "email": order.get("email", ""),
            "box_sku": box_sku,
            "to_remove": to_remove,
        })

    return results


def print_plan(results):
    print(f"\n{'='*80}")
    print(f"CLASS 4B DUPLICATE CURATION ITEMS: {len(results)} orders")
    print(f"{'='*80}\n")

    for r in results:
        skus = ", ".join(f"{x['sku']} (x{x['qty']})" for x in r['to_remove'])
        print(f"Order {r['order_name']} | {r['customer']} | {r['box_sku']}")
        print(f"  REMOVE DUPES: {skus}")
        print()


def apply_changes(results):
    success = 0
    failed = 0

    for r in results:
        order_name = r['order_name']
        order_gid = f"gid://shopify/Order/{r['order_id']}"

        print(f"\n  Editing {order_name}...")

        # Begin edit
        begin_query = """
        mutation($id: ID!) {
          orderEditBegin(id: $id) {
            calculatedOrder {
              id
              lineItems(first: 50) {
                edges {
                  node {
                    id
                    sku
                    quantity
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
            failed += 1
            continue

        edit_result = data["orderEditBegin"]
        if edit_result["userErrors"]:
            print(f"    FAILED: {edit_result['userErrors']}")
            failed += 1
            continue

        calc_order = edit_result["calculatedOrder"]
        calc_order_id = calc_order["id"]

        # Build map: sku -> list of calculated line item IDs
        calc_line_items = {}
        for edge in calc_order["lineItems"]["edges"]:
            node = edge["node"]
            key = node["sku"] or "(blank)"
            if key not in calc_line_items:
                calc_line_items[key] = []
            calc_line_items[key].append({
                "id": node["id"],
                "qty": node["quantity"],
            })

        time.sleep(0.5)

        # Remove duplicate line items
        # For each SKU with dupes, skip the first calculated line item (keep it),
        # remove subsequent ones
        skus_to_remove = {}
        for item in r['to_remove']:
            sku = item['sku']
            if sku not in skus_to_remove:
                skus_to_remove[sku] = 0
            skus_to_remove[sku] += 1

        edit_ok = True
        for sku, remove_count in skus_to_remove.items():
            if sku not in calc_line_items:
                print(f"    WARNING: {sku} not found in calculated order, skipping")
                continue

            calc_items = calc_line_items[sku]
            # Skip first (keep it), remove the rest up to remove_count
            items_to_remove = [i for i in calc_items[1:] if i["qty"] > 0][:remove_count]

            for calc_li in items_to_remove:
                remove_query = """
                mutation($id: ID!, $lineItemId: ID!, $quantity: Int!) {
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
                        print(f"    FAILED to remove {sku}: {errors}")
                        edit_ok = False
                    else:
                        print(f"    Removed duplicate {sku}")
                except Exception as e:
                    print(f"    FAILED to remove {sku}: {e}")
                    edit_ok = False

                time.sleep(0.3)

        # Commit
        if edit_ok:
            commit_query = """
            mutation($id: ID!) {
              orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Fix Class 4B: remove duplicate curation items") {
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

    print("\nIdentifying Class 4B duplicate curation orders...")
    results = find_4b_orders(orders)

    if single_order:
        results = [r for r in results if r["order_name"] == f"#{single_order}"]

    print_plan(results)

    if not results:
        print("No Class 4B duplicate orders found.")
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
