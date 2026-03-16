"""Swap curated SKUs on unfulfilled custom box orders.

Replaces specific curation items (_rc_bundle only) with substitute SKUs.
Uses $0 variants. Removes old item, adds new one.

Usage:
    python swap_curation_skus.py              # dry-run
    python swap_curation_skus.py --commit     # apply changes
    python swap_curation_skus.py --single 117913  # one order only
"""
import requests, json, sys, time, csv
from datetime import datetime

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL_URL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

CUSTOM_BOX_PREFIXES = ("AHB-MCUST", "AHB-LCUST")

# Swap definitions: old_sku -> (new_sku, $0_variant_gid)
SWAPS = {
    "MT-BRAS": ("MT-SOP", "gid://shopify/ProductVariant/49467543257368"),
    "CH-WWBC": ("CH-WMANG", "gid://shopify/ProductVariant/51478178824472"),
    "CH-EBCC": ("CH-PBRIE", "gid://shopify/ProductVariant/51474205245720"),
    "CH-FOWC": ("CH-BRZ", "gid://shopify/ProductVariant/49611711611160"),
    "CH-IPRW": ("CH-PVEC", "gid://shopify/ProductVariant/50570542154008"),
    "CH-ALPHA": ("CH-6COM", "gid://shopify/ProductVariant/51474250760472"),
}


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


def fetch_all_unfulfilled():
    orders = []
    url = f"{BASE}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,tags,line_items,customer,email",
    }
    page = 0
    while url:
        page += 1
        print(f"  Fetching page {page}...")
        resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        orders.extend(resp.json().get("orders", []))
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return orders


def find_swap_orders(orders):
    results = []
    for o in orders:
        tags = o.get("tags", "")
        if "reship" in tags.lower():
            continue
        items = o.get("line_items", [])

        # Must have a custom box
        box_sku = ""
        for li in items:
            s = (li.get("sku") or "").strip()
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if s.startswith(CUSTOM_BOX_PREFIXES) and fq > 0:
                box_sku = s
                break
        if not box_sku:
            continue

        # Find curation items matching swap SKUs
        swaps_needed = []
        for li in items:
            sku = (li.get("sku") or "").strip()
            fq = li.get("fulfillable_quantity", li.get("quantity", 0))
            if fq <= 0 or sku not in SWAPS:
                continue
            props = li.get("properties") or []
            if "_rc_bundle" in {p.get("name", "") for p in props}:
                swaps_needed.append(sku)

        if swaps_needed:
            cust = o.get("customer", {}) or {}
            name = f"{cust.get('first_name', '')} {cust.get('last_name', '')}".strip()
            results.append({
                "order": o["name"],
                "id": o["id"],
                "name": name,
                "box_sku": box_sku,
                "swaps": swaps_needed,
            })
    return results


def fix_order(order_info, commit=False):
    name = order_info["order"]
    order_gid = f"gid://shopify/Order/{order_info['id']}"
    swaps = order_info["swaps"]

    swap_desc = ", ".join(f"{s}->{SWAPS[s][0]}" for s in swaps)
    print(f"  {name} | {order_info['box_sku']} | {swap_desc}")

    if not commit:
        return "dry_run"

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
        print(f"    FAILED to begin: {edit['userErrors']}")
        return "failed"

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

    edit_ok = True
    for old_sku in swaps:
        new_sku, variant_gid = SWAPS[old_sku]

        # Remove old
        if old_sku not in calc_items:
            print(f"    {old_sku} not in calc order, skip")
            continue
        for cli in calc_items[old_sku]:
            if cli["qty"] == 0:
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
                    print(f"    FAILED remove {old_sku}: {errors}")
                    edit_ok = False
                else:
                    print(f"    Removed {old_sku}")
            except Exception as e:
                print(f"    FAILED remove {old_sku}: {e}")
                edit_ok = False
            time.sleep(0.3)

        # Add new ($0 variant, allowDuplicates in case new SKU already on order)
        try:
            data = gql("""
            mutation($id: ID!, $variantId: ID!, $quantity: Int!) {
              orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
                calculatedLineItem { id }
                calculatedOrder { id }
                userErrors { field message }
              }
            }""", {"id": calc_id, "variantId": variant_gid, "quantity": 1})
            errors = data["orderEditAddVariant"]["userErrors"]
            if errors:
                print(f"    FAILED add {new_sku}: {errors}")
                edit_ok = False
            else:
                print(f"    Added {new_sku} ($0)")
        except Exception as e:
            print(f"    FAILED add {new_sku}: {e}")
            edit_ok = False
        time.sleep(0.3)

    if edit_ok:
        staff_note = "Shortage swap: " + ", ".join(f"{s} -> {SWAPS[s][0]}" for s in swaps)
        data = gql("""
        mutation($id: ID!) {
          orderEditCommit(id: $id, notifyCustomer: false, staffNote: "%s") {
            order { id name }
            userErrors { field message }
          }
        }""" % staff_note, {"id": calc_id})
        errors = data["orderEditCommit"]["userErrors"]
        if errors:
            print(f"    COMMIT FAILED: {errors}")
            return "failed"
        else:
            print(f"    COMMITTED")
            return "committed"
    else:
        print(f"    CANCELLED due to errors")
        return "failed"


def main():
    commit = "--commit" in sys.argv
    single = None
    if "--single" in sys.argv:
        idx = sys.argv.index("--single")
        single = sys.argv[idx + 1]

    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    only_sku = None
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        only_sku = sys.argv[idx + 1]

    print("Fetching unfulfilled orders...")
    orders = fetch_all_unfulfilled()
    print(f"Fetched {len(orders)} orders\n")

    print("Finding orders needing swaps...")
    swap_orders = find_swap_orders(orders)
    print(f"Found {len(swap_orders)} orders\n")

    # Filter to specific old SKU if requested
    if only_sku:
        swap_orders = [o for o in swap_orders if any(s == only_sku for s in o["swaps"])]
        # Also trim swaps list to only the requested SKU
        for o in swap_orders:
            o["swaps"] = [s for s in o["swaps"] if s == only_sku]
        print(f"Filtered to {only_sku}: {len(swap_orders)} orders\n")

    # Apply limit
    if limit and not single:
        swap_orders = swap_orders[:limit]
        print(f"Limited to first {limit} orders\n")

    # Save log CSV
    log_rows = []
    success = 0
    failed = 0

    for i, order_info in enumerate(swap_orders):
        if single and order_info["order"] != f"#{single}":
            continue

        result = fix_order(order_info, commit=commit)

        for old_sku in order_info["swaps"]:
            new_sku = SWAPS[old_sku][0]
            log_rows.append({
                "Order": order_info["order"],
                "Order ID": order_info["id"],
                "Customer": order_info["name"],
                "Box SKU": order_info["box_sku"],
                "Old SKU": old_sku,
                "New SKU": new_sku,
                "Result": result,
            })

        if result in ("committed", "dry_run"):
            success += 1
        else:
            failed += 1

        # Rate limit for commits
        if commit and (i + 1) % 10 == 0:
            print(f"  ... {i+1}/{len(swap_orders)} processed")
            time.sleep(1)

    # Write log
    log_path = f"swap_log_{datetime.now().strftime('%Y-%m-%d_%H%M')}.csv"
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Order", "Order ID", "Customer", "Box SKU", "Old SKU", "New SKU", "Result"])
        w.writeheader()
        w.writerows(log_rows)

    print(f"\n{'='*40}")
    mode = "COMMITTED" if commit else "DRY RUN"
    print(f"{mode}: {success} ok, {failed} failed")
    print(f"Log saved: {log_path}")
    if not commit:
        print("Use --commit to apply changes.")


if __name__ == "__main__":
    main()
