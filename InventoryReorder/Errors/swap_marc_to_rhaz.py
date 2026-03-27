"""Swap AC-MARC to AC-RHAZ on AHB-XSPR orders (all 13) and half of BL-SDB orders (27).

Usage:
    python swap_marc_to_rhaz.py              # dry-run
    python swap_marc_to_rhaz.py --commit     # apply changes
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
NEW_SKU = "AC-RHAZ"


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


# Look up $0 variant for AC-RHAZ
def find_rhaz_variant():
    query = """
    {
      productVariants(first: 5, query: "sku:AC-RHAZ") {
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
    # Prefer $0 variant
    variants.sort(key=lambda v: float(v["price"]))
    return variants[0]["id"] if variants else None


# Fetch target orders
def fetch_targets():
    xspr_targets = []
    sdb_targets = []

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

            items = o.get("line_items", [])
            has_xspr = any(
                (li.get("sku") or "").strip() == "AHB-XSPR"
                and li.get("fulfillable_quantity", li.get("quantity", 0)) > 0
                for li in items
            )
            has_sdb = any(
                (li.get("sku") or "").strip() == "BL-SDB"
                and li.get("fulfillable_quantity", li.get("quantity", 0)) > 0
                for li in items
            )

            # Find paid AC-MARC
            for li in items:
                sku = (li.get("sku") or "").strip()
                if sku != OLD_SKU:
                    continue
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty <= 0:
                    continue
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if "_rc_bundle" in prop_names:
                    continue

                info = {
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                    "qty": qty,
                }
                if has_xspr:
                    xspr_targets.append(info)
                elif has_sdb:
                    sdb_targets.append(info)

        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)

    return xspr_targets, sdb_targets


def swap_item(order_info, rhaz_variant_gid):
    """Remove AC-MARC, add AC-RHAZ via order edit."""
    order_name = order_info["order_name"]
    order_gid = order_info["order_gid"]

    # Begin edit
    begin_query = """
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder {
          id
          lineItems(first: 50) {
            edges { node { id sku quantity title } }
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

    # Find AC-MARC
    marc_li = None
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == OLD_SKU and node["quantity"] > 0:
            marc_li = node
            break

    if not marc_li:
        print(f"    {OLD_SKU} not found in edit session")
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
            print(f"    FAILED remove {OLD_SKU}: {data['orderEditSetQuantity']['userErrors']}")
            return False
        print(f"    Removed {OLD_SKU} x{marc_li['quantity']}")
    except Exception as e:
        print(f"    FAILED remove: {e}")
        return False

    time.sleep(0.3)

    # Add AC-RHAZ
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
        data = gql(add_query, {"id": calc_id, "variantId": rhaz_variant_gid, "quantity": marc_li["quantity"]})
        if data["orderEditAddVariant"]["userErrors"]:
            print(f"    FAILED add {NEW_SKU}: {data['orderEditAddVariant']['userErrors']}")
            return False
        print(f"    Added {NEW_SKU} x{marc_li['quantity']}")
    except Exception as e:
        print(f"    FAILED add: {e}")
        return False

    time.sleep(0.3)

    # Commit
    commit_query = """
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap AC-MARC -> AC-RHAZ (out of stock)") {
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
    print(f"  Swap {OLD_SKU} -> {NEW_SKU} [{mode}]")
    print(f"{'='*60}\n")

    print("Looking up AC-RHAZ variant...")
    rhaz_gid = find_rhaz_variant()
    if not rhaz_gid:
        print("  ERROR: Could not find AC-RHAZ variant!")
        return

    print("\nFetching orders...")
    xspr, sdb = fetch_targets()
    print(f"\n  AHB-XSPR orders with paid AC-MARC: {len(xspr)}")
    print(f"  BL-SDB orders with paid AC-MARC: {len(sdb)}")

    # Take all XSPR + first half of SDB
    half_sdb = len(sdb) // 2
    targets = xspr + sdb[:half_sdb]

    print(f"\n  Will swap: {len(xspr)} XSPR + {half_sdb} SDB = {len(targets)} total\n")

    print(f"{'Order':<12} {'Source':<10} {'Qty':>4}")
    print("-" * 30)
    for t in xspr:
        print(f"{t['order_name']:<12} {'XSPR':<10} {t['qty']:>4}")
    for t in sdb[:half_sdb]:
        print(f"{t['order_name']:<12} {'SDB':<10} {t['qty']:>4}")

    if not COMMIT:
        print(f"\nDRY-RUN. {len(targets)} orders would be swapped.")
        print("Run with --commit to apply.")
        return

    success = 0
    failed = 0
    for t in targets:
        print(f"\n  Editing {t['order_name']}...")
        if swap_item(t, rhaz_gid):
            success += 1
        else:
            failed += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {success} swapped, {failed} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
