# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Partial swap: CH-FOWC → CH-CTGOD (30), CH-CSGOD (5), CH-WMANG (198).

Keeps 87 orders with CH-FOWC, swaps the remaining 233.
"""
import sys
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "InventoryReorder"))

from utils import get_shopify_auth, shopify_graphql
import requests

SHIP_TAG = "_SHIP_2026-04-06"
SOURCE_SKU = "CH-FOWC"
KEEP_COUNT = 87  # orders to leave as CH-FOWC

# Swap batches in order — first 30 get CTGOD, next 5 get CSGOD, rest get WMANG
SWAP_BATCHES = [
    (30, "CH-CTGOD"),
    (5, "CH-CSGOD"),
    (198, "CH-WMANG"),
]

DIETARY_RESTRICTION_FRAGMENTS = ("NNRS", "CORS", "NCRS")

def lookup_variant_gids(base, headers, target_skus):
    gids = {}
    for sku in target_skus:
        data = shopify_graphql(base, headers, """
            query($q: String!) {
                productVariants(first: 5, query: $q) {
                    edges { node { id sku price } }
                }
            }
        """, {"q": f"sku:{sku}"})
        variants = data.get("productVariants", {}).get("edges", [])
        # Prefer $0 variant
        for edge in variants:
            node = edge["node"]
            if node.get("sku") == sku and node.get("price") == "0.00":
                gids[sku] = node["id"]
                break
        if sku not in gids:
            for edge in variants:
                node = edge["node"]
                if node.get("sku") == sku:
                    gids[sku] = node["id"]
                    break
    return gids

def fetch_orders(base, headers, ship_tag, source_sku):
    all_orders = []
    url = f"{base}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled",
              "limit": 250, "fields": "id,name,tags,line_items"}
    page = 0
    while url:
        page += 1
        resp = requests.get(url, headers=headers,
                            params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
        all_orders.extend(orders)
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
        time.sleep(0.1)

    targets = []
    for o in all_orders:
        tags = [t.strip() for t in o.get("tags", "").split(",")]
        if ship_tag not in tags:
            continue
        has_source = any(li.get("sku") == source_sku for li in o.get("line_items", []))
        if has_source:
            targets.append(o)
    return targets

def swap_one(base, headers, order, old_sku, new_sku, variant_gids):
    name = order.get("name", "")
    order_gid = f"gid://shopify/Order/{order['id']}"

    data = shopify_graphql(base, headers, """
        mutation orderEditBegin($id: ID!) {
            orderEditBegin(id: $id) {
                calculatedOrder { id lineItems(first: 50) { edges { node { id quantity sku } } } }
                userErrors { field message }
            }
        }
    """, {"id": order_gid})

    calc_order = data["orderEditBegin"]["calculatedOrder"]
    if not calc_order:
        raise RuntimeError(f"beginEdit failed: {data['orderEditBegin']['userErrors']}")
    calc_id = calc_order["id"]

    li_id = None
    qty = 0
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        if node.get("sku") == old_sku and node.get("quantity", 0) > 0:
            li_id = node["id"]
            qty = node["quantity"]
            break

    if not li_id:
        return name, None, "no swappable item in calculated order"

    new_gid = variant_gids[new_sku]
    shopify_graphql(base, headers, """
        mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
            orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
                userErrors { field message }
            }
        }
    """, {"id": calc_id, "lineItemId": li_id, "quantity": 0})
    shopify_graphql(base, headers, """
        mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
            orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                userErrors { field message }
            }
        }
    """, {"id": calc_id, "variantId": new_gid, "quantity": qty, "allowDuplicates": True})

    data = shopify_graphql(base, headers, """
        mutation orderEditCommit($id: ID!) {
            orderEditCommit(id: $id) {
                order { id }
                userErrors { field message }
            }
        }
    """, {"id": calc_id})

    errors = data["orderEditCommit"]["userErrors"]
    if errors:
        raise RuntimeError(f"commitEdit failed: {errors}")

    return name, f"{old_sku}->{new_sku}", None

def main():
    base, headers = get_shopify_auth()

    target_skus = {new_sku for _, new_sku in SWAP_BATCHES}
    print(f"Looking up variant GIDs for {target_skus}...")
    variant_gids = lookup_variant_gids(base, headers, target_skus)
    print(f"  GIDs: {variant_gids}")

    if len(variant_gids) != len(target_skus):
        missing = target_skus - set(variant_gids.keys())
        print(f"  ERROR: Missing GIDs for {missing}")
        return

    print(f"Fetching orders tagged {SHIP_TAG} with {SOURCE_SKU}...")
    targets = fetch_orders(base, headers, SHIP_TAG, SOURCE_SKU)
    print(f"  {len(targets)} orders with {SOURCE_SKU}")

    # Skip first KEEP_COUNT orders (they keep FOWC), swap the rest
    to_swap = targets[KEEP_COUNT:]
    print(f"  Keeping first {KEEP_COUNT}, swapping {len(to_swap)}")

    # Assign each order to a swap batch
    assignments = []  # (order, new_sku)
    idx = 0
    for count, new_sku in SWAP_BATCHES:
        batch = to_swap[idx:idx + count]
        for o in batch:
            assignments.append((o, new_sku))
        idx += count
        print(f"  Batch: {len(batch)} orders -> {new_sku}")

    if not assignments:
        print("Nothing to swap!")
        return

    print(f"\nSwapping {len(assignments)} orders...")
    success, failed = 0, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(swap_one, base, headers, o, SOURCE_SKU, new_sku, variant_gids): o.get("name")
            for o, new_sku in assignments
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                order_name, swapped, err = future.result()
                if err:
                    print(f"  SKIP {order_name}: {err}")
                else:
                    print(f"  OK   {order_name}: {swapped}")
                    success += 1
            except Exception as e:
                print(f"  FAIL {name}: {e}")
                failed += 1

    print(f"\nDone: {success} swapped, {failed} failed")

if __name__ == "__main__":
    main()
