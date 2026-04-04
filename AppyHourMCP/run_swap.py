# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Standalone swap runner — bypasses MCP timeout."""
import sys
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "InventoryReorder"))

from utils import get_shopify_auth, shopify_graphql
import requests

SHIP_TAG = "_SHIP_2026-03-30"
SWAPS = {
    "CH-ALPHA": "CH-ALP",
    "MT-BRAS": "MT-SBRES",
}

# Dietary restriction box SKU fragments — orders with these are excluded from
# automatic swaps because their contents are curated for the restriction.
DIETARY_RESTRICTION_FRAGMENTS = ("NNRS", "CORS", "NCRS")

def lookup_variant_gids(base, headers, target_skus):
    gids = {}
    for sku in target_skus:
        resp = requests.get(f"{base}/variants.json", headers=headers,
                            params={"sku": sku, "limit": 1}, timeout=30)
        resp.raise_for_status()
        variants = resp.json().get("variants", [])
        for v in variants:
            if v.get("price") == "0.00":
                gids[sku] = f"gid://shopify/ProductVariant/{v['id']}"
                break
        if sku not in gids and variants:
            gids[sku] = f"gid://shopify/ProductVariant/{variants[0]['id']}"
    return gids

def fetch_orders(base, headers, ship_tag, source_skus):
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
    skipped = 0
    for o in all_orders:
        tags = [t.strip() for t in o.get("tags", "").split(",")]
        if ship_tag not in tags:
            continue
        # Exclude dietary restriction orders (NNRS/CORS/NCRS)
        order_skus = [(li.get("sku") or "").upper() for li in o.get("line_items", [])]
        if any(frag in sku for sku in order_skus for frag in DIETARY_RESTRICTION_FRAGMENTS):
            skipped += 1
            continue
        swap_skus = {li["sku"] for li in o.get("line_items", [])
                     if li.get("sku") in source_skus}
        if swap_skus:
            targets.append((o, swap_skus))
    print(f"  Skipped {skipped} dietary restriction orders (NNRS/CORS/NCRS)")
    return targets

def swap_one(base, headers, order, swap_skus, variant_gids, swaps):
    name = order.get("name", "")
    order_gid = f"gid://shopify/Order/{order['id']}"
    swap_map = {s: swaps[s] for s in swap_skus}

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

    calc_items = {}
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        sku = node.get("sku") or ""
        if node.get("quantity", 0) > 0 and sku in swap_map:
            calc_items[sku] = (node["id"], node["quantity"])

    if not calc_items:
        return name, [], "no swappable items in calculated order"

    for old_sku, (li_id, qty) in calc_items.items():
        new_sku = swap_map[old_sku]
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

    swapped = [f"{s}->{swap_map[s]}" for s in calc_items]
    return name, swapped, None

def main():
    base, headers = get_shopify_auth()
    source_skus = set(SWAPS.keys())
    target_skus = set(SWAPS.values())

    print(f"Looking up variant GIDs for {target_skus}...")
    variant_gids = lookup_variant_gids(base, headers, target_skus)
    print(f"  GIDs: {variant_gids}")

    print(f"Fetching orders tagged {SHIP_TAG}...")
    targets = fetch_orders(base, headers, SHIP_TAG, source_skus)
    print(f"  {len(targets)} orders to swap")

    success, failed = 0, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(swap_one, base, headers, o, ss, variant_gids, SWAPS): o.get("name")
                   for o, ss in targets}
        for future in as_completed(futures):
            name = futures[future]
            try:
                order_name, swapped, err = future.result()
                if err:
                    print(f"  SKIP {order_name}: {err}")
                else:
                    print(f"  OK   {order_name}: {', '.join(swapped)}")
                    success += 1
            except Exception as e:
                print(f"  FAIL {name}: {e}")
                failed += 1

    print(f"\nDone: {success} swapped, {failed} failed")

if __name__ == "__main__":
    main()
