"""Shopify order SKU swap via GraphQL order edit API.

Swaps a shortage SKU for a substitute across unfulfilled orders
filtered by ship date tag. Used by the fulfillment web app's
swap integration on shortage rows.
"""

from __future__ import annotations

import json
import time
from typing import Callable

import requests


def _gql(store_url: str, token: str, query: str, variables: dict | None = None) -> dict:
    """Execute a Shopify Admin GraphQL query."""
    url = f"https://{store_url}.myshopify.com/admin/api/2024-01/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


def _rest_get(store_url: str, token: str, path: str, params: dict | None = None) -> requests.Response:
    """Execute a Shopify Admin REST GET request."""
    url = f"https://{store_url}.myshopify.com/admin/api/2024-01/{path}"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp


def lookup_variant_gid(store_url: str, token: str, sku: str) -> str | None:
    """Find the $0 variant GID for a SKU. Returns None if not found."""
    # Escape double quotes in SKU to prevent GraphQL injection
    safe_sku = sku.replace('"', '\\"')
    query = f'{{ productVariants(first: 5, query: "sku:{safe_sku}") {{ edges {{ node {{ id sku price }} }} }} }}'
    data = _gql(store_url, token, query)
    variants = []
    for edge in data["productVariants"]["edges"]:
        node = edge["node"]
        if node["sku"] == sku:
            variants.append(node)
    if not variants:
        return None
    # Prefer $0 variant (used for curation swaps)
    variants.sort(key=lambda v: float(v["price"]))
    return variants[0]["id"]


def find_swap_targets(
    store_url: str,
    token: str,
    ship_tag: str,
    old_sku: str,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """Find unfulfilled orders with ship_tag containing old_sku as a curation item.

    Only includes line items with fulfillableQuantity > 0 and _rc_bundle property.
    """
    targets = []
    url = "orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,tags,line_items",
    }
    page = 0

    while url:
        page += 1
        if progress_callback:
            progress_callback(f"Fetching orders page {page}...")

        if page == 1:
            resp = _rest_get(store_url, token, url, params)
        else:
            # Pagination URL is absolute
            headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()

        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if ship_tag not in tags:
                continue
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                if sku != old_sku:
                    continue
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty <= 0:
                    continue
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if "_rc_bundle" not in prop_names:
                    continue
                targets.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                    "qty": qty,
                })

        # Pagination via Link header
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
        time.sleep(0.1)

    return targets


def execute_swap(
    store_url: str,
    token: str,
    order_gid: str,
    old_sku: str,
    new_variant_gid: str,
    staff_note: str = "",
) -> dict:
    """Swap old_sku for new variant on a single order via GraphQL order edit.

    Returns {success: bool, order_name: str, error: str | None}.
    """
    # Step 1: Begin edit
    data = _gql(store_url, token, """
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
    }""", {"id": order_gid})

    if data["orderEditBegin"]["userErrors"]:
        errors = data["orderEditBegin"]["userErrors"]
        return {"success": False, "error": f"beginEdit: {errors}"}

    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]

    # Find the old SKU line item
    li_node = None
    for edge in calc["lineItems"]["edges"]:
        node = edge["node"]
        if (node.get("sku") or "").strip() == old_sku and node["quantity"] > 0:
            li_node = node
            break

    if not li_node:
        return {"success": False, "error": f"Line item {old_sku} not found or qty=0"}

    # Step 2: Set old line item qty to 0
    time.sleep(0.3)
    data = _gql(store_url, token, """
    mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
        calculatedOrder { id }
        userErrors { field message }
      }
    }""", {"id": calc_id, "lineItemId": li_node["id"], "quantity": 0})

    if data["orderEditSetQuantity"]["userErrors"]:
        errors = data["orderEditSetQuantity"]["userErrors"]
        return {"success": False, "error": f"setQuantity: {errors}"}

    # Step 3: Add new variant
    time.sleep(0.3)
    data = _gql(store_url, token, """
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
        calculatedLineItem { id }
        calculatedOrder { id }
        userErrors { field message }
      }
    }""", {"id": calc_id, "variantId": new_variant_gid, "quantity": li_node["quantity"]})

    if data["orderEditAddVariant"]["userErrors"]:
        errors = data["orderEditAddVariant"]["userErrors"]
        return {"success": False, "error": f"addVariant: {errors}"}

    # Step 4: Commit
    time.sleep(0.3)
    data = _gql(store_url, token, """
    mutation orderEditCommit($id: ID!, $staffNote: String) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: $staffNote) {
        order { id name }
        userErrors { field message }
      }
    }""", {"id": calc_id, "staffNote": staff_note})

    if data["orderEditCommit"]["userErrors"]:
        errors = data["orderEditCommit"]["userErrors"]
        return {"success": False, "error": f"commit: {errors}"}

    return {"success": True, "error": None}


def execute_bulk_swap(
    store_url: str,
    token: str,
    targets: list[dict],
    old_sku: str,
    new_variant_gid: str,
    staff_note: str = "",
    dry_run: bool = True,
    progress_callback: Callable[[str], None] | None = None,
    cancel_flag: list | None = None,
) -> dict:
    """Execute swap on multiple orders.

    Args:
        targets: List from find_swap_targets().
        old_sku: SKU being replaced.
        new_variant_gid: GID of new variant to add.
        staff_note: Note added to each order edit.
        dry_run: If True, just return count without executing.
        progress_callback: Called with status string for each order.
        cancel_flag: Single-element list; if cancel_flag[0] is True, abort.

    Returns:
        {total, success, failed, errors, dry_run}
    """
    total = len(targets)

    if dry_run:
        return {
            "total": total,
            "success": 0,
            "failed": 0,
            "errors": [],
            "dry_run": True,
            "targets": [
                {"order_name": t["order_name"], "qty": t["qty"]}
                for t in targets
            ],
        }

    success = 0
    failed = 0
    errors = []

    for i, t in enumerate(targets, 1):
        if cancel_flag and cancel_flag[0]:
            errors.append("Cancelled by user")
            break

        if progress_callback:
            progress_callback(f"Swapping {i}/{total}: {t['order_name']}...")

        result = execute_swap(
            store_url, token, t["order_gid"], old_sku, new_variant_gid, staff_note
        )

        if result["success"]:
            success += 1
        else:
            failed += 1
            errors.append(f"{t['order_name']}: {result['error']}")

        time.sleep(0.1)

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "errors": errors,
        "dry_run": False,
    }
