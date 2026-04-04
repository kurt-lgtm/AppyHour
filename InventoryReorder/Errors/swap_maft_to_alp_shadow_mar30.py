# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Swap CH-MAFT → CH-ALP + CH-SHADOW on _SHIP_2026-03-30 orders by email."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "AppyHourMCP"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import get_shopify_auth, shopify_graphql
import requests

SHIP_TAG = "_SHIP_2026-03-30"
REMOVE_SKU = "CH-MAFT"
ADD_SKUS = ["CH-ALP", "CH-SHADOW"]

EMAILS = [
    "karenclayton@comcast.net",
    "ronmcq59@yahoo.com",
    "judi.brockmeyer@aol.com",
    "jaspinwall13@yahoo.com",
    "pamela.kinsel@gmail.com",
    "colleenshea@snet.net",
    "vidinha@aol.com",
    "theresav.ortiz@gmail.com",
    "hmahmoodzadegan@gmail.com",
    "faekelly13@gmail.com",
    "thetworobbins007@gmail.com",
    "bbfoodandwine@gmail.com",
    "kimberlymwall@hotmail.com",
    "oh_stop_it@hotmail.com",
    "carolynb7886@gmail.com",
    "bifrost2010@me.com",
    "jbmason331@gmail.com",
    "mwaldorf03@gmail.com",
    "nlsmith8315@gmail.com",
    "tmpnqn@aol.com",
    "cmjgran@gmail.com",
    "bgballard123@gmail.com",
    "dianasanchezromero7@gmail.com",
    "pete.isenberg@gmail.com",
    "sciekot@yahoo.com",
    "vanessa.crans@gmail.com",
    "skmefford89@gmail.com",
    "cometswin5@gmail.com",
    "theronhi@gmail.com",
    "hrickets@aol.com",
    "kk.berrien@gmail.com",
    "melba4066@gmail.com",
    "oemqueen13@yahoo.com",
    "maggie_waller@yahoo.com",
    "megmacy@yahoo.com",
    "emma1nana@icloud.com",
    "laurapeters@comcast.net",
    "mdglitz@comcast.net",
    "gweisenhorn@gmail.com",
    "torieabbott@gmail.com",
    "mariamc528@gmail.com",
    "jenniecrazymom@yahoo.com",
    "leopardspot92462@att.net",
    "sabrina.bentler@experian.com",
    "liv2themaxx@gmail.com",
    "zayra.arrieta@gmail.com",
    "jodidawnt@gmail.com",
    "carolynpell@aol.com",
    "amberrkuhn@gmail.com",
    "patches27@comcast.net",
    "dancinglisaj@msn.com",
    "kathy@gingeryinfo.com",
    "billmayhan@hotmail.com",
    "shelbymae1953@gmail.com",
    "ksleckenby@gmail.com",
    "anne@annebingham.com",
    "love.allachka@gmail.com",
    "anileen63@gmail.com",
    "delainieb@gmail.com",
    "lrjohnson13@gmail.com",
    "craftynananat@gmail.com",
    "bbewest@sbcglobal.net",
    "miss.marie2414@gmail.com",
    "sgilles1964@gmail.com",
]

COMMIT = "--commit" in sys.argv

def lookup_variant_gids(base, headers, skus):
    """Find $0 variant GIDs for target SKUs via GraphQL (same pattern as order_edit.py)."""
    variant_map = {}
    query_str = " OR ".join(f"sku:{s}" for s in skus)
    data = shopify_graphql(
        base,
        headers,
        """
        query($q: String!) {
            productVariants(first: 50, query: $q) {
                edges { node { id sku price product { title } } }
            }
        }
    """,
        {"q": query_str},
    )
    for edge in data.get("productVariants", {}).get("edges", []):
        node = edge["node"]
        sku = node["sku"]
        price = float(node.get("price", "999"))
        if sku in skus:
            prev_price = variant_map.get(sku, (None, float("inf")))[1]
            if price < prev_price:
                variant_map[sku] = (node["id"], price)
    return {sku: gid for sku, (gid, _) in variant_map.items()}

def fetch_orders_by_tag(base, headers, tag):
    """Fetch all unfulfilled orders with the given tag."""
    all_orders = []
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,email,tags,line_items",
    }
    url = f"{base}/orders.json"
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
        for o in orders:
            if tag in (o.get("tags") or ""):
                all_orders.append(o)
        # Pagination via Link header
        link = resp.headers.get("Link", "")
        url = None
        params = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
                    break
        time.sleep(0.1)
    return all_orders

def swap_order(base, headers, order, remove_sku, add_gids):
    """Remove remove_sku and add all add_gids to order via GraphQL edit."""
    order_gid = f"gid://shopify/Order/{order['id']}"

    # Begin edit
    data = shopify_graphql(
        base,
        headers,
        """
        mutation($id: ID!) {
            orderEditBegin(id: $id) {
                calculatedOrder {
                    id
                    lineItems(first: 100) {
                        edges { node { id sku quantity } }
                    }
                }
                userErrors { field message }
            }
        }
    """,
        {"id": order_gid},
    )

    edit = data.get("orderEditBegin", {})
    errors = edit.get("userErrors", [])
    if errors:
        return False, errors[0].get("message", "Unknown error")

    calc_order = edit.get("calculatedOrder", {})
    calc_id = calc_order.get("id")
    if not calc_id:
        return False, "No calculatedOrder returned"

    # Find the line item to remove
    removed = False
    for edge in calc_order.get("lineItems", {}).get("edges", []):
        node = edge["node"]
        if node.get("sku") == remove_sku and node.get("quantity", 0) > 0:
            shopify_graphql(
                base,
                headers,
                """
                mutation($id: ID!, $lineItemId: ID!, $quantity: Int!) {
                    orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
                        userErrors { field message }
                    }
                }
            """,
                {"id": calc_id, "lineItemId": node["id"], "quantity": 0},
            )
            removed = True
            break

    if not removed:
        # Commit empty edit to cancel
        shopify_graphql(
            base,
            headers,
            """
            mutation($id: ID!) {
                orderEditCommit(id: $id, notifyCustomer: false, staffNote: "cancelled - no MAFT found") {
                    userErrors { field message }
                }
            }
        """,
            {"id": calc_id},
        )
        return False, f"{remove_sku} not found on order"

    # Add replacement SKUs
    for sku, gid in add_gids.items():
        shopify_graphql(
            base,
            headers,
            """
            mutation($id: ID!, $variantId: ID!, $quantity: Int!) {
                orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
                    userErrors { field message }
                }
            }
        """,
            {"id": calc_id, "variantId": gid, "quantity": 1},
        )

    # Commit
    result = shopify_graphql(
        base,
        headers,
        """
        mutation($id: ID!) {
            orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Swap CH-MAFT → CH-ALP + CH-SHADOW (shortage)") {
                order { id }
                userErrors { field message }
            }
        }
    """,
        {"id": calc_id},
    )

    commit_errors = result.get("orderEditCommit", {}).get("userErrors", [])
    if commit_errors:
        return False, commit_errors[0].get("message", "Commit error")

    return True, "OK"

def main():
    base, headers = get_shopify_auth()
    email_set = {e.lower().strip() for e in EMAILS}

    print(f"Looking up $0 variant GIDs for {ADD_SKUS}...")
    add_gids = lookup_variant_gids(base, headers, ADD_SKUS)
    for sku, gid in add_gids.items():
        print(f"  {sku}: {gid}")
    if len(add_gids) != len(ADD_SKUS):
        missing = set(ADD_SKUS) - set(add_gids.keys())
        print(f"ERROR: Missing variant GIDs for {missing}")
        sys.exit(1)

    print(f"\nFetching orders with tag {SHIP_TAG}...")
    orders = fetch_orders_by_tag(base, headers, SHIP_TAG)
    print(f"  Found {len(orders)} orders")

    # Filter to target emails that have CH-MAFT
    targets = []
    for o in orders:
        email = (o.get("email") or "").lower().strip()
        if email not in email_set:
            continue
        has_maft = any(
            li.get("sku") == REMOVE_SKU and li.get("fulfillable_quantity", li.get("quantity", 0)) > 0
            for li in o.get("line_items", [])
        )
        if has_maft:
            targets.append(o)

    print(f"  Matched {len(targets)} orders with {REMOVE_SKU}")

    # Show preview
    for o in targets:
        print(f"  {o['name']:10s} {(o.get('email') or ''):40s}")

    if not COMMIT:
        print(f"\nDRY RUN — {len(targets)} orders would be swapped.")
        print("Run with --commit to execute.")
        return

    # Execute swaps
    print(f"\nExecuting swaps on {len(targets)} orders...")
    success = 0
    failed = 0
    for o in targets:
        ok, msg = swap_order(base, headers, o, REMOVE_SKU, add_gids)
        status = "OK" if ok else f"FAIL: {msg}"
        print(f"  {o['name']:10s} {status}")
        if ok:
            success += 1
        else:
            failed += 1
        time.sleep(0.3)

    print(f"\nDone: {success} swapped, {failed} failed out of {len(targets)} orders")

if __name__ == "__main__":
    main()
