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
`%APPDATA%\\AppyHour\\shipping.db` — the Kori-owned DB containing all
4 carriers + delivery_status (Parcel Panel) + feedback (Gorgias) +
kori_snapshots + weather_history. As of 2026-05-14 this is the sole
canonical DB; `ShippingReports/output/shipments.db` and
`GelPackCalculator/shipping.db` (0-byte stub) are deprecated.

Override
========
Set `APPYHOUR_DB_PATH` env var to point at a different file (useful for
testing, scratch analysis, or future multi-machine setups). The override
is honored only if the file exists; otherwise we fall back to canonical.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["db_path", "db_dir", "appyhour_appdata", "invoices_dir"]


def appyhour_appdata() -> Path:
    """Return %APPDATA%\\AppyHour as a Path. Creates if missing."""
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    p = base / "AppyHour"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_dir() -> Path:
    """Return the directory containing shipping.db. Always exists."""
    return appyhour_appdata()


def db_path() -> Path:
    """Return the canonical shipping.db path.

    Honors APPYHOUR_DB_PATH env override if the override file exists.
    """
    override = os.environ.get("APPYHOUR_DB_PATH", "").strip()
    if override:
        p = Path(override)
        if p.exists():
            return p
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
