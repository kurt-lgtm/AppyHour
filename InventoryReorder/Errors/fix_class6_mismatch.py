"""Fix Class 6 curation mismatch — replace wrong curation items with correct recipe.

Only fixes clean 1:1 mismatches where ALL curation items match a single wrong curation.
Removes wrong _rc_bundle items, adds correct recipe items.

Usage:
    python fix_class6_mismatch.py              # dry-run
    python fix_class6_mismatch.py --commit     # apply changes
    python fix_class6_mismatch.py --single 117313  # one order only
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

CURATION_RECIPES = settings.get("curation_recipes", {})
# Override: swap AC-LFOLIVE → AC-PPCM in MDT recipe
if "MDT" in CURATION_RECIPES:
    CURATION_RECIPES["MDT"] = [
        (("AC-PPCM" if s == "AC-LFOLIVE" else s), q)
        for s, q in CURATION_RECIPES["MDT"]
    ]
FOOD_PREFIXES = ("CH-", "MT-", "AC-", "CEX-")
CUSTOM_BOX_PREFIXES = ("AHB-MCUST", "AHB-LCUST")
PICKABLE_PREFIXES = ("CH-", "MT-", "AC-")

# Minimum match % to consider it a clean 1:1 swap
MIN_MATCH_PCT = 0.85

# Recharge API setup
RC_TOKEN = settings.get("recharge_api_token", "")
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "Accept": "application/json",
    "X-Recharge-Version": "2021-11",
}


def fetch_rc_charge(charge_id):
    """Fetch a single Recharge charge by ID. Returns charge dict or None."""
    if not RC_TOKEN or not charge_id:
        return None
    try:
        resp = requests.get(
            f"https://api.rechargeapps.com/charges/{charge_id}",
            headers=RC_HEADERS, timeout=30,
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("charge")
    except Exception:
        return None


def check_rc_items(order, expected_curation, actual_curation):
    """Check Recharge charge to determine if items were customer-chosen.

    Returns a dict:
      rc_status: "batch_issue" | "customer_chosen" | "rc_matches_expected" | "unknown"
      rc_items: set of pickable SKUs from the RC charge
      rc_charge_id: str
    """
    note_attrs = order.get("note_attributes") or []
    charge_id = None
    for attr in note_attrs:
        if attr.get("name") == "rc_charge_id":
            charge_id = attr.get("value")
            break

    if not charge_id:
        return {"rc_status": "unknown", "rc_items": set(), "rc_charge_id": ""}

    charge = fetch_rc_charge(charge_id)
    if not charge:
        return {"rc_status": "unknown", "rc_items": set(), "rc_charge_id": charge_id}

    # Extract pickable SKUs from RC charge line items
    rc_skus = set()
    for item in charge.get("line_items", []):
        sku = (item.get("sku") or "").strip()
        if sku and any(sku.startswith(p) for p in PICKABLE_PREFIXES):
            rc_skus.add(sku)

    expected_skus = set(s for s, q in CURATION_RECIPES.get(expected_curation, []))
    actual_skus = set(s for s, q in CURATION_RECIPES.get(actual_curation, []))

    if not rc_skus:
        return {"rc_status": "unknown", "rc_items": rc_skus, "rc_charge_id": charge_id}

    # RC has the correct recipe → only Shopify is wrong
    expected_overlap = len(rc_skus & expected_skus) / max(len(rc_skus), 1)
    if expected_overlap >= 0.85:
        return {"rc_status": "rc_matches_expected", "rc_items": rc_skus, "rc_charge_id": charge_id}

    # RC has the same wrong recipe → batch issue on Recharge side
    actual_overlap = len(rc_skus & actual_skus) / max(len(rc_skus), 1)
    if actual_overlap >= 0.85:
        return {"rc_status": "batch_issue", "rc_items": rc_skus, "rc_charge_id": charge_id}

    # RC items don't match either recipe → customer may have customized
    return {"rc_status": "customer_chosen", "rc_items": rc_skus, "rc_charge_id": charge_id}


def fetch_all_unfulfilled():
    orders = []
    url = f"{REST_BASE}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,created_at,customer,email,tags,line_items,note_attributes",
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


def get_curation_from_box(sku):
    parts = sku.split("-")
    if len(parts) >= 3:
        return parts[-1]
    return None


def lookup_variant_ids(skus_needed):
    """Look up variant IDs for a set of SKUs via GraphQL.

    Always prefers $0.00 variants (curation/bundle variants) over paid ones.
    """
    variant_map = {}       # sku -> variant GID
    variant_prices = {}    # sku -> price of currently selected variant
    # Query in batches (GraphQL query string length limit)
    sku_list = sorted(skus_needed)
    batch_size = 10
    for i in range(0, len(sku_list), batch_size):
        batch = sku_list[i:i + batch_size]
        query_str = " OR ".join(f"sku:{s}" for s in batch)
        query = """
        {
          productVariants(first: 50, query: "%s") {
            edges {
              node {
                id
                sku
                title
                price
                product { title }
              }
            }
          }
        }
        """ % query_str
        data = gql(query)
        for edge in data["productVariants"]["edges"]:
            node = edge["node"]
            sku = node["sku"]
            price = float(node.get("price", "999"))
            if sku in skus_needed:
                prev_price = variant_prices.get(sku, 999)
                # Always prefer the $0 variant
                if sku not in variant_map or price < prev_price:
                    variant_map[sku] = node["id"]
                    variant_prices[sku] = price
                    print(f"  Found {sku}: {node['id']} ${price:.2f} ({node['product']['title']})")
                else:
                    print(f"  Skip {sku}: {node['id']} ${price:.2f} (keeping ${prev_price:.2f} variant)")
        time.sleep(0.5)

    missing = skus_needed - set(variant_map.keys())
    if missing:
        print(f"  WARNING: Could not find variants for: {sorted(missing)}")
    return variant_map


def find_class6_orders(orders):
    results = []
    for order in orders:
        tags = order.get("tags", "")
        if "reship" in tags.lower():
            continue

        line_items = order.get("line_items", [])
        if not line_items:
            continue

        # Find custom box SKU
        box_sku = ""
        for li in line_items:
            s = (li.get("sku") or "").strip()
            if s.startswith(CUSTOM_BOX_PREFIXES):
                box_sku = s
                break
        if not box_sku:
            continue

        box_curation = get_curation_from_box(box_sku)
        if not box_curation or box_curation not in CURATION_RECIPES:
            continue

        # Collect curation food items only (_rc_bundle, qty > 0)
        curation_food = []
        for li in line_items:
            sku = (li.get("sku") or "").strip()
            qty = li.get("quantity", 0)
            if qty == 0:
                continue
            if not sku.startswith(FOOD_PREFIXES):
                continue
            props = li.get("properties") or []
            if "_rc_bundle" not in {p.get("name", "") for p in props}:
                continue
            curation_food.append({"id": li["id"], "sku": sku, "qty": qty})

        if not curation_food:
            continue

        actual_skus = set(item["sku"] for item in curation_food)
        expected_skus = set(s for s, q in CURATION_RECIPES[box_curation])
        expected_pct = len(actual_skus & expected_skus) / len(actual_skus)

        # Find best matching curation
        best_cur = None
        best_pct = 0
        for cur, recipe in CURATION_RECIPES.items():
            recipe_skus = set(s for s, q in recipe)
            if recipe_skus:
                overlap = len(actual_skus & recipe_skus)
                pct = overlap / len(actual_skus)
                if pct > best_pct:
                    best_pct = pct
                    best_cur = cur

        # Must be a mismatch
        if not best_cur or best_cur == box_curation or best_pct <= expected_pct + 0.2:
            continue

        # Only clean 1:1 swaps (high match to one wrong curation)
        if best_pct < MIN_MATCH_PCT:
            continue

        # Determine correct recipe
        correct_recipe = [(s, q) for s, q in CURATION_RECIPES[box_curation]]
        correct_skus = set(s for s, q in correct_recipe)

        # Items to remove: curation items not in correct recipe
        to_remove = [item for item in curation_food if item["sku"] not in correct_skus]
        # Items to add: correct recipe items not already present
        to_add = [s for s in correct_skus if s not in actual_skus]

        if not to_remove and not to_add:
            continue

        customer = order.get("customer") or {}
        cust_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()

        # Check Recharge charge for customer-chosen items
        rc_check = check_rc_items(order, box_curation, best_cur)
        time.sleep(0.3)  # rate limit RC API

        results.append({
            "order_id": order["id"],
            "order_name": order.get("name", ""),
            "customer": cust_name,
            "email": order.get("email", ""),
            "box_sku": box_sku,
            "box_curation": box_curation,
            "actual_curation": best_cur,
            "match_pct": best_pct,
            "to_remove": to_remove,
            "to_add": to_add,
            "rc_status": rc_check["rc_status"],
            "rc_items": rc_check["rc_items"],
            "rc_charge_id": rc_check["rc_charge_id"],
        })

    return results


def print_plan(results):
    safe = [r for r in results if r["rc_status"] != "customer_chosen"]
    skipped = [r for r in results if r["rc_status"] == "customer_chosen"]

    print(f"\n{'='*80}")
    print(f"CLASS 6 CLEAN 1:1 MISMATCH: {len(results)} orders "
          f"({len(safe)} fixable, {len(skipped)} customer-chosen)")
    print(f"{'='*80}\n")

    RC_LABELS = {
        "batch_issue": "RC has wrong items too (batch issue)",
        "rc_matches_expected": "RC has correct items (Shopify-only issue)",
        "unknown": "RC charge not found",
        "customer_chosen": "SKIP — items may be customer-chosen",
    }

    for r in results:
        status_label = RC_LABELS.get(r["rc_status"], r["rc_status"])
        marker = " *** SKIPPING" if r["rc_status"] == "customer_chosen" else ""
        print(f"Order {r['order_name']} | {r['customer']} | {r['box_sku']}{marker}")
        print(f"  Box says {r['box_curation']}, items match {r['actual_curation']} ({r['match_pct']:.0%})")
        print(f"  Recharge: {status_label} (charge {r['rc_charge_id']})")
        if r["rc_items"]:
            print(f"  RC items: {', '.join(sorted(r['rc_items']))}")
        if r['to_remove']:
            skus = ", ".join(item['sku'] for item in r['to_remove'])
            print(f"  REMOVE: {skus}")
        if r['to_add']:
            print(f"  ADD: {', '.join(r['to_add'])}")
        print()

    return safe


def apply_changes(results, variant_map):
    success = 0
    failed = 0

    for r in results:
        order_name = r['order_name']
        order_gid = f"gid://shopify/Order/{r['order_id']}"

        # Check all add SKUs have variant IDs
        missing = [s for s in r['to_add'] if s not in variant_map]
        if missing:
            print(f"  SKIP {order_name}: Missing variant IDs for {missing}")
            failed += 1
            continue

        print(f"\n  Editing {order_name}...")

        # Begin edit
        begin_query = """
        mutation($id: ID!) {
          orderEditBegin(id: $id) {
            calculatedOrder {
              id
              lineItems(first: 50) {
                edges {
                  node { id sku quantity }
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

        # Build calc line item map
        calc_line_items = {}
        for edge in calc_order["lineItems"]["edges"]:
            node = edge["node"]
            key = node["sku"] or "(blank)"
            if key not in calc_line_items:
                calc_line_items[key] = []
            calc_line_items[key].append({"id": node["id"], "qty": node["quantity"]})

        time.sleep(0.5)

        # Remove wrong items
        edit_ok = True
        for item in r['to_remove']:
            sku = item['sku']
            if sku in calc_line_items and calc_line_items[sku]:
                calc_li = calc_line_items[sku].pop(0)
                if calc_li["qty"] == 0:
                    print(f"    SKIP {sku} (already qty 0)")
                    continue
                calc_li_id = calc_li["id"]
            else:
                print(f"    WARNING: {sku} not found, skipping")
                continue

            try:
                data = gql("""
                mutation($id: ID!, $lineItemId: ID!, $quantity: Int!) {
                  orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
                    calculatedOrder { id }
                    userErrors { field message }
                  }
                }""", {"id": calc_order_id, "lineItemId": calc_li_id, "quantity": 0})
                errors = data["orderEditSetQuantity"]["userErrors"]
                if errors:
                    print(f"    FAILED to remove {sku}: {errors}")
                    edit_ok = False
                else:
                    print(f"    Removed {sku}")
            except Exception as e:
                print(f"    FAILED to remove {sku}: {e}")
                edit_ok = False
            time.sleep(0.3)

        # Add correct items
        for sku in r['to_add']:
            variant_gid = variant_map[sku]
            try:
                data = gql("""
                mutation($id: ID!, $variantId: ID!, $quantity: Int!) {
                  orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity) {
                    calculatedLineItem { id }
                    calculatedOrder { id }
                    userErrors { field message }
                  }
                }""", {"id": calc_order_id, "variantId": variant_gid, "quantity": 1})
                errors = data["orderEditAddVariant"]["userErrors"]
                if errors:
                    print(f"    FAILED to add {sku}: {errors}")
                    edit_ok = False
                else:
                    print(f"    Added {sku}")
            except Exception as e:
                print(f"    FAILED to add {sku}: {e}")
                edit_ok = False
            time.sleep(0.3)

        # Commit
        if edit_ok:
            try:
                data = gql("""
                mutation($id: ID!) {
                  orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Fix Class 6: replace wrong curation items with correct recipe") {
                    order { id name }
                    userErrors { field message }
                  }
                }""", {"id": calc_order_id})
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

    print("\nIdentifying Class 6 clean 1:1 mismatches...")
    results = find_class6_orders(orders)

    if single_order:
        results = [r for r in results if r["order_name"] == f"#{single_order}"]

    safe_results = print_plan(results)

    if not safe_results:
        print("No fixable Class 6 mismatches found.")
        return

    if not commit:
        print("DRY RUN — no changes made. Use --commit to apply changes.")
        return

    # Collect all SKUs we need to add (only from safe results)
    all_add_skus = set()
    for r in safe_results:
        all_add_skus.update(r['to_add'])

    print(f"\nLooking up variant IDs for {len(all_add_skus)} SKUs...")
    variant_map = lookup_variant_ids(all_add_skus)

    missing = all_add_skus - set(variant_map.keys())
    if missing:
        print(f"\nERROR: Missing variant IDs for: {sorted(missing)}")
        print("Cannot proceed.")
        return

    print(f"\nApplying changes to {len(safe_results)} orders...")
    confirm = input("Type 'yes' to confirm: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return

    apply_changes(safe_results, variant_map)


if __name__ == "__main__":
    main()
