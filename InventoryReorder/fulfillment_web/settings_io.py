"""Settings persistence — shared with inventory_reorder.py (tkinter app).

Reads/writes inventory_reorder_settings.json with atomic write + backup.
Backward-compatible: new curation keys have defaults; old keys preserved.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

SETTINGS_FILE = "inventory_reorder_settings.json"


def _settings_path(for_write: bool = False) -> str:
    r"""Canonical path for the shared settings JSON.

    🔴 THIS USED TO WRITE ``InventoryReorder/dist/`` (through 2026-08-31), which made it a
    THIRD live copy: `appyhour_lib` read ``%APPDATA%`` first, so nothing this app saved was
    visible to the MCP servers, the cut-order builder, or the carrier IMAP jobs — and
    ``%APPDATA%`` was itself split in two by MSIX virtualization. Measured on 2026-08-31 the
    dist copy held 230 inventory SKUs while the %APPDATA% overlay held 157 of the same SKUs.
    Nothing failed; the copies just answered different questions.

    Canonical is ``C:\AppyHourData``. Falls back to the pre-migration behaviour only if
    appyhour_lib cannot be imported (frozen exe with a trimmed tree) — never silently, the
    resolver prints when it uses a legacy path.
    """
    try:
        if _APPYHOUR_ROOT and str(_APPYHOUR_ROOT) not in sys.path:
            sys.path.insert(0, str(_APPYHOUR_ROOT))
        from appyhour_lib.paths import inventory_settings_path
        if for_write:
            # 🔴 A WRITE NEVER FALLS BACK. Reads may still land on the legacy %APPDATA% copy
            # for one deprecation cycle; a write that followed that fallback would re-fork the
            # file, which is the divergence the 2026-08-31 merge closed.
            return str(inventory_settings_path(for_write=True))
        try:
            return str(inventory_settings_path())
        except FileNotFoundError:
            return str(inventory_settings_path(for_write=True))
    except ImportError:
        print("settings_io: appyhour_lib unavailable — falling back to the legacy app dir. "
              "This copy is NOT the one other AppyHour tools read.", file=sys.stderr)
        return os.path.join(_get_app_dir(), SETTINGS_FILE)


# AppyHour repo root: fulfillment_web/ -> InventoryReorder/ -> AppyHour/
_APPYHOUR_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dist = os.path.join(base, "dist", SETTINGS_FILE)
    if os.path.exists(dist):
        return os.path.join(base, "dist")
    return base


def _get_project_dir() -> str:
    """Return the project root dir (for RMFG folders, Shipments, etc.)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_settings() -> dict:
    path = _settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(data: dict) -> None:
    path = _settings_path(for_write=True)
    try:
        new_json = json.dumps(data, indent=2)
        if os.path.exists(path) and os.path.getsize(path) > 100:
            if len(new_json) < 50:
                return  # refuse to write essentially empty settings
            tmp_path = path + ".tmp"
            with open(tmp_path, "w") as f:
                f.write(new_json)
            bak_path = path + ".bak"
            shutil.copy2(path, bak_path)
            os.replace(tmp_path, path)
        else:
            with open(path, "w") as f:
                f.write(new_json)
    except Exception:
        pass
