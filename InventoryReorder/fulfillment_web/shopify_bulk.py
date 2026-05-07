"""Shopify GraphQL Bulk Operations API helper.

Replaces paginated REST `/orders.json` reads with a single async bulk query.
Returns orders in the same shape used by REST callers (dicts with
`created_at`, `tags`, `line_items[].sku`, `line_items[].quantity`).

Flow:
    1. submit_bulk_query(query) -> bulk_op_id
    2. poll_bulk_op(bulk_op_id) -> jsonl_url (waits until COMPLETED)
    3. stream_jsonl(url) -> iterator of node dicts
    4. reconstruct_orders(iter) -> List[order_dict]

Public convenience:
    fetch_fulfilled_orders_bulk(store, token, weeks_back) -> List[dict]

Notes:
    - Bulk Ops are exempt from cost-based rate limits.
    - One bulk op at a time per shop. Caller must serialize.
    - JSONL URL is S3 presigned, valid ~24h.
    - Children rows carry `__parentId` referencing parent's GID.
"""

from __future__ import annotations

import datetime
import json
import time
from typing import Iterator, List, Optional

import requests

API_VERSION = "2026-04"
DEFAULT_POLL_INTERVAL_SEC = 3
DEFAULT_POLL_TIMEOUT_SEC = 600


class BulkOpError(Exception):
    """Raised when a bulk operation fails or times out."""


def _gql_endpoint(store: str) -> str:
    if not store.startswith("http"):
        store = f"https://{store}.myshopify.com"
    return f"{store}/admin/api/{API_VERSION}/graphql.json"


def _gql_post(store: str, token: str, query: str, variables: Optional[dict] = None) -> dict:
    """Execute a single GraphQL request. Raises on transport or userErrors."""
    url = _gql_endpoint(store)
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    payload = {"query": query, "variables": variables or {}}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise BulkOpError(f"GraphQL HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if data.get("errors"):
        raise BulkOpError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def submit_bulk_query(store: str, token: str, query: str) -> str:
    """Submit a bulk query. Returns the bulk operation GID."""
    mutation = """
    mutation($q: String!) {
      bulkOperationRunQuery(query: $q) {
        bulkOperation { id status }
        userErrors { field message }
      }
    }
    """
    data = _gql_post(store, token, mutation, {"q": query})
    result = data["bulkOperationRunQuery"]
    errors = result.get("userErrors") or []
    if errors:
        raise BulkOpError(f"bulkOperationRunQuery userErrors: {errors}")
    op = result.get("bulkOperation")
    if not op or not op.get("id"):
        raise BulkOpError("bulkOperationRunQuery returned no operation id")
    return op["id"]


def poll_bulk_op(
    store: str,
    token: str,
    timeout_sec: int = DEFAULT_POLL_TIMEOUT_SEC,
    interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
) -> Optional[str]:
    """Poll currentBulkOperation until COMPLETED. Returns JSONL url, or None if no rows."""
    query = """
    { currentBulkOperation {
        id status errorCode objectCount url partialDataUrl
    } }
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        data = _gql_post(store, token, query)
        op = data.get("currentBulkOperation") or {}
        status = op.get("status")
        if status == "COMPLETED":
            return op.get("url")  # None when zero rows
        if status in ("FAILED", "CANCELED", "EXPIRED"):
            raise BulkOpError(f"bulk op {status}: errorCode={op.get('errorCode')}")
        time.sleep(interval_sec)
    raise BulkOpError(f"bulk op poll timeout after {timeout_sec}s")


def stream_jsonl(url: str) -> Iterator[dict]:
    """Stream-parse JSONL from the bulk op result URL."""
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            yield json.loads(line)


def _gid_to_int(gid: str) -> int:
    """Extract numeric id from a Shopify GID like gid://shopify/Order/123."""
    try:
        return int(gid.rsplit("/", 1)[-1])
    except (ValueError, AttributeError):
        return 0


def reconstruct_orders(rows: Iterator[dict]) -> List[dict]:
    """Reassemble flat JSONL rows into REST-shaped order dicts.

    Returns list of dicts with keys: id, name, created_at, tags (str), line_items.
    line_items entries have keys: sku, quantity.
    """
    orders: dict = {}
    for row in rows:
        gid = row.get("id") or ""
        parent = row.get("__parentId")
        if parent is None:
            # Order row
            tags_field = row.get("tags") or []
            if isinstance(tags_field, list):
                tags_str = ", ".join(tags_field)
            else:
                tags_str = str(tags_field)
            orders[gid] = {
                "id": _gid_to_int(gid),
                "name": row.get("name", ""),
                "created_at": row.get("createdAt", ""),
                "tags": tags_str,
                "line_items": [],
            }
        else:
            # Child line item row
            order = orders.get(parent)
            if order is None:
                continue
            order["line_items"].append(
                {
                    "sku": row.get("sku") or "",
                    "quantity": row.get("quantity", 0),
                }
            )
    return list(orders.values())


def gql_orders_by_tag(
    store: str,
    token: str,
    ship_tags: List[str],
    status: str = "open",
    fulfillment_status: str = "unfulfilled",
    line_items_first: int = 50,
) -> List[dict]:
    """Fetch orders matching one of `ship_tags` via GraphQL, server-side filtered.

    Uses orders(query: "(tag:X OR tag:Y) status:Z") — Shopify tag index, no
    client-side filter. Cursor pagination, 250/page (GraphQL max).

    Returns REST-shaped list of dicts with keys: id, name, tags (str),
    line_items[].sku, line_items[].quantity. Drop-in for REST callers that
    pull-all-then-filter by ship tag.
    """
    if not ship_tags:
        return []

    tag_clause = " OR ".join(f"tag:{t}" for t in ship_tags)
    parts = [f"({tag_clause})"]
    if status:
        parts.append(f"status:{status}")
    if fulfillment_status:
        parts.append(f"fulfillment_status:{fulfillment_status}")
    query_filter = " ".join(parts)

    gql = """
    query($cursor: String, $q: String!, $li: Int!) {
      orders(first: 250, after: $cursor, query: $q) {
        edges {
          node {
            id
            name
            tags
            lineItems(first: $li) {
              edges { node { sku quantity currentQuantity unfulfilledQuantity } }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """

    out: List[dict] = []
    cursor: Optional[str] = None
    while True:
        data = _gql_post(store, token, gql, {"cursor": cursor, "q": query_filter, "li": line_items_first})
        conn = data.get("orders") or {}
        for edge in conn.get("edges", []):
            node = edge.get("node") or {}
            tags_field = node.get("tags") or []
            tags_str = ", ".join(tags_field) if isinstance(tags_field, list) else str(tags_field)
            li_edges = (node.get("lineItems") or {}).get("edges", [])
            line_items = []
            for e in li_edges:
                n = e.get("node") or {}
                line_items.append({
                    "sku": n.get("sku") or "",
                    "quantity": n.get("quantity", 0),
                    "fulfillable_quantity": n.get("unfulfilledQuantity", n.get("currentQuantity", n.get("quantity", 0))),
                })
            out.append({
                "id": _gid_to_int(node.get("id") or ""),
                "name": node.get("name", ""),
                "tags": tags_str,
                "line_items": line_items,
            })
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return out


def fetch_fulfilled_orders_bulk(
    store: str,
    token: str,
    weeks_back: int = 8,
    timeout_sec: int = DEFAULT_POLL_TIMEOUT_SEC,
) -> List[dict]:
    """Pull fulfilled orders within `weeks_back` via Bulk Operations API.

    Returns REST-shaped list of dicts. Drop-in replacement for the REST
    pagination loop at fulfillment_web/app.py:6867.
    """
    cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=weeks_back * 7)).date().isoformat()
    bulk_query = f"""
    {{
      orders(query: "created_at:>={cutoff_date} fulfillment_status:shipped") {{
        edges {{
          node {{
            id
            name
            createdAt
            tags
            lineItems {{
              edges {{
                node {{
                  sku
                  quantity
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    submit_bulk_query(store, token, bulk_query)
    url = poll_bulk_op(store, token, timeout_sec=timeout_sec)
    if not url:
        return []
    return reconstruct_orders(stream_jsonl(url))
