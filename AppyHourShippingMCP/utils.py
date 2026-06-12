"""Lean utils for shipping MCP. Mirrors the subset used by shipping/weather tools."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

APPYHOUR_ROOT = Path(__file__).resolve().parent.parent
SHIPPING_DIR = APPYHOUR_ROOT / "ShippingReports"
GELCALC_DIR = APPYHOUR_ROOT / "GelPackCalculator"
SHOPIFY_API_VERSION = "2024-10"


def format_error(e: Exception, context: str = "") -> str:
    prefix = f"Error in {context}: " if context else "Error: "
    etype = type(e).__name__
    msg = str(e)
    if "401" in msg or "Unauthorized" in msg:
        return f"{prefix}Authentication failed. Check your API credentials. ({etype})"
    if "403" in msg or "Forbidden" in msg:
        return f"{prefix}Permission denied. ({etype})"
    if "404" in msg or "Not Found" in msg:
        return f"{prefix}Resource not found. ({etype})"
    if "429" in msg or "rate limit" in msg.lower():
        return f"{prefix}Rate limit exceeded. Wait and retry. ({etype})"
    if "timeout" in msg.lower():
        return f"{prefix}Request timed out. ({etype})"
    if isinstance(e, ImportError):
        return f"{prefix}Required module not available: {e}"
    return f"{prefix}{etype}: {e}"


def to_json(data: Any, indent: int = 2) -> str:
    return json.dumps(data, indent=indent, default=str)


def shopify_paginate(
    url: str,
    headers: dict,
    params: dict | None = None,
    key: str = "orders",
    timeout: int = 30,
    sleep: float = 0.1,
) -> list[dict]:
    """Paginate Shopify REST following Link rel=next. Params only sent on page 1."""
    all_items: list[dict] = []
    page = 0
    while url:
        page += 1
        resp = requests.get(url, headers=headers, params=params if page == 1 else None, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"Shopify API returned {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if key:
            all_items.extend(data.get(key, []))
        else:
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
    return all_items
