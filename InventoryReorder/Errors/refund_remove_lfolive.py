"""Refund and remove paid AC-LFOLIVE from _SHIP_2026-03-23 orders.

Only targets PAID items (no _rc_bundle property). Curation items are left alone.

Usage:
    python refund_remove_lfolive.py              # dry-run
    python refund_remove_lfolive.py --commit     # apply changes
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
TARGET_SKU = "AC-LFOLIVE"


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


def find_paid_lfolive(orders):
    """Find orders with paid (non-curation) AC-LFOLIVE."""
    results = []
    for order in orders:
        tags = [t.strip() for t in (order.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" not in tags:
            continue
        for li in order.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku != TARGET_SKU:
                continue
            qty = li.get("fulfillable_quantity", li.get("quantity", 0))
            if qty <= 0:
                continue
            props = li.get("properties", []) or []
            prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
            if "_rc_bundle" in prop_names:
                continue  # skip curation items
            price = float(li.get("price", "0"))
            results.append({
                "order_id": order["id"],
                "order_name": order["name"],
                "order_gid": f"gid://shopify/Order/{order['id']}",
                "line_item_id": li["id"],
                "qty": qty,
                "price": price,
                "total": price * qty,
            })
    return results


def refund_and_remove(info):
    """Remove item via order edit, then refund via REST API."""
    order_name = info["order_name"]
    order_gid = info["order_gid"]
    order_id = info["order_id"]

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

    # Find the paid LFOLIVE line item in calculated order
    calc_li = None
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == TARGET_SKU and node["quantity"] > 0:
            calc_li = node
            break

    if not calc_li:
        print(f"    Could not find {TARGET_SKU} in edit session, skipping")
        return False

    time.sleep(0.3)

    # Step 2: Set quantity to 0
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
            print(f"    FAILED to remove: {errors}")
            return False
        print(f"    Removed {TARGET_SKU} x{calc_li['quantity']}")
    except Exception as e:
        print(f"    FAILED to remove: {e}")
        return False

    time.sleep(0.3)

    # Step 3: Commit
    commit_query = """
    mutation orderEditCommit($id: ID!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Refund & remove AC-LFOLIVE - out of stock") {
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
        print(f"    COMMITTED edit on {order_name}")
    except Exception as e:
        print(f"    COMMIT FAILED: {e}")
        return False

    time.sleep(0.3)

    # Step 4: Calculate refund
    refund_calc_url = f"{REST_BASE}/orders/{order_id}/refunds/calculate.json"
    refund_calc_body = {
        "refund": {
            "refund_line_items": [{
                "line_item_id": info["line_item_id"],
                "quantity": info["qty"],
            }],
        }
    }
    try:
        calc_resp = requests.post(refund_calc_url, headers=HEADERS,
                                   json=refund_calc_body, timeout=30)
        calc_resp.raise_for_status()
        calc_data = calc_resp.json().get("refund", {})
        transactions = calc_data.get("transactions", [])
    except Exception as e:
        print(f"    REFUND CALC FAILED: {e}")
        return True  # item removed, just refund failed

    time.sleep(0.3)

    # Step 5: Issue refund
    refund_url = f"{REST_BASE}/orders/{order_id}/refunds.json"
    refund_body = {
        "refund": {
            "notify": True,
            "refund_line_items": [{
                "line_item_id": info["line_item_id"],
                "quantity": info["qty"],
            }],
            "transactions": transactions,
        }
    }
    try:
        ref_resp = requests.post(refund_url, headers=HEADERS,
                                  json=refund_body, timeout=30)
        ref_resp.raise_for_status()
        refund_data = ref_resp.json().get("refund", {})
        refund_total = sum(
            float(t.get("amount", 0))
            for t in refund_data.get("transactions", [])
        )
        print(f"    REFUNDED ${refund_total:.2f} on {order_name}")
    except Exception as e:
        print(f"    REFUND FAILED: {e} (item was removed, refund manually)")

    return True


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Refund & Remove paid {TARGET_SKU} [{mode}]")
    print(f"{'='*60}\n")

    print("Fetching unfulfilled orders...")
    orders = fetch_all_unfulfilled()
    print(f"  Found {len(orders)} unfulfilled orders\n")

    print(f"Finding paid {TARGET_SKU} items...")
    targets = find_paid_lfolive(orders)
    print(f"  Found {len(targets)} paid {TARGET_SKU} items\n")

    if not targets:
        print("Nothing to do.")
        return

    total_refund = sum(t["total"] for t in targets)
    print(f"{'Order':<12} {'Qty':>4} {'Price':>8} {'Total':>8}")
    print("-" * 40)
    for t in targets:
        print(f"{t['order_name']:<12} {t['qty']:>4} ${t['price']:>7.2f} ${t['total']:>7.2f}")
    print("-" * 40)
    print(f"{'TOTAL':<12} {sum(t['qty'] for t in targets):>4} {'':>8} ${total_refund:>7.2f}")

    if not COMMIT:
        print(f"\nDRY-RUN complete. {len(targets)} orders would be edited & refunded.")
        print("Run with --commit to apply changes.")
        return

    success = 0
    failed = 0
    for t in targets:
        print(f"\n  Processing {t['order_name']}...")
        if refund_and_remove(t):
            success += 1
        else:
            failed += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  Done: {success} processed, {failed} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
