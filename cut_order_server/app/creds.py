"""Credential resolver — reuse appyhour_lib + settings JSON fallback.

Local dev: pull from existing InventoryReorder settings JSON.
Prod (DO droplet): env vars only (no settings JSON present).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make appyhour_lib importable when running from cut_order_server/
_APPYHOUR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_APPYHOUR_ROOT) not in sys.path:
    sys.path.insert(0, str(_APPYHOUR_ROOT))


def get_shopify() -> tuple[str, dict[str, str]]:
    """Return (base_url, headers). Delegates to appyhour_lib if available, else env-only."""
    try:
        from appyhour_lib.credentials import get_shopify_auth
        return get_shopify_auth()
    except (ImportError, RuntimeError):
        pass

    store = os.environ.get("SHOPIFY_SHOP") or os.environ.get("SHOPIFY_STORE_URL", "")
    token = os.environ.get("SHOPIFY_ADMIN_TOKEN") or os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
    version = os.environ.get("SHOPIFY_API_VERSION", "2026-04")
    if not store or not token:
        raise RuntimeError("Shopify credentials missing — set SHOPIFY_SHOP + SHOPIFY_ADMIN_TOKEN")
    return f"https://{store}.myshopify.com/admin/api/{version}", {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }


def _settings_fallback_path() -> Path | None:
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "AppyHour" / "inventory_reorder_settings.json",
        _APPYHOUR_ROOT / "InventoryReorder" / "inventory_reorder_settings.json",
        _APPYHOUR_ROOT / "InventoryReorder" / "dist" / "inventory_reorder_settings.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def get_google_credentials_path() -> str:
    """Service account JSON path. Priority:
    1. GOOGLE_SVC_ACCOUNT_JSON_CONTENT env var (inline JSON, App Platform pattern)
       → decoded to /tmp/gsvc.json on first call
    2. GOOGLE_SVC_ACCOUNT_JSON env var (file path)
    3. InventoryReorder settings.json fallback (local dev)
    4. AppyHour root fallback file
    """
    content = os.environ.get("GOOGLE_SVC_ACCOUNT_JSON_CONTENT", "").strip()
    if content:
        tmp = Path("/tmp/gsvc.json") if os.name != "nt" else Path(os.environ.get("TEMP", ".")) / "gsvc.json"
        if not tmp.exists() or tmp.stat().st_size != len(content):
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(content, encoding="utf-8")
        return str(tmp)

    p = os.environ.get("GOOGLE_SVC_ACCOUNT_JSON", "").strip()
    if p and Path(p).exists():
        return p

    settings_path = _settings_fallback_path()
    if settings_path:
        try:
            with settings_path.open(encoding="utf-8") as f:
                data = json.load(f)
            cand = str(data.get("google_credentials_path", "")).strip()
            if cand and Path(cand).exists():
                return cand
        except (OSError, json.JSONDecodeError):
            pass

    fallback = _APPYHOUR_ROOT / "shipping-perfomance-review-accd39ac4b78.json"
    if fallback.exists():
        return str(fallback)
    raise RuntimeError(
        "Google service account JSON missing — set GOOGLE_SVC_ACCOUNT_JSON_CONTENT env var"
    )


def get_recharge_token() -> str:
    """Env first, then InventoryReorder settings JSON."""
    token = os.environ.get("RECHARGE_TOKEN", "").strip()
    if token:
        return token
    p = _settings_fallback_path()
    if p:
        try:
            with p.open(encoding="utf-8") as f:
                data = json.load(f)
            t = str(data.get("recharge_api_token", "")).strip()
            if t:
                return t
        except (OSError, json.JSONDecodeError):
            pass
    raise RuntimeError("Recharge token missing — set RECHARGE_TOKEN or configure InventoryReorder settings")
