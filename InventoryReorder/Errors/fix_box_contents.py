"""Fix orders to match customer box_contents selections.

Uses $0 variants only. Removes wrong curation items, adds correct ones.
"""
import requests, json, time, sys

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

FOOD_PREFIXES = ("CH-", "MT-", "AC-", "CEX-EC", "EX-EA")
KEEP_PREFIXES = ("AHB-", "PR-CJAM", "PK-", "BL-", "CEX-EC-")

# $0 variant GIDs (always use these)
VARIANT_MAP = {
    "MT-SOP":    "gid://shopify/ProductVariant/49467543257368",
    "AC-MUSTCH": "gid://shopify/ProductVariant/47413507850520",
    "AC-SMAL":   "gid://shopify/ProductVariant/48786762727704",
    "AC-ACRISP": "gid://shopify/ProductVariant/50600430043416",
    "CEX-EC":    "gid://shopify/ProductVariant/50668365709592",
    "MT-BRAS":   "gid://shopify/ProductVariant/50834764857624",
    "CH-FOWC":   "gid://shopify/ProductVariant/49752032968984",
    "MT-JAHH":   "gid://shopify/ProductVariant/49611715543320",
    "AC-MARC":   "gid://shopify/ProductVariant/51507472269592",
    "AC-RBOL":   "gid://shopify/ProductVariant/49724016296216",
    "AC-HON":    "gid://shopify/ProductVariant/50034669650200",
    "CH-KM39":   "gid://shopify/ProductVariant/50689080557848",
    "CH-FONTAL": "gid://shopify/ProductVariant/51196926394648",
    "MT-SPAP":   "gid://shopify/ProductVariant/50739874693400",
    "CH-SOT":    "gid://shopify/ProductVariant/50896457040152",
    "CH-TOPR":   "gid://shopify/ProductVariant/49611765678360",
    "MT-SFEN":   "gid://shopify/ProductVariant/50431272485144",
    "MT-PRO":    "gid://shopify/ProductVariant/49125018829080",
    "AC-SDF":    "gid://shopify/ProductVariant/49773465370904",
    "EX-EA":     "gid://shopify/ProductVariant/47613564748056",
}

# Orders to fix with their target items (from box_contents)
ORDERS = [
    {
        "name": "#117915",
        "target": {"MT-SOP": 1, "AC-MUSTCH": 2, "AC-SMAL": 1, "AC-ACRISP": 1,
                   "CEX-EC": 1, "MT-BRAS": 1, "CH-FOWC": 1, "MT-JAHH": 1},
    },
    {
        "name": "#116952",
        "target": {"AC-MARC": 1, "AC-RBOL": 1, "AC-HON": 1, "CH-KM39": 1,
                   "CH-FONTAL": 1, "MT-SPAP": 1, "MT-JAHH": 1},
    },
    {
        "name": "#116846",
        "target": {"CH-SOT": 1, "CH-TOPR": 1, "MT-SFEN": 1, "MT-PRO": 1,
                   "AC-SDF": 1, "AC-RBOL": 1, "EX-EA": 1},
    },
]


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


def is_food(sku):
    return any(sku.startswith(p) for p in FOOD_PREFIXES)


def is_keep(sku):
    return any(sku.startswith(p) for p in KEEP_PREFIXES)


def fix_order(order_info, commit=False):
    order_name = order_info["name"]
    target = order_info["target"]

    # Get Shopify order
    resp = requests.get(f"{REST_BASE}/orders.json", headers=HEADERS, params={
        "name": order_name, "status": "any", "fields": "id,name,line_items"
    }, timeout=30)
    order = [o for o in resp.json()["orders"] if o["name"] == order_name]
    if not order:
        print(f"  {order_name}: NOT FOUND")
        return False
    order = order[0]
    order_id = order["id"]
    order_gid = f"gid://shopify/Order/{order_id}"

    # Current food items (qty > 0, not keep-prefixed)
    current_food = {}
    for li in order["line_items"]:
        sku = (li.get("sku") or "").strip()
        qty = li.get("quantity", 0)
        if qty > 0 and is_food(sku) and not is_keep(sku):
            current_food[sku] = current_food.get(sku, 0) + qty

    # What to remove and add
    to_remove = [sku for sku in current_food if sku not in target]
    to_add = {sku: qty for sku, qty in target.items() if sku not in current_food}

    print(f"\n{'='*60}")
    print(f"{order_name} (ID: {order_id})")
    print(f"  Current food: {current_food}")
    print(f"  Target:       {target}")
    print(f"  Remove: {to_remove}")
    print(f"  Add:    {to_add}")

    if not to_remove and not to_add:
        print(f"  Already correct!")
        return True

    if not commit:
        print(f"  DRY RUN - no changes")
        return True

    # Check variant IDs
    for sku in to_add:
        if sku not in VARIANT_MAP:
            print(f"  MISSING variant for {sku}!")
            return False

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

    # Build sku -> calc line items
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
            print(f"  {sku} not in calc order, skip")
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

    # Add correct items ($0 variants)
    for sku, qty in to_add.items():
        vid = VARIANT_MAP[sku]
        for _ in range(qty):
            try:
                data = gql("""
                mutation($id: ID!, $variantId: ID!, $quantity: Int!) {
                  orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity) {
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
          orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Fix Class 6: replace items to match customer box_contents selection") {
            order { id name }
            userErrors { field message }
          }
        }""", {"id": calc_id})
        errors = data["orderEditCommit"]["userErrors"]
        if errors:
            print(f"  COMMIT FAILED: {errors}")
            return False
        else:
            print(f"  COMMITTED {order_name}")
            return True
    else:
        print(f"  CANCELLED {order_name} due to errors")
        return False


def main():
    commit = "--commit" in sys.argv

    single = None
    if "--single" in sys.argv:
        idx = sys.argv.index("--single")
        single = sys.argv[idx + 1]

    success = 0
    failed = 0
    for order_info in ORDERS:
        if single and order_info["name"] != f"#{single}":
            continue
        if fix_order(order_info, commit=commit):
            success += 1
        else:
            failed += 1

    print(f"\n{'='*40}")
    mode = "COMMITTED" if commit else "DRY RUN"
    print(f"{mode}: {success} ok, {failed} failed")
    if not commit:
        print("Use --commit to apply changes.")


if __name__ == "__main__":
    main()
