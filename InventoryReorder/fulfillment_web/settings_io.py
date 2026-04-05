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
    path = os.path.join(_get_app_dir(), SETTINGS_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(data: dict) -> None:
    path = os.path.join(_get_app_dir(), SETTINGS_FILE)
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
