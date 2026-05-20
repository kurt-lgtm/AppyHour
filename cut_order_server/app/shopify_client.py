"""Shopify Admin REST client — open/unfulfilled orders by ship tag."""
from __future__ import annotations

import re
import time
from typing import Iterator
import requests
from .creds import get_shopify


def _session() -> requests.Session:
    base, headers = get_shopify()
    s = requests.Session()
    s.headers.update(headers)
    s.base_url = base
    return s


def fetch_open_orders() -> Iterator[dict]:
    """Yield all open/unfulfilled orders. Link-header pagination."""
    s = _session()
    url = f"{s.base_url}/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled", "limit": 250}

    while url:
        resp = s.get(url, params=params, timeout=60)
        if resp.status_code == 429:
            time.sleep(2)
            continue
        resp.raise_for_status()
        for order in resp.json().get("orders", []):
            yield order

        link = resp.headers.get("Link", "")
        url = None
        params = None
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
        time.sleep(0.3)


def fetch_orders_90d() -> Iterator[dict]:
    """Yield trailing-90d orders for first-order multiplier ratio calc."""
    from datetime import datetime, timezone, timedelta
    s = _session()
    since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    url = f"{s.base_url}/orders.json"
    params = {"status": "any", "created_at_min": since, "limit": 250}

    while url:
        resp = s.get(url, params=params, timeout=60)
        if resp.status_code == 429:
            time.sleep(2)
            continue
        resp.raise_for_status()
        for order in resp.json().get("orders", []):
            yield order

        link = resp.headers.get("Link", "")
        url = None
        params = None
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
        time.sleep(0.3)
