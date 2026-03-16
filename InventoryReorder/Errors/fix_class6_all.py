"""Fix all remaining Class 6 curation mismatches.

Uses box_contents (customer customization) when available, falls back to curation recipe.
Always uses $0 variants. Uses allowDuplicates for items already on order.

Usage:
    python fix_class6_all.py              # dry-run
    python fix_class6_all.py --commit     # apply changes
    python fix_class6_all.py --single 117913  # one order only
"""
import requests, json, sys, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

# Load plan and variant map
PLAN_FILE = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\class6_remaining_plan.json"
VARIANT_FILE = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\zero_variant_map.json"

with open(PLAN_FILE, encoding="utf-8") as f:
    PLAN = json.load(f)

with open(VARIANT_FILE, encoding="utf-8") as f:
    VARIANT_MAP = json.load(f)


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


def fix_order(order_plan, commit=False):
    name = order_plan["name"]
    order_gid = f"gid://shopify/Order/{order_plan['id']}"
    to_remove = order_plan["to_remove"]
    to_add = order_plan["to_add"]
    source = order_plan["source"]

    print(f"\n{'='*60}")
    print(f"{name} | {order_plan['box_sku']} | {source}")

    if not to_remove and not to_add:
        print(f"  Already correct!")
        return True

    if to_remove:
        print(f"  Remove: {to_remove}")
    if to_add:
        print(f"  Add:    {to_add}")

    # Check variant IDs
    for sku in to_add:
        if sku not in VARIANT_MAP:
            print(f"  MISSING variant for {sku}!")
            return False

    if not commit:
        print(f"  DRY RUN")
        return True

    # Begin edit
    data = gql("""
    mutation($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder {
          id
          lineItems(first: 50) { edges { node { id sku quantity } } }
        }
        userErrors { field message }
      }
    }""", {"id": order_gid})

    edit = data["orderEditBegin"]
    if edit["userErrors"]:
        print(f"  FAILED to begin: {edit['userErrors']}")
        return False

    calc_order = edit["calculatedOrder"]
    calc_id = calc_order["id"]

    calc_items = {}
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        sku = node["sku"] or ""
        if sku not in calc_items:
            calc_items[sku] = []
        calc_items[sku].append({"id": node["id"], "qty": node["quantity"]})

    time.sleep(0.5)

    # Remove wrong items
    edit_ok = True
    for sku in to_remove:
        if sku not in calc_items:
            print(f"  {sku} not found, skip")
            continue
        for cli in calc_items[sku]:
            if cli["qty"] == 0:
                print(f"  SKIP {sku} (already qty 0)")
                continue
            try:
                data = gql("""
                mutation($id: ID!, $lineItemId: ID!, $quantity: Int!) {
                  orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
                    calculatedOrder { id }
                    userErrors { field message }
                  }
                }""", {"id": calc_id, "lineItemId": cli["id"], "quantity": 0})
                errors = data["orderEditSetQuantity"]["userErrors"]
                if errors:
                    print(f"  FAILED remove {sku}: {errors}")
                    edit_ok = False
                else:
                    print(f"  Removed {sku}")
            except Exception as e:
                print(f"  FAILED remove {sku}: {e}")
                edit_ok = False
            time.sleep(0.3)

    # Add correct items ($0 variants, allowDuplicates)
    for sku, qty in to_add.items():
        vid = VARIANT_MAP[sku]
        for _ in range(qty):
            try:
                data = gql("""
                mutation($id: ID!, $variantId: ID!, $quantity: Int!) {
                  orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
                    calculatedLineItem { id }
                    calculatedOrder { id }
                    userErrors { field message }
                  }
                }""", {"id": calc_id, "variantId": vid, "quantity": 1})
                errors = data["orderEditAddVariant"]["userErrors"]
                if errors:
                    print(f"  FAILED add {sku}: {errors}")
                    edit_ok = False
                else:
                    print(f"  Added {sku} ($0)")
            except Exception as e:
                print(f"  FAILED add {sku}: {e}")
                edit_ok = False
            time.sleep(0.3)

    # Commit
    if edit_ok:
        data = gql("""
        mutation($id: ID!) {
          orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Fix Class 6: replace items to match correct curation/box_contents") {
            order { id name }
            userErrors { field message }
          }
        }""", {"id": calc_id})
        errors = data["orderEditCommit"]["userErrors"]
        if errors:
            print(f"  COMMIT FAILED: {errors}")
            return False
        else:
            print(f"  COMMITTED {name}")
            return True
    else:
        print(f"  CANCELLED {name} due to errors")
        return False


def main():
    commit = "--commit" in sys.argv
    single = None
    if "--single" in sys.argv:
        idx = sys.argv.index("--single")
        single = int(sys.argv[idx + 1])

    success = 0
    failed = 0
    skipped = 0

    for order_plan in PLAN:
        if single and order_plan["name"] != f"#{single}":
            continue
        if not order_plan["to_remove"] and not order_plan["to_add"]:
            skipped += 1
            continue
        if fix_order(order_plan, commit=commit):
            success += 1
        else:
            failed += 1

    print(f"\n{'='*40}")
    mode = "COMMITTED" if commit else "DRY RUN"
    print(f"{mode}: {success} ok, {failed} failed, {skipped} already correct")
    if not commit:
        print("Use --commit to apply changes.")


if __name__ == "__main__":
    main()
