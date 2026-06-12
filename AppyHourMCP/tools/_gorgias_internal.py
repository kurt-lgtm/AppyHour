"""
Shared Gorgias API internals — auth, HTTP helpers, pagination.

Used by gorgias.py (query tools) and gorgias_sheets_sync.py (sync tools).
Config is cached at module level to avoid re-reading settings per request.
"""

import json
import logging
import time
from typing import Optional

import requests

from utils import APPDATA_SETTINGS

logger = logging.getLogger(__name__)

_GORGIAS_MIN_INTERVAL = 0.5  # Gorgias caps at roughly 2 req/s.
_gorgias_last_call: list[float] = [0.0]

# ---------------------------------------------------------------------------
# Config cache — loaded once, reused across all Gorgias API calls
# ---------------------------------------------------------------------------

_cached_auth: Optional[tuple[tuple[str, str], str]] = None


def _load_settings() -> dict:
    """Load raw settings dict from AppData."""
    if not APPDATA_SETTINGS.exists():
        raise FileNotFoundError("AppyHour settings not found in AppData.")
    with open(APPDATA_SETTINGS, encoding="utf-8") as f:
        return json.load(f)


def get_auth() -> tuple[tuple[str, str], str]:
    """Return cached (auth_tuple, base_url) for Gorgias API.

    Auth tuple is (email, token) for requests basic auth.
    """
    global _cached_auth
    if _cached_auth is None:
        settings = _load_settings()
        subdomain = settings.get("gorgias_subdomain", "")
        token = settings.get("gorgias_api_token", "")
        email = settings.get("gorgias_email", "")
        if not subdomain or not token or not email:
            raise ValueError("Gorgias subdomain, email, or API token not configured in settings.")
        _cached_auth = (email, token), f"https://{subdomain}.gorgias.com/api"
    return _cached_auth


def reload_auth():
    """Force-reload Gorgias auth (e.g. after settings change)."""
    global _cached_auth
    _cached_auth = None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _gorgias_get(
    url: str,
    *,
    auth: tuple[str, str],
    params: dict | None = None,
    timeout: int = 30,
    max_retries: int = 5,
) -> requests.Response:
    """GET a Gorgias endpoint with pacing and basic retry handling."""
    gap = time.monotonic() - _gorgias_last_call[0]
    if gap < _GORGIAS_MIN_INTERVAL:
        time.sleep(_GORGIAS_MIN_INTERVAL - gap)

    delay = 1.0
    for attempt in range(1, max_retries + 1):
        _gorgias_last_call[0] = time.monotonic()
        resp = requests.get(url, auth=auth, params=params, timeout=timeout)
        if resp.status_code == 429 and attempt < max_retries:
            retry_after = resp.headers.get("Retry-After")
            sleep_for = float(retry_after) if retry_after else delay
            logger.warning("Gorgias 429 on %s; retrying in %.1fs", url.rsplit("/", 1)[-1], sleep_for)
            time.sleep(sleep_for)
            delay = min(delay * 2, 60.0)
            continue
        if 500 <= resp.status_code < 600 and attempt < max_retries:
            logger.warning("Gorgias %d on %s; retrying in %.1fs", resp.status_code, url, delay)
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
            continue
        return resp
    return resp


def gorgias_get(endpoint: str, params: dict[str, str] | None = None) -> dict:
    """Make an authenticated GET request to the Gorgias API."""
    auth, base_url = get_auth()
    url = f"{base_url}/{endpoint}"
    resp = _gorgias_get(url, auth=auth, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def gorgias_paginate(endpoint: str, params: dict[str, str] | None = None, limit: int = 100) -> list[dict]:
    """Paginate through Gorgias API results using cursor pagination."""
    auth, base_url = get_auth()
    results: list[dict] = []
    params = dict(params or {})
    params.setdefault("limit", min(limit, 100))
    cursor = None

    while len(results) < limit:
        if cursor:
            params["cursor"] = cursor
        url = f"{base_url}/{endpoint}"
        resp = _gorgias_get(url, auth=auth, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            break
        results.extend(items)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    return results[:limit]
