"""Shopify credential resolver.

Env vars win; InventoryReorder settings are the fallback. Usable by lean
MCP servers (shipping) that don't want to import the tkinter app just to
load a JSON file.

Env vars:
  SHOPIFY_STORE_URL       e.g. "elevatefoods" (subdomain only, no .myshopify.com)
  SHOPIFY_ACCESS_TOKEN    Admin API access token
  SHOPIFY_API_VERSION     optional; defaults to "2024-10"
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_API_VERSION = "2026-04"


def _read_settings_fallback() -> dict:
    """Read InventoryReorder settings JSON directly (no module import).

    Keeps this resolver lean for the shipping MCP.
    """
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "AppyHour" / "inventory_reorder_settings.json",
        Path(__file__).resolve().parent.parent / "InventoryReorder" / "inventory_reorder_settings.json",
        Path(__file__).resolve().parent.parent / "InventoryReorder" / "dist" / "inventory_reorder_settings.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                with p.open(encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def get_shopify_credentials() -> tuple[str, str]:
    """Return (store_subdomain, access_token).

    Env vars first, InventoryReorder settings as fallback. Raises RuntimeError
    if neither source has both pieces.
    """
    store = os.environ.get("SHOPIFY_STORE_URL", "").strip()
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN", "").strip()

    if not (store and token):
        settings = _read_settings_fallback()
        store = store or str(settings.get("shopify_store_url", "")).strip()
        token = token or str(settings.get("shopify_access_token", "")).strip()

    if not store or not token:
        raise RuntimeError(
            "Shopify credentials not found. Set SHOPIFY_STORE_URL + "
            "SHOPIFY_ACCESS_TOKEN env vars, or configure InventoryReorder settings."
        )
    return store, token


def get_shopify_auth() -> tuple[str, dict[str, str]]:
    """Return (base_url, headers) tuple for requests calls."""
    store, token = get_shopify_credentials()
    version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION
    base = f"https://{store}.myshopify.com/admin/api/{version}"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    return base, headers


def get_openweather_key() -> str | None:
    """Return OWM key from env (OPENWEATHER_API_KEY) or None."""
    key = os.environ.get("OPENWEATHER_API_KEY", "").strip()
    return key or None
