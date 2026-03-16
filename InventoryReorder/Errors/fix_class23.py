"""Fix Class 2/3 Shopify orders: remove food items, add correct AHB- box SKU + PR-CJAM-GEN.

Class 2/3 orders have a promotional "AppyHour Box" product with blank SKU.
Food items were added by curation tool but shouldn't be there (monthly boxes
get food from monthly assignment, not curation).

Usage:
    python fix_class23.py              # dry-run: shows planned changes
    python fix_class23.py --commit     # applies changes via GraphQL order edits
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

# Orders to skip entirely
EXCLUDE_ORDERS = {118002}

# SKUs to keep (non-food, non-box)
KEEP_PREFIXES = ("PR-CJAM",)
# Food/item prefixes that Recharge curation adds
REMOVE_PREFIXES = ("CH-", "MT-", "AC-", "CEX-")

# Simple Bundles property names — items with these are from paid bundles, not Recharge
BUNDLE_PROPERTY_NAMES = {"_bundle_id", "_bundled_by", "_bundle_product_id"}
# Box SKUs that mean order already has a box
EXISTING_BOX_SKUS = {"AHB-MCUST", "AHB-LCUST", "AHB-MED", "AHB-LGE", "AHB-CMED"}

# Box SKU mapping from variant title
BOX_MAP = [
    (lambda t: "cheese" in t.lower() and ("medium" in t.lower() or "8 item" in t.lower()), "AHB-CMED"),
    (lambda t: "10 item" in t.lower() or "large" in t.lower(), "AHB-LGE"),
    (lambda t: "8 item" in t.lower() or "medium" in t.lower(), "AHB-MED"),
]


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


def lookup_variant_ids():
    """Look up variant IDs for AHB-MED, AHB-LGE, AHB-CMED, PR-CJAM-GEN by SKU."""
    query = """
    {
      productVariants(first: 10, query: "sku:AHB-MED OR sku:AHB-LGE OR sku:AHB-CMED OR sku:PR-CJAM-GEN") {
        edges {
          node {
            id
            sku
            title
            product { title }
          }
        }
      }
    }
    """
    data = gql(query)
    result = {}
    for edge in data["productVariants"]["edges"]:
        node = edge["node"]
        sku = node["sku"]
        if sku in ("AHB-MED", "AHB-LGE", "AHB-CMED", "PR-CJAM-GEN"):
            result[sku] = node["id"]
            print(f"  Found {sku}: {node['id']} ({node['product']['title']} / {node['title']})")
    missing = {"AHB-MED", "AHB-LGE", "AHB-CMED", "PR-CJAM-GEN"} - set(result.keys())
    if missing:
        print(f"  WARNING: Could not find variants for: {missing}")
    return result


def determine_box_sku(variant_title):
    """Determine box SKU from the promo product variant title."""
    for test_fn, sku in BOX_MAP:
        if test_fn(variant_title):
            return sku
    return None


def find_class23_orders(orders):
    """Identify Class 2/3 orders and plan changes."""
    results = []
    for order in orders:
        order_name_raw = order.get("name", "#0").replace("#", "")
        # Order names can have letters (e.g. "113941A")
        order_number = int(re.sub(r"[^0-9]", "", order_name_raw) or "0")
        if order_number in EXCLUDE_ORDERS:
            continue

        tags = order.get("tags", "")
        if "reship" in tags.lower():
            continue

        line_items = order.get("line_items", [])
        if not line_items:
            continue

        # Find blank-SKU promo product
        promo_line = None
        for li in line_items:
            sku = (li.get("sku") or "").strip()
            title = (li.get("title") or "")
            if ("appyhour box" in title.lower() or "appy hour" in title.lower()) and not sku:
                promo_line = li
                break

        if not promo_line:
            continue

        # Skip if order already has a real box SKU
        has_box = any(
            (li.get("sku") or "").strip().startswith("AHB-")
            for li in line_items
        )
        if has_box:
            continue

        # Determine box SKU from variant title
        variant_title = promo_line.get("variant_title", "") or ""
        box_sku = determine_box_sku(variant_title)

        # Classify line items
        to_remove = []
        to_keep = []
        has_pr_cjam = False
        for li in line_items:
            sku = (li.get("sku") or "").strip()
            li_id = li["id"]
            qty = li.get("quantity", 1)
            title = li.get("title", "")

            if not sku:
                # Remove blank-SKU promo product (would double-charge with the real AHB- variant)
                li_title = li.get("title", "")
                if "appyhour box" in li_title.lower() or "appy hour" in li_title.lower():
                    to_remove.append({"id": li_id, "sku": "(blank)", "qty": qty, "title": li_title})
                continue
            if sku.startswith(KEEP_PREFIXES):
                has_pr_cjam = True
                continue

            # Check line item properties to determine origin
            props = li.get("properties") or []
            prop_names = {p.get("name", "") for p in props}

            # _rc_bundle = added by Recharge curation tool → remove
            is_recharge_curation = "_rc_bundle" in prop_names

            # Items WITHOUT _rc_bundle are from paid bundles, Simple Bundles,
            # or customer-added extras → keep
            if not is_recharge_curation and sku.startswith(REMOVE_PREFIXES):
                reason = "bundle" if sku.startswith(("BL-", "PK-", "EX-")) else "paid/extra"
                to_keep.append({"sku": sku, "qty": qty, "title": title, "reason": reason})
                continue

            if is_recharge_curation and sku.startswith(REMOVE_PREFIXES):
                to_remove.append({"id": li_id, "sku": sku, "qty": qty, "title": title})

        to_add = []
        if box_sku:
            to_add.append(box_sku)
        if not has_pr_cjam:
            to_add.append("PR-CJAM-GEN")

        customer = order.get("customer") or {}
        cust_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()

        results.append({
            "order_id": order["id"],
            "order_name": order.get("name", ""),
            "customer": cust_name,
            "email": order.get("email", ""),
            "variant_title": variant_title,
            "box_sku": box_sku,
            "to_remove": to_remove,
            "to_keep": to_keep,
            "to_add": to_add,
            "has_pr_cjam": has_pr_cjam,
        })

    return results


def print_plan(results):
    """Print dry-run summary."""
    print(f"\n{'='*80}")
    print(f"CLASS 2/3 ORDERS: {len(results)} found")
    print(f"{'='*80}\n")

    for r in results:
        print(f"Order {r['order_name']} | {r['customer']} | {r['email']}")
        print(f"  Variant: \"{r['variant_title']}\" -> Box SKU: {r['box_sku'] or 'UNKNOWN'}")
        if r['to_keep']:
            skus = ", ".join(f"{x['sku']} (x{x['qty']}, {x['reason']})" for x in r['to_keep'])
            print(f"  KEEP: {skus}")
        if r['to_remove']:
            skus = ", ".join(f"{x['sku']} (x{x['qty']})" for x in r['to_remove'])
            print(f"  REMOVE: {skus}")
        else:
            print(f"  REMOVE: (nothing to remove)")
        if r['to_add']:
            print(f"  ADD: {', '.join(r['to_add'])}")
        else:
            print(f"  ADD: (nothing to add)")
        if not r['box_sku']:
            print(f"  *** WARNING: Could not determine box SKU from variant title!")
        print()


def apply_changes(results, variant_ids):
    """Apply changes via Shopify GraphQL order edit mutations."""
    success = 0
    failed = 0

    for r in results:
        order_name = r['order_name']
        order_gid = f"gid://shopify/Order/{r['order_id']}"

        if not r['box_sku']:
            print(f"  SKIP {order_name}: Cannot determine box SKU")
            failed += 1
            continue

        if r['box_sku'] not in variant_ids:
            print(f"  SKIP {order_name}: No variant ID for {r['box_sku']}")
            failed += 1
            continue

        for add_sku in r['to_add']:
            if add_sku not in variant_ids:
                print(f"  SKIP {order_name}: No variant ID for {add_sku}")
                failed += 1
                continue

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
                    originalUnitPriceSet { shopMoney { amount } }
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

        # Build map of calculated line item IDs from the edit session
        # Key by SKU, but blank SKUs get keyed as "(blank)"
        calc_line_items = {}
        for edge in calc_order["lineItems"]["edges"]:
            node = edge["node"]
            key = node["sku"] or "(blank)"
            calc_line_items[key] = calc_line_items.get(key, [])
            price = 0.0
            try:
                price = float(node["originalUnitPriceSet"]["shopMoney"]["amount"])
            except (KeyError, TypeError):
                pass
            calc_line_items[key].append({
                "id": node["id"],
                "qty": node["quantity"],
                "title": node["title"],
                "price": price,
            })

        time.sleep(0.5)

        # Step 2: Remove line items — track price removed for discount calc
        edit_ok = True
        price_removed = 0.0
        for item in r['to_remove']:
            sku = item['sku']

            # Find matching calculated line item
            if sku in calc_line_items and calc_line_items[sku]:
                calc_li = calc_line_items[sku].pop(0)
                calc_li_id = calc_li["id"]
                remove_qty = calc_li["qty"]
            else:
                print(f"    WARNING: Could not find calculated line item for {sku}, skipping")
                continue

            # Skip items already at qty 0 (previously removed/refunded)
            if remove_qty == 0:
                print(f"    SKIP {sku} (already qty 0)")
                continue

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
                    "lineItemId": calc_li_id,
                    "quantity": 0,
                })
                errors = data["orderEditSetQuantity"]["userErrors"]
                if errors:
                    print(f"    FAILED to remove {sku}: {errors}")
                    edit_ok = False
                else:
                    print(f"    Removed {sku} x{remove_qty}")
                    price_removed += float(calc_li.get("price", 0)) * remove_qty
            except Exception as e:
                print(f"    FAILED to remove {sku}: {e}")
                edit_ok = False

            time.sleep(0.3)

        # Step 3: Add items — capture new line item IDs for discount
        added_line_items = {}  # sku -> calculated line item id
        for add_sku in r['to_add']:
            variant_gid = variant_ids[add_sku]
            add_query = """
            mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
              orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity) {
                calculatedLineItem { id }
                calculatedOrder { id }
                userErrors { field message }
              }
            }
            """
            try:
                data = gql(add_query, {
                    "id": calc_order_id,
                    "variantId": variant_gid,
                    "quantity": 1,
                })
                errors = data["orderEditAddVariant"]["userErrors"]
                if errors:
                    print(f"    FAILED to add {add_sku}: {errors}")
                    edit_ok = False
                else:
                    calc_li_id = data["orderEditAddVariant"]["calculatedLineItem"]["id"]
                    added_line_items[add_sku] = calc_li_id
                    print(f"    Added {add_sku}")
            except Exception as e:
                print(f"    FAILED to add {add_sku}: {e}")
                edit_ok = False

            time.sleep(0.3)

        # Step 3b: Apply discount on AHB- line item to keep subtotal at $79.00
        TARGET_SUBTOTAL = 79.00
        box_sku = r['box_sku']
        if edit_ok and box_sku in added_line_items and box_sku in variant_ids:
            # Look up variant price
            price_query = """
            query ($id: ID!) {
              productVariant(id: $id) { price }
            }
            """
            try:
                vdata = gql(price_query, {"id": variant_ids[box_sku]})
                box_price = float(vdata["productVariant"]["price"])
            except Exception:
                box_price = 89.00  # fallback

            discount_amount = box_price - TARGET_SUBTOTAL
            if discount_amount > 0:
                discount_query = """
                mutation orderEditAddLineItemDiscount($id: ID!, $lineItemId: ID!, $discount: OrderEditAppliedDiscountInput!) {
                  orderEditAddLineItemDiscount(id: $id, lineItemId: $lineItemId, discount: $discount) {
                    calculatedOrder { id }
                    userErrors { field message }
                  }
                }
                """
                try:
                    data = gql(discount_query, {
                        "id": calc_order_id,
                        "lineItemId": added_line_items[box_sku],
                        "discount": {
                            "description": "Promo box price adjustment",
                            "fixedValue": {"amount": f"{discount_amount:.2f}", "currencyCode": "USD"},
                        },
                    })
                    errors = data["orderEditAddLineItemDiscount"]["userErrors"]
                    if errors:
                        print(f"    FAILED to apply discount: {errors}")
                        edit_ok = False
                    else:
                        print(f"    Applied ${discount_amount:.2f} discount on {box_sku} (${box_price:.2f} -> ${TARGET_SUBTOTAL:.2f})")
                except Exception as e:
                    print(f"    FAILED to apply discount: {e}")
                    edit_ok = False
                time.sleep(0.3)

        # Step 4: Commit (or cancel if errors)
        if edit_ok:
            commit_query = """
            mutation orderEditCommit($id: ID!) {
              orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Fix Class 2/3: remove curation items, add box SKU + PR-CJAM-GEN") {
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
            # Cancel the edit
            cancel_query = """
            mutation orderEditCommit($id: ID!) {
              orderEditCommit(id: $id, staffNote: "Cancelled - errors during edit") {
                order { id }
                userErrors { field message }
              }
            }
            """
            print(f"    CANCELLED edit for {order_name} due to errors")
            failed += 1

        time.sleep(0.5)

    print(f"\n{'='*40}")
    print(f"RESULTS: {success} succeeded, {failed} failed")
    print(f"{'='*40}")


def main():
    commit = "--commit" in sys.argv
    # --single ORDER_NUM: only process one order (for testing)
    single_order = None
    if "--single" in sys.argv:
        idx = sys.argv.index("--single")
        single_order = int(sys.argv[idx + 1])

    print("Fetching unfulfilled Shopify orders...")
    orders = fetch_all_unfulfilled()
    print(f"Fetched {len(orders)} unfulfilled orders")

    print("\nIdentifying Class 2/3 orders...")
    results = find_class23_orders(orders)

    if single_order:
        results = [r for r in results if r["order_name"] == f"#{single_order}"]

    print_plan(results)

    if not results:
        print("No Class 2/3 orders found.")
        return

    if not commit:
        print("DRY RUN — no changes made. Use --commit to apply changes.")
        return

    print("\n*** COMMIT MODE — Looking up variant IDs...")
    variant_ids = lookup_variant_ids()

    required = set()
    for r in results:
        if r['box_sku']:
            required.add(r['box_sku'])
        for sku in r['to_add']:
            required.add(sku)

    missing = required - set(variant_ids.keys())
    if missing:
        print(f"\nERROR: Missing variant IDs for: {missing}")
        print("Cannot proceed without all variant IDs.")
        return

    print(f"\nApplying changes to {len(results)} orders...")
    confirm = input("Type 'yes' to confirm: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return

    apply_changes(results, variant_ids)


if __name__ == "__main__":
    main()
