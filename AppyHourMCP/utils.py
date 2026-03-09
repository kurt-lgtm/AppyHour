"""
Shared utilities for the AppyHour MCP server.
Handles path setup, settings loading, and error formatting.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Path setup — add sibling project directories so we can import their modules
# ---------------------------------------------------------------------------

MCP_ROOT = Path(__file__).resolve().parent
APPYHOUR_ROOT = MCP_ROOT.parent  # AppyHour/

GELCALC_DIR = APPYHOUR_ROOT / "GelPackCalculator"
INVENTORY_DIR = APPYHOUR_ROOT / "InventoryReorder"
SHIPPING_DIR = APPYHOUR_ROOT / "ShippingReports"


def setup_paths():
    """Add sibling project directories to sys.path (idempotent)."""
    for p in [GELCALC_DIR, INVENTORY_DIR, SHIPPING_DIR]:
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
        from gel_pack_shopify import load_settings
        _gelcalc_settings = load_settings()
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
