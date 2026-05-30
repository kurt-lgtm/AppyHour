# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""
Shared utilities for the AppyHour MCP server.
Handles path setup, settings loading, and error formatting.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("appyhour_mcp.utils")

# ---------------------------------------------------------------------------
# Path setup — add sibling project directories so we can import their modules
# ---------------------------------------------------------------------------

MCP_ROOT = Path(__file__).resolve().parent
APPYHOUR_ROOT = MCP_ROOT.parent  # AppyHour/

GELCALC_DIR = APPYHOUR_ROOT / "GelPackCalculator"
INVENTORY_DIR = APPYHOUR_ROOT / "InventoryReorder"
SHIPPING_DIR = APPYHOUR_ROOT / "ShippingReports"

# Shared settings path — used by google_sheets, gorgias, gorgias_sheets_sync, ops_summary_builder
APPDATA_SETTINGS = Path(os.environ.get("APPDATA", "")) / "AppyHour" / "gel_calc_shopify_settings.json"

# Shared Google Sheet IDs
OPS_SHEET_ID = "190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA"

def setup_paths():
    """Add sibling project directories to sys.path (idempotent)."""
    for p in [APPYHOUR_ROOT, GELCALC_DIR, INVENTORY_DIR, SHIPPING_DIR]:
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

# Run at import time so tools can do top-level imports
setup_paths()

# ---------------------------------------------------------------------------
# Settings cache
# ---------------------------------------------------------------------------

_gelcalc_settings: Optional[dict] = None
_inventory_settings: Optional[dict] = None

def get_gelcalc_settings() -> dict:
    """Load GelPackCalculator settings (cached)."""
    global _gelcalc_settings
    if _gelcalc_settings is None:
        try:
            from gel_pack_shopify import load_settings
            _gelcalc_settings = load_settings()
        except Exception:
            logger.exception("Failed to load GelPackCalculator settings")
            _gelcalc_settings = {}
    return _gelcalc_settings

def get_inventory_settings() -> dict:
    """Load InventoryReorder settings (cached)."""
    global _inventory_settings
    if _inventory_settings is None:
        try:
            from inventory_reorder import load_settings
            _inventory_settings = load_settings()
        except ImportError:
            _inventory_settings = {}
    return _inventory_settings

def reload_settings():
    """Force-reload all settings caches."""
    global _gelcalc_settings, _inventory_settings
    _gelcalc_settings = None
    _inventory_settings = None

# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def format_error(e: Exception, context: str = "") -> str:
    """Format an exception into a user-friendly error string."""
    prefix = f"Error in {context}: " if context else "Error: "
    etype = type(e).__name__

    if "401" in str(e) or "Unauthorized" in str(e):
        return f"{prefix}Authentication failed. Check your API credentials. ({etype})"
    if "403" in str(e) or "Forbidden" in str(e):
        return f"{prefix}Permission denied. You don't have access to this resource. ({etype})"
    if "404" in str(e) or "Not Found" in str(e):
        return f"{prefix}Resource not found. Check the ID or identifier. ({etype})"
    if "429" in str(e) or "rate limit" in str(e).lower():
        return f"{prefix}Rate limit exceeded. Wait a moment and try again. ({etype})"
    if "timeout" in str(e).lower():
        return f"{prefix}Request timed out. Try again. ({etype})"
    if isinstance(e, ImportError):
        return f"{prefix}Required module not available: {e}"

    return f"{prefix}{etype}: {e}"

# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------

def to_json(data: Any, indent: int = 2) -> str:
    """Serialize data to a JSON string, handling common types."""
    return json.dumps(data, indent=indent, default=str)

# ---------------------------------------------------------------------------
# Shopify helpers (shared across shopify, order_edit, matrix_qc, shipping)
# ---------------------------------------------------------------------------

def shopify_paginate(
    url: str,
    headers: dict,
    params: dict | None = None,
    key: str = "orders",
    timeout: int = 30,
    sleep: float = 0.1,
    resource: str = "",
) -> list[dict]:
    """Paginate a Shopify REST endpoint following Link rel=next headers.

    Args:
        url: Full Shopify REST URL (e.g. https://store.myshopify.com/admin/api/2026-04/orders.json).
        headers: Auth headers dict.
        params: Query params (only sent on first page; Shopify cursor URLs include them).
        key: JSON key to extract results from (e.g. "orders", "products"). Use "" for auto-detect (first key).
        timeout: Request timeout in seconds.
        sleep: Delay between pages in seconds.
        resource: Cache tier (see tools/cache.py CACHE_TTL). Defaults to ``key``
            when omitted. Pass "orders-live" or any name absent from the tier
            map to bypass caching for write-adjacent reads.

    Returns:
        Combined list of all items across all pages.
    """
    import re
    import time
    import requests as _requests

    # Cache full paginated result keyed on (url, params). Resource tier
    # defaults to ``key`` (e.g. "orders" -> 10m, "products" -> 1h).
    cache_resource = resource or key or "default"
    _store = None
    _ckey = None
    if cache_resource:
        from tools.cache import cache_key, get_store

        _store = get_store()
        _ckey = cache_key(cache_resource, url=url, params=params or {}, key=key)
        _cached = _store.get(_ckey)
        if _cached is not None:
            return _cached

    all_items: list[dict] = []
    page = 0
    while url:
        page += 1
        resp = _requests.get(url, headers=headers, params=params if page == 1 else None, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"Shopify API returned {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if key:
            all_items.extend(data.get(key, []))
        else:
            # Auto-detect: use first key in response
            first_key = next(iter(data), None)
            if first_key and isinstance(data[first_key], list):
                all_items.extend(data[first_key])
        url = None
        link = resp.headers.get("Link", "")
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
        if url:
            time.sleep(sleep)
    if _store is not None and _ckey is not None:
        _store.put(_ckey, all_items, cache_resource)
    return all_items


# Re-exported from appyhour_lib.credentials (single source of truth).
# Env vars > InventoryReorder settings.
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402,F401

def active_line_items(order: dict) -> list:
    """Return line items with net quantity > 0, excluding refunded/removed items.

    Shopify keeps refunded line items at their original quantity — only
    refund_line_items records show what was removed. This subtracts refunded
    quantities so callers see only what was actually shipped or still pending.

    Always use this when checking which SKUs are present on an order.
    Requires order to be fetched with fields including 'line_items,refunds'.
    """
    refunded: dict = {}
    for refund in order.get("refunds", []):
        for rli in refund.get("refund_line_items", []):
            lid = rli["line_item_id"]
            refunded[lid] = refunded.get(lid, 0) + rli["quantity"]

    return [
        {**li, "quantity": li.get("quantity", 0) - refunded.get(li["id"], 0)}
        for li in order.get("line_items", [])
        if li.get("quantity", 0) - refunded.get(li["id"], 0) > 0
    ]

def shopify_graphql(
    base: str,
    headers: dict,
    query: str,
    variables: Optional[dict] = None,
    resource: str = "default",
) -> dict:
    """Execute a Shopify GraphQL query. Returns the 'data' key.

    When ``resource`` maps to a TTL tier (see tools/cache.py CACHE_TTL), the
    response is cached per-resource. Pass ``resource="orders-live"`` (or any
    name absent from the tier map) to bypass the cache for write-adjacent
    reads. Mutations must NOT be cached — callers running mutations should
    leave resource at default AND the mutation tool must bust the affected
    resource afterward.
    """
    import requests

    # Never cache mutations — they're writes, and a cached mutation response
    # could be served to a later identical call. Detect by leading keyword.
    is_mutation = query.lstrip().lower().startswith("mutation")

    store = None
    key = None
    if not is_mutation:
        from tools.cache import cache_key, get_store

        store = get_store()
        key = cache_key(resource, base=base, query=query, variables=variables or {})
        cached = store.get(key)
        if cached is not None:
            return cached

    url = f"{base}/graphql.json"
    body = {"query": query}
    if variables:
        body["variables"] = variables
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    result = data["data"]
    if store is not None and key is not None:
        store.put(key, result, resource)
    return result
