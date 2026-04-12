"""
Order Edit MCP tools — SKU swaps via Shopify GraphQL Order Edit API.

Pattern: beginEdit -> setQuantity(0) -> addVariant(allowDuplicates) -> commitEdit
Uses InventoryReorder's static Admin API token.
"""

import json
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

from utils import get_shopify_auth, shopify_graphql, format_error, to_json, APPYHOUR_ROOT, shopify_paginate

# Dietary restriction box SKU fragments — these indicate curated boxes but
# do NOT prevent swaps. The restriction only means the *default* curation
# avoids that category. If an item is on the order, the customer chose it
# or it's a standard rotation item safe to swap within its own category.
# - NNRS = No Nuts (swap anything except nuts)
# - NCRS = No Crackers (swap anything except crackers)
# - CORS = No Meat (swap anything except meat)
DIETARY_RESTRICTION_FRAGMENTS = ("NNRS", "CORS", "NCRS")


# Module-level variant GID cache — same SKUs get looked up every swap run
_variant_gid_cache: dict[str, str] = {}


def _lookup_variant_gids(base: str, headers: dict[str, str], skus: set[str]) -> dict[str, str]:
    """Look up $0 variant GIDs for a set of SKUs. Prefers cheapest variant. Cached."""
    uncached = skus - set(_variant_gid_cache)
    if not uncached:
        return {sku: _variant_gid_cache[sku] for sku in skus}
    variant_map: dict[str, tuple[str, float]] = {}
    sku_list = sorted(uncached)
    batch_size = 10
    for i in range(0, len(sku_list), batch_size):
        batch = sku_list[i:i + batch_size]
        query_str = " OR ".join(f"sku:{s}" for s in batch)
        data = shopify_graphql(base, headers, """
        query($q: String!) {
          productVariants(first: 50, query: $q) {
            edges {
              node { id sku price product { title } }
            }
          }
        }
        """, {"q": query_str})
        for edge in data["productVariants"]["edges"]:
            node = edge["node"]
            sku = node["sku"]
            price = float(node.get("price", "999"))
            if sku in skus:
                prev_price = variant_map.get(sku, (None, float("inf")))[1]
                if price < prev_price:
                    variant_map[sku] = (node["id"], price)
        time.sleep(0.1)
    missing = uncached - set(variant_map.keys())
    if missing:
        raise RuntimeError(f"Could not find variants for: {sorted(missing)}")
    # Populate cache with new lookups
    for sku, (gid, _) in variant_map.items():
        _variant_gid_cache[sku] = gid
    return {sku: _variant_gid_cache[sku] for sku in skus}


def _swap_order_skus(base: str, headers: dict[str, str], order_gid: str, swaps: dict[str, str], variant_gids: dict[str, str]) -> list[str]:
    """Swap SKUs on a single order. Returns list of swap descriptions.

    Safety: snapshots all line items after beginEdit, verifies only target
    SKUs are modified before commit. Aborts if unexpected changes detected.
    """
    data = shopify_graphql(base, headers, """
        mutation orderEditBegin($id: ID!) {
            orderEditBegin(id: $id) {
                calculatedOrder {
                    id
                    lineItems(first: 50) {
                        edges { node { id quantity sku } }
                    }
                }
                userErrors { field message }
            }
        }
    """, {"id": order_gid})

    calc_order = data["orderEditBegin"]["calculatedOrder"]
    if not calc_order:
        errors = data["orderEditBegin"]["userErrors"]
        raise RuntimeError(f"beginEdit failed: {errors}")
    calc_id = calc_order["id"]

    # Snapshot ALL line items for pre-commit verification
    all_items_snapshot: dict[str, tuple[str, int]] = {}
    calc_items: dict[str, tuple[str, int]] = {}
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        sku = node.get("sku") or ""
        qty = node.get("quantity", 0)
        li_id = node["id"]
        if qty > 0:
            all_items_snapshot[li_id] = (sku, qty)
            if sku in swaps:
                calc_items[sku] = (li_id, qty)

    if not calc_items:
        raise RuntimeError("No swappable line items found in calculated order")

    swapped = []
    modified_li_ids: set[str] = set()
    for old_sku, (calc_li_id, qty) in calc_items.items():
        new_sku = swaps[old_sku]
        new_gid = variant_gids[new_sku]
        modified_li_ids.add(calc_li_id)

        shopify_graphql(base, headers, """
            mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
                orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "lineItemId": calc_li_id, "quantity": 0})

        shopify_graphql(base, headers, """
            mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
                orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "variantId": new_gid, "quantity": qty, "allowDuplicates": True})

        swapped.append(f"{old_sku}->{new_sku}(qty={qty})")

    # Pre-commit verification: re-fetch calculated order, check only target items changed
    verify_data = shopify_graphql(base, headers, """
        query($id: ID!) {
            node(id: $id) {
                ... on CalculatedOrder {
                    lineItems(first: 50) {
                        edges { node { id quantity sku } }
                    }
                }
            }
        }
    """, {"id": calc_id})

    post_items: dict[str, tuple[str, int]] = {}
    for edge in verify_data["node"]["lineItems"]["edges"]:
        node = edge["node"]
        post_items[node["id"]] = (node.get("sku") or "", node.get("quantity", 0))

    # Check: any non-target items that changed quantity?
    unexpected_changes = []
    for li_id, (sku, orig_qty) in all_items_snapshot.items():
        if li_id in modified_li_ids:
            continue  # expected to change
        post = post_items.get(li_id)
        if post and post[1] != orig_qty:
            unexpected_changes.append(f"{sku}: {orig_qty}->{post[1]}")

    if unexpected_changes:
        raise RuntimeError(f"ABORT: unexpected item changes detected: {unexpected_changes}")

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

    return swapped


def register(mcp: object) -> None:
    """Register order edit tools on the MCP server."""

    class SwapInput(BaseModel):
        """Input for swapping SKUs on Shopify orders."""
        model_config = ConfigDict(str_strip_whitespace=True)

        ship_tag: str = Field(..., description="Ship date tag to filter orders (e.g. '_SHIP_2026-03-23')")
        swaps: dict[str, str] = Field(..., description="Map of old_sku -> new_sku (e.g. {'CH-LEON': 'CH-LOU'})")
        box_sku: str = Field("", description="Optional: only process orders containing this box SKU (e.g. 'AHB-MCUST-SPN')")
        dry_run: bool = Field(True, description="If true (default), preview without modifying orders")

    @mcp.tool(
        name="appyhour_swap_order_skus",
        annotations={
            "title": "Swap SKUs on Shopify Orders",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def swap_order_skus(params: SwapInput) -> str:
        """Swap SKUs on unfulfilled Shopify orders using the Order Edit API.

        Finds orders matching the ship_tag (and optional box_sku filter) that
        contain any of the source SKUs, then replaces them with target SKUs.
        Automatically looks up $0 variant GIDs for replacement SKUs.

        WARNING: With dry_run=False, this modifies orders on your Shopify store.

        Args:
            params: Ship tag, swap map, optional box SKU filter, dry_run flag.

        Returns:
            JSON with preview (dry_run=True) or results (dry_run=False).
        """
        try:
            base, headers = get_shopify_auth()
            source_skus = set(params.swaps.keys())
            target_skus = set(params.swaps.values())

            # Look up $0 variant GIDs for replacement SKUs
            variant_gids = _lookup_variant_gids(base, headers, target_skus)

            # Fetch all unfulfilled orders
            all_orders = shopify_paginate(
                f"{base}/orders.json", headers,
                params={
                    "status": "open",
                    "fulfillment_status": "unfulfilled",
                    "limit": 250,
                    "fields": "id,name,tags,line_items,customer,email",
                },
            )

            # Filter by ship tag + box SKU + swappable SKUs
            targets = []
            for o in all_orders:
                tags = [t.strip() for t in o.get("tags", "").split(",")]
                if params.ship_tag not in tags:
                    continue
                has_box = not params.box_sku
                swap_skus = set()
                for li in o.get("line_items", []):
                    sku = (li.get("sku") or "")
                    fq = li.get("fulfillable_quantity", 0)
                    if params.box_sku and sku == params.box_sku:
                        has_box = True
                    if sku in source_skus and fq > 0:
                        swap_skus.add(sku)
                if has_box and swap_skus:
                    targets.append((o, swap_skus))

            if params.dry_run:
                preview = []
                for o, swap_skus in targets:
                    preview.append({
                        "order": o.get("name", ""),
                        "swaps": {s: params.swaps[s] for s in sorted(swap_skus)},
                    })
                return to_json({
                    "dry_run": True,
                    "ship_tag": params.ship_tag,
                    "box_sku": params.box_sku or "(any)",
                    "variant_gids": variant_gids,
                    "orders_to_swap": len(targets),
                    "preview": preview,
                })

            # Execute swaps — run 8 orders concurrently to stay within MCP timeout
            results = []
            errors_list = []

            def _do_swap(order, swap_skus):
                oid = order["id"]
                name = order.get("name", "")
                order_gid = f"gid://shopify/Order/{oid}"
                email = ""
                cust = order.get("customer")
                if cust:
                    email = cust.get("email", "") or ""
                if not email:
                    email = order.get("email", "") or ""
                swap_map = {s: params.swaps[s] for s in swap_skus}
                swapped = _swap_order_skus(base, headers, order_gid, swap_map, variant_gids)
                return {"order": name, "email": email, "swaps": swapped}

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_do_swap, order, swap_skus): order.get("name", "")
                           for order, swap_skus in targets}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as e:
                        errors_list.append({"order": name, "error": str(e)})

            # Write CSV
            today = datetime.now().strftime("%Y-%m-%d")
            csv_path = str(APPYHOUR_ROOT / "GelPackCalculator" / f"swap_results_{today}.csv")
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["order", "email", "swaps"])
                writer.writeheader()
                for r in results:
                    writer.writerow({**r, "swaps": "; ".join(r["swaps"])})

            return to_json({
                "swapped": len(results),
                "failed": len(errors_list),

                "csv_path": csv_path,
                "results": results[:20],
                "errors": errors_list[:20],
            })
        except Exception as e:
            return format_error(e, "swap_order_skus")
