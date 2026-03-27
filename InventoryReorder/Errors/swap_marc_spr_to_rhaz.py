"""Swap AC-MARC to AC-RHAZ on 40 AHB-SPR orders.

Usage:
    python swap_marc_spr_to_rhaz.py              # dry-run
    python swap_marc_spr_to_rhaz.py --commit     # apply
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
RHAZ_GID = "gid://shopify/ProductVariant/50823380631832"  # $0 variant
SWAP_LIMIT = 40


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


def fetch_spr_marc():
    targets = []
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
            has_spr = any(
                (li.get("sku") or "").strip() == "AHB-SPR"
                and li.get("fulfillable_quantity", li.get("quantity", 0)) > 0
                for li in o.get("line_items", [])
            )
            if not has_spr:
                continue
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                if sku != OLD_SKU:
                    continue
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty <= 0:
                    continue
                targets.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                    "qty": qty,
                })
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    params = None
        time.sleep(0.5)
    return targets


def swap_item(order_info):
    order_name = order_info["order_name"]
    order_gid = order_info["order_gid"]

    begin_query = """
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

    marc_li = None
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == OLD_SKU and node["quantity"] > 0:
            marc_li = node
            break

    if not marc_li:
        print(f"    {OLD_SKU} not found")
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
    """, {"id": calc_id, "lineItemId": marc_li["id"], "quantity": 0})
    if data["orderEditSetQuantity"]["userErrors"]:
        print(f"    FAILED remove: {data['orderEditSetQuantity']['userErrors']}")
        return False
    print(f"    Removed {OLD_SKU} x{marc_li['quantity']}")

    time.sleep(0.3)

    # Add
    data = gql("""
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
        calculatedLineItem { id }
        calculatedOrder { id }
        userErrors { field message }
      }
    }
    """, {"id": calc_id, "variantId": RHAZ_GID, "quantity": marc_li["quantity"]})
    if data["orderEditAddVariant"]["userErrors"]:
        print(f"    FAILED add: {data['orderEditAddVariant']['userErrors']}")
        return False
    print(f"    Added {NEW_SKU} x{marc_li['quantity']}")

    time.sleep(0.3)

    # Commit
    data = gql("""
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap AC-MARC -> AC-RHAZ (AHB-SPR spring box)") {
        order { id name }
        userErrors { field message }
      }
    }
    """, {"id": calc_id})
    if data["orderEditCommit"]["userErrors"]:
        print(f"    COMMIT FAILED: {data['orderEditCommit']['userErrors']}")
        return False
    print(f"    COMMITTED {order_name}")
    return True


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Swap {OLD_SKU} -> {NEW_SKU} on AHB-SPR orders [{mode}]")
    print(f"  Limit: {SWAP_LIMIT}")
    print(f"{'='*60}\n")

    print("Fetching AHB-SPR orders with AC-MARC...")
    targets = fetch_spr_marc()
    print(f"  Found {len(targets)} AHB-SPR orders with AC-MARC")

    targets = targets[:SWAP_LIMIT]
    print(f"  Will swap: {len(targets)}\n")

    for t in targets:
        print(f"  {t['order_name']}")

    if not COMMIT:
        print(f"\nDRY-RUN. {len(targets)} orders would be swapped.")
        print("Run with --commit to apply.")
        return

    success = 0
    failed = 0
    for t in targets:
        print(f"\n  Editing {t['order_name']}...")
        if swap_item(t):
            success += 1
        else:
            failed += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {success} swapped, {failed} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
