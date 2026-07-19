"""Canonical filesystem paths for the AppyHour data layer.

Single source of truth — every script that touches `shipping.db` (Kori,
ShippingReports, skill query.py, sync_logon, sync_all_carriers, parsers,
backfills) MUST import from here. No hardcoded paths anywhere else.

Why this module exists
======================
Prior to 2026-05-14, ~15 scripts hardcoded `shipping.db` with subtly
different prefixes — `PROJECT_DIR/shipping.db` (the 0-byte stub),
absolute Windows paths to the canonical, ad-hoc `os.path.join(...)`
constructions. The drift caused at least one wrong-DB crash this session
(`download_fedex_imap.py:125`) plus stale-data confusion across other
agent sessions.

Canonical DB location
=====================
`C:\\AppyHourData\\shipping.db` (as of 2026-07-08) — the Kori-owned DB
containing all 4 carriers + delivery_status (Parcel Panel) + feedback
(Gorgias) + kori_snapshots + weather_history. Moved OUT of
`%APPDATA%\\AppyHour` because MSIX virtualizes AppData: packaged
(Claude/MCP) processes saw a copy-on-write shadow that a 7/07 app update
deleted, causing the 7/08 false-MISSING incident (REBUILD-WITH-AI.md
§5.1). The legacy `%APPDATA%` path remains a TRANSITION fallback only —
never symlink it to the new location (sqlite sidecars follow the opened
name; two names = two wals = corruption).
`ShippingReports/output/shipments.db` and `GelPackCalculator/shipping.db`
(0-byte stub) are deprecated since 2026-05-14.

Override
========
Set `APPYHOUR_DB_PATH` env var to point at a different file (useful for
testing, scratch analysis, or future multi-machine setups). The override
is honored only if the file exists; otherwise we fall back to canonical.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["db_path", "db_dir", "appyhour_appdata", "data_root", "invoices_dir"]

# Canonical data root — deliberately OUTSIDE %APPDATA% (MSIX-virtualized; see module docstring).
DATA_ROOT = Path(r"C:\AppyHourData")


def appyhour_appdata() -> Path:
    """Return %APPDATA%\\AppyHour as a Path. Creates if missing.

    Settings JSONs and other 3-app shared config live here. The shipping.db
    does NOT (as of 2026-07-08) — use db_path().
    """
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    p = base / "AppyHour"
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_root() -> Path:
    """Return the canonical data root (C:\\AppyHourData). Does NOT create it —
    the migration/restore step owns creation; a missing root means the legacy
    location is still live."""
    return DATA_ROOT


def db_dir() -> Path:
    """Return the directory containing shipping.db (backups/ + writelock live beside it)."""
    return db_path().parent


def db_path() -> Path:
    """Return the canonical shipping.db path.

    Resolution order (REBUILD-WITH-AI.md §5.1):
      1. APPYHOUR_DB_PATH env override, if that file exists
      2. C:\\AppyHourData\\shipping.db, if it exists (canonical)
      3. legacy %APPDATA%\\AppyHour\\shipping.db (transition fallback)
    """
    override = os.environ.get("APPYHOUR_DB_PATH", "").strip()
    if override:
        p = Path(override)
        if p.exists():
            return p
    canonical = DATA_ROOT / "shipping.db"
    if canonical.exists():
        return canonical
    return appyhour_appdata() / "shipping.db"


def invoices_dir() -> Path:
    """Carrier-invoice landing directory (where IMAP pullers + manual drops save).

    Currently still at `GelPackCalculator/Invoices` for backwards-compat with
    OnTrac/Veho IMAP scripts. Migrate to %APPDATA%/AppyHour/Invoices in a
    later pass once all sources are agnostic.
    """
    # PROJECT_DIR isn't stable across repos — anchor on a marker file instead.
    # Walk up from this module looking for the AppyHour repo root.
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "GelPackCalculator" / "Invoices"
        if candidate.is_dir():
            return candidate
    # Fallback: APPDATA-relative
    p = appyhour_appdata() / "Invoices"
    p.mkdir(parents=True, exist_ok=True)
    return p
