"""
Order Edit MCP tools — SKU swaps via Shopify GraphQL Order Edit API.

Pattern: beginEdit -> setQuantity(0) -> addVariant(allowDuplicates) -> commitEdit
Uses InventoryReorder's static Admin API token.
"""

import json
import csv
import logging
import os
import time
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


# Append-only audit log — same file the fulfillment_web swap path writes
# (shopify_swap.py). HARD RULE (Kurt 2026-06-19): never run a swap path
# without a revert log. This closes the MCP-path gap (swap_audit.jsonl was
# missing bulk MCP swaps — see memory swap-audit-log-incomplete).
_AUDIT_LOG = os.path.join(
    os.environ.get("SWAP_AUDIT_DIR", r"C:\Users\Work\Claude Projects\_outputs\logs"),
    "swap_audit.jsonl",
)


def _audit(row: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_AUDIT_LOG), exist_ok=True)
        row = {"ts": datetime.now().isoformat(timespec="seconds"), "source": "mcp:order_edit", **row}
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001 — logging must never break a swap
        pass


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


def _swap_order_skus(base: str, headers: dict[str, str], order_gid: str, swaps: dict[str, str], variant_gids: dict[str, str], rc_bundle_only: bool = True) -> list[str]:
    """Swap SKUs on a single order. Returns list of swap descriptions.

    Safety: snapshots all line items after beginEdit, verifies only target
    SKUs are modified before commit. Aborts if unexpected changes detected.

    When rc_bundle_only=True (default), only line items with the `_rc_bundle`
    custom attribute are eligible — paid add-ons (no props) are never swapped.
    """
    data = shopify_graphql(base, headers, """
        mutation orderEditBegin($id: ID!) {
            orderEditBegin(id: $id) {
                calculatedOrder {
                    id
                    lineItems(first: 50) {
                        edges { node { id quantity sku customAttributes { key value } } }
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

    # Snapshot ALL line items for pre-commit verification.
    # calc_items is a LIST — an order can carry the SAME SKU on multiple line
    # items (e.g. two TR-TAPAS lines on #157930). A dict keyed by SKU silently
    # dropped all but the last line, leaving un-swapped quantity behind.
    all_items_snapshot: dict[str, tuple[str, int]] = {}
    calc_items: list[tuple[str, str, int]] = []  # (old_sku, li_id, qty)
    for edge in calc_order["lineItems"]["edges"]:
        node = edge["node"]
        sku = node.get("sku") or ""
        qty = node.get("quantity", 0)
        li_id = node["id"]
        attrs = {a.get("key"): a.get("value") for a in (node.get("customAttributes") or [])}
        is_rc_bundle = "_rc_bundle" in attrs
        if qty > 0:
            all_items_snapshot[li_id] = (sku, qty)
            if sku in swaps:
                if rc_bundle_only and not is_rc_bundle:
                    continue  # skip paid / non-bundle line items
                calc_items.append((sku, li_id, qty))

    if not calc_items:
        raise RuntimeError("No swappable line items found in calculated order")

    swapped = []
    modified_li_ids: set[str] = set()
    qty_removed = 0
    qty_added = 0
    for old_sku, calc_li_id, qty in calc_items:
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
        qty_removed += qty

        shopify_graphql(base, headers, """
            mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
                orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "variantId": new_gid, "quantity": qty, "allowDuplicates": True})
        qty_added += qty

        swapped.append(f"{old_sku}->{new_sku}(qty={qty})")

    # BALANCE INVARIANT (Kurt 2026-07-09, order #157930: removed 2 trays, added 1
    # back — box shipped a tray short). Every swap must put back exactly as much
    # quantity as it removed. Abort (never commit) on mismatch.
    if qty_removed != qty_added:
        _audit({"order_gid": order_gid, "swaps": swapped,
                "result": f"ABORT:unbalanced removed={qty_removed} added={qty_added}"})
        raise RuntimeError(
            f"ABORT: unbalanced swap — removed qty {qty_removed} != added qty {qty_added}; edit NOT committed"
        )

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
        _audit({"order_gid": order_gid, "swaps": swapped, "result": f"FAIL:commit:{errors}"})
        raise RuntimeError(f"commitEdit failed: {errors}")

    _audit({"order_gid": order_gid, "swaps": swapped,
            "qty_removed": qty_removed, "qty_added": qty_added, "result": "OK"})

    # Order mutated — invalidate cached order reads so subsequent fetches
    # see the edit (per cache.py bust contract).
    try:
        from tools.cache import get_store
        get_store().bust("orders")
    except Exception:  # noqa: BLE001 — cache bust is best-effort, never block a commit
        logging.getLogger("appyhour_mcp.order_edit").warning(
            "cache bust('orders') failed after commit (non-fatal)", exc_info=True
        )

    return swapped


def _lookup_zero_variant_gids(base: str, headers: dict[str, str], skus: set[str]) -> dict[str, str]:
    """Look up the $0.00 (free/curation) variant GID for each SKU.

    HARD RULE (see shopify-api skill): adds onto a customer order MUST use the
    $0.00 variant — a paid variant would charge the customer. Aborts if any SKU
    has no $0.00 variant rather than falling back to a paid one.
    """
    sku_list = sorted(skus)
    out: dict[str, str] = {}
    for i in range(0, len(sku_list), 10):
        batch = sku_list[i:i + 10]
        query_str = " OR ".join(f"sku:{s}" for s in batch)
        data = shopify_graphql(base, headers, """
        query($q: String!) {
          productVariants(first: 50, query: $q) {
            edges { node { id sku price } }
          }
        }
        """, {"q": query_str})
        for edge in data["productVariants"]["edges"]:
            node = edge["node"]
            sku = node["sku"]
            if sku in skus and node.get("price") == "0.00":
                out.setdefault(sku, node["id"])
        time.sleep(0.1)
    missing = skus - set(out)
    if missing:
        raise RuntimeError(f"No $0.00 variant found for: {sorted(missing)} — aborting (never add a paid variant)")
    return out


def _add_variants_to_order(base: str, headers: dict[str, str], order_gid: str,
                           add_gids: dict[str, str], qty: int = 1,
                           skip_if_present: bool = True) -> list[str]:
    """Add free variants to a single order. Returns descriptions of what was added.

    Idempotent: with skip_if_present=True, a SKU already on the order (with
    fulfillable qty > 0) is skipped, so re-running never double-adds.
    Pure add — no existing line item is modified or removed.
    """
    data = shopify_graphql(base, headers, """
        mutation orderEditBegin($id: ID!) {
            orderEditBegin(id: $id) {
                calculatedOrder {
                    id
                    lineItems(first: 100) { edges { node { id quantity sku } } }
                }
                userErrors { field message }
            }
        }
    """, {"id": order_gid})

    calc_order = data["orderEditBegin"]["calculatedOrder"]
    if not calc_order:
        raise RuntimeError(f"beginEdit failed: {data['orderEditBegin']['userErrors']}")
    calc_id = calc_order["id"]

    present = {edge["node"].get("sku") for edge in calc_order["lineItems"]["edges"]
               if edge["node"].get("quantity", 0) > 0}

    added: list[str] = []
    for sku, gid in add_gids.items():
        if skip_if_present and sku in present:
            continue
        shopify_graphql(base, headers, """
            mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!, $allowDuplicates: Boolean) {
                orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: $allowDuplicates) {
                    calculatedOrder { id }
                    userErrors { field message }
                }
            }
        """, {"id": calc_id, "variantId": gid, "quantity": qty, "allowDuplicates": True})
        added.append(f"+{sku}(qty={qty})")

    if not added:
        return []  # nothing to do — leave the draft uncommitted

    data = shopify_graphql(base, headers, """
        mutation orderEditCommit($id: ID!) {
            orderEditCommit(id: $id, notifyCustomer: false, staffNote: "Added free accompaniment(s)") {
                order { id }
                userErrors { field message }
            }
        }
    """, {"id": calc_id})
    errors = data["orderEditCommit"]["userErrors"]
    if errors:
        raise RuntimeError(f"commitEdit failed: {errors}")

    try:
        from tools.cache import get_store
        get_store().bust("orders")
    except Exception:  # noqa: BLE001 — cache bust is best-effort, never block a commit
        logging.getLogger("appyhour_mcp.order_edit").warning(
            "cache bust('orders') failed after add (non-fatal)", exc_info=True
        )
    return added


def register(mcp: object) -> None:
    """Register order edit tools on the MCP server."""

    class SwapInput(BaseModel):
        """Input for swapping SKUs on Shopify orders."""
        model_config = ConfigDict(str_strip_whitespace=True)

        ship_tag: str = Field(..., description="Ship date tag to filter orders (e.g. '_SHIP_2026-03-23')")
        swaps: dict[str, object] = Field(..., description="Map of old_sku -> new_sku (str) OR list[str] to alternate per-order (e.g. {'AC-RBOL': ['AC-PFLAT', 'AC-TCRISP']})")
        box_sku: str = Field("", description="Optional: only process orders containing this box SKU (e.g. 'AHB-MCUST-SPN')")
        box_sku_contains: list[str] = Field(default_factory=list, description="Optional: only process orders containing a box SKU whose name includes ANY of these substrings (e.g. ['-MDT', 'XMDT']). Applied in addition to box_sku.")
        dry_run: bool = Field(True, description="If true (default), preview without modifying orders")
        rc_bundle_only: bool = Field(True, description="If true (default), only swap line items with _rc_bundle property; skip paid/chosen items")

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
            # Flatten target SKUs (value may be str or list[str])
            target_skus: set[str] = set()
            for v in params.swaps.values():
                if isinstance(v, list):
                    target_skus.update(v)
                else:
                    target_skus.add(str(v))

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
                has_contains = not params.box_sku_contains
                swap_skus = set()
                for li in o.get("line_items", []):
                    sku = (li.get("sku") or "")
                    fq = li.get("fulfillable_quantity", 0)
                    if params.box_sku and sku == params.box_sku:
                        has_box = True
                    if params.box_sku_contains and any(frag in sku for frag in params.box_sku_contains):
                        has_contains = True
                    if sku in source_skus and fq > 0:
                        if params.rc_bundle_only:
                            props = li.get("properties") or []
                            has_rc = any((p.get("name") == "_rc_bundle") for p in props)
                            if not has_rc:
                                continue
                        swap_skus.add(sku)
                if has_box and has_contains and swap_skus:
                    targets.append((o, swap_skus))

            # Deterministic order for alternating targets: sort by order name
            targets.sort(key=lambda t: t[0].get("name", ""))

            def _resolve_swap_map(order_idx: int, swap_skus: set[str]) -> dict[str, str]:
                """Resolve per-order target SKU (str value as-is, list value round-robin by order_idx)."""
                out: dict[str, str] = {}
                for s in swap_skus:
                    v = params.swaps[s]
                    if isinstance(v, list):
                        out[s] = v[order_idx % len(v)]
                    else:
                        out[s] = str(v)
                return out

            if params.dry_run:
                preview = []
                target_counts: dict[str, int] = {}
                for idx, (o, swap_skus) in enumerate(targets):
                    swap_map = _resolve_swap_map(idx, swap_skus)
                    for v in swap_map.values():
                        target_counts[v] = target_counts.get(v, 0) + 1
                    preview.append({
                        "order": o.get("name", ""),
                        "swaps": swap_map,
                    })
                return to_json({
                    "dry_run": True,
                    "ship_tag": params.ship_tag,
                    "box_sku": params.box_sku or "(any)",
                    "rc_bundle_only": params.rc_bundle_only,
                    "variant_gids": variant_gids,
                    "orders_to_swap": len(targets),
                    "target_counts": target_counts,
                    "preview": preview,
                })

            # Execute swaps — run 8 orders concurrently to stay within MCP timeout
            results = []
            errors_list = []

            def _do_swap(order, swap_skus, idx):
                oid = order["id"]
                name = order.get("name", "")
                order_gid = f"gid://shopify/Order/{oid}"
                email = ""
                cust = order.get("customer")
                if cust:
                    email = cust.get("email", "") or ""
                if not email:
                    email = order.get("email", "") or ""
                swap_map = _resolve_swap_map(idx, swap_skus)
                swapped = _swap_order_skus(base, headers, order_gid, swap_map, variant_gids, params.rc_bundle_only)
                return {"order": name, "email": email, "swaps": swapped}

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_do_swap, order, swap_skus, idx): order.get("name", "")
                           for idx, (order, swap_skus) in enumerate(targets)}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as e:
                        errors_list.append({"order": name, "error": str(e)})

            # Write CSV
            today = datetime.now().strftime("%Y-%m-%d")
            csv_path = str(APPYHOUR_ROOT / "InventoryReorder" / "fulfillment_web" / f"swap_results_{today}.csv")
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

    class AddVariantsInput(BaseModel):
        """Input for adding free variants to Shopify orders (pure add, no removal)."""
        model_config = ConfigDict(str_strip_whitespace=True)

        add_skus: list[str] = Field(..., description="SKUs to add. Each MUST have a $0.00 variant (e.g. ['AC-KETT', 'AC-RHB']).")
        ship_tag: str = Field("", description="Optional: only orders carrying this ship tag (e.g. '_SHIP_2026-07-04').")
        box_sku: str = Field("", description="Optional: only orders containing this exact box SKU.")
        box_sku_contains: list[str] = Field(default_factory=list, description="Optional: only orders with a SKU containing ANY of these substrings (e.g. ['AHB-X4JUL']).")
        order_names: list[str] = Field(default_factory=list, description="Optional: explicit order names/numbers (e.g. ['#155799', '154238']). Overrides tag/box filters when set.")
        quantity: int = Field(1, ge=1, description="Quantity per added SKU (default 1).")
        skip_if_present: bool = Field(True, description="If true (default), skip a SKU already on the order — makes re-runs safe (no double-add).")
        dry_run: bool = Field(True, description="If true (default), preview without modifying orders.")

    @mcp.tool(
        name="appyhour_add_order_variants",
        annotations={
            "title": "Add Free Variants to Shopify Orders",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def add_order_variants(params: AddVariantsInput) -> str:
        """Add free ($0.00) variant(s) to unfulfilled Shopify orders — pure add, never removes.

        Resolves each SKU to its $0.00 variant (aborts if none — never adds a paid
        variant, so the customer is never charged). Idempotent: an order already
        carrying a SKU is skipped, so re-running can't double-add.

        Target selection: explicit order_names, OR (ship_tag / box_sku / box_sku_contains)
        filters over unfulfilled orders.

        WARNING: With dry_run=False, this modifies orders on your Shopify store.
        """
        try:
            base, headers = get_shopify_auth()
            add_gids = _lookup_zero_variant_gids(base, headers, set(params.add_skus))

            # Build target order list
            if params.order_names:
                wanted = {n.strip().lstrip("#") for n in params.order_names}
                all_orders = shopify_paginate(
                    f"{base}/orders.json", headers,
                    params={"status": "any", "limit": 250,
                            "fields": "id,name,tags,line_items"},
                )
                targets = [o for o in all_orders if (o.get("name", "").lstrip("#")) in wanted]
            else:
                all_orders = shopify_paginate(
                    f"{base}/orders.json", headers,
                    params={"status": "open", "fulfillment_status": "unfulfilled",
                            "limit": 250, "fields": "id,name,tags,line_items"},
                )
                targets = []
                for o in all_orders:
                    tags = [t.strip() for t in o.get("tags", "").split(",")]
                    if params.ship_tag and params.ship_tag not in tags:
                        continue
                    skus = {(li.get("sku") or "") for li in o.get("line_items", [])
                            if li.get("fulfillable_quantity", 0) > 0}
                    if params.box_sku and params.box_sku not in skus:
                        continue
                    if params.box_sku_contains and not any(
                        frag in s for s in skus for frag in params.box_sku_contains):
                        continue
                    targets.append(o)

            targets.sort(key=lambda o: o.get("name", ""))

            if params.dry_run:
                preview = []
                for o in targets:
                    present = {(li.get("sku") or "") for li in o.get("line_items", [])
                               if li.get("fulfillable_quantity", 0) > 0}
                    to_add = [s for s in params.add_skus
                              if not (params.skip_if_present and s in present)]
                    preview.append({"order": o.get("name", ""), "would_add": to_add})
                return to_json({
                    "dry_run": True,
                    "add_skus": params.add_skus,
                    "zero_variant_gids": add_gids,
                    "orders_matched": len(targets),
                    "orders_needing_add": sum(1 for p in preview if p["would_add"]),
                    "preview": preview[:100],
                })

            results, errors_list = [], []

            def _do_add(order):
                name = order.get("name", "")
                gid = f"gid://shopify/Order/{order['id']}"
                added = _add_variants_to_order(base, headers, gid, add_gids,
                                               params.quantity, params.skip_if_present)
                return {"order": name, "added": added or ["(already present — skipped)"]}

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_do_add, o): o.get("name", "") for o in targets}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as e:
                        errors_list.append({"order": name, "error": str(e)})

            committed = sum(1 for r in results if r["added"] != ["(already present — skipped)"])
            return to_json({
                "orders_matched": len(targets),
                "orders_committed": committed,
                "orders_skipped": len(results) - committed,
                "failed": len(errors_list),
                "results": results[:30],
                "errors": errors_list[:20],
            })
        except Exception as e:
            return format_error(e, "add_order_variants")
