"""LTF sheet reader — RECIPE_BOX, RECIPE_TRAY, INV_TOTAL_BOX, INV_TOTAL_TRAY.

LTF tabs are formula-driven. Read with valueRenderOption=UNFORMATTED_VALUE
so we get computed numbers, not formula strings.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

from .creds import get_google_credentials_path

_SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_LTF_SHEET_ID = os.environ.get(
    "LTF_SHEET_ID",
    "1Obz-Ib6KsjhB83NiRlFFCLFk9UjHr_YZl8lONl6VzKw",
)

# Module-level cache: 5min TTL (per plan section 4 Phase 4)
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300.0


def _service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        get_google_credentials_path(),
        scopes=_SHEETS_SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _read_range(sheet_range: str) -> list[list]:
    cache_key = f"range::{sheet_range}"
    cached = _CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1]

    svc = _service()
    resp = svc.spreadsheets().values().get(
        spreadsheetId=_LTF_SHEET_ID,
        range=sheet_range,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    rows = resp.get("values", [])
    _CACHE[cache_key] = (time.time(), rows)
    return rows


def _to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _yield_pct(v: Any, default: float = 1.0) -> float:
    """RECIPE_TRAY yield is e.g. '80%' or 0.8 or 100. Return as fraction."""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        f = float(v)
        return f / 100.0 if f > 1.5 else f
    s = str(v).strip()
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return default
    try:
        f = float(s)
        return f / 100.0 if f > 1.5 else f
    except ValueError:
        return default


@dataclass(frozen=True)
class RawToProcessed:
    raw_name: str      # e.g. "Cheese Wheel, Honey Clover Gouda"
    pack_size: float   # e.g. 20.0
    uom: str           # e.g. "lb"
    processed_sku: str # e.g. "CH-HCGU"
    oz_per_unit: float # ounces per processed unit (e.g. 6.0)
    conversion: float  # processed / raw pack
    yield_pct: float   # fraction (default 1.0 if blank)


@dataclass(frozen=True)
class TrayComponent:
    tray_sku: str        # e.g. "TR-DGG"
    component_type: str  # e.g. "Cheese"
    component_sku: str   # e.g. "CH-HCGU"
    raw_ingredient: str  # e.g. "Cheese Wheel, Honey Clover Gouda"
    oz: float            # oz per tray
    yield_pct: float     # fraction


def read_recipe_box() -> dict[str, RawToProcessed]:
    """Parse RECIPE_BOX Section 1. Returns {processed_sku: RawToProcessed}."""
    rows = _read_range("RECIPE_BOX!A1:H200")
    out: dict[str, RawToProcessed] = {}
    # Find header row containing "Processed SKU"
    header_row = None
    for i, r in enumerate(rows):
        if r and any(str(c).strip() == "Processed SKU" for c in r):
            header_row = i
            break
    if header_row is None:
        return out
    for r in rows[header_row + 1:]:
        if not r or len(r) < 4:
            continue
        raw_name = str(r[0]).strip() if len(r) > 0 else ""
        processed_sku = str(r[3]).strip().upper() if len(r) > 3 else ""
        if not processed_sku or not raw_name:
            continue
        # Section 2 (UNMAPPED) header sentinel — stop
        if processed_sku.startswith("2.") or "UNMAPPED" in raw_name.upper():
            break
        out[processed_sku] = RawToProcessed(
            raw_name=raw_name,
            pack_size=_to_float(r[1]) if len(r) > 1 else 0.0,
            uom=str(r[2]).strip() if len(r) > 2 else "",
            processed_sku=processed_sku,
            oz_per_unit=_to_float(r[4]) if len(r) > 4 else 0.0,
            conversion=_to_float(r[5]) if len(r) > 5 else 0.0,
            yield_pct=_yield_pct(r[6] if len(r) > 6 else None),
        )
    return out


def read_recipe_tray() -> dict[str, list[TrayComponent]]:
    """Parse RECIPE_TRAY. Returns {tray_sku: [TrayComponent, ...]}."""
    rows = _read_range("RECIPE_TRAY!A1:H500")
    out: dict[str, list[TrayComponent]] = {}
    header_row = None
    for i, r in enumerate(rows):
        if r and any(str(c).strip() == "Tray SKU" for c in r):
            header_row = i
            break
    if header_row is None:
        return out
    for r in rows[header_row + 1:]:
        if not r or len(r) < 6:
            continue
        tray_sku = str(r[1]).strip().upper() if len(r) > 1 else ""
        component_sku = str(r[3]).strip().upper() if len(r) > 3 else ""
        if not tray_sku or not component_sku:
            continue
        out.setdefault(tray_sku, []).append(
            TrayComponent(
                tray_sku=tray_sku,
                component_type=str(r[2]).strip() if len(r) > 2 else "",
                component_sku=component_sku,
                raw_ingredient=str(r[4]).strip() if len(r) > 4 else "",
                oz=_to_float(r[5]) if len(r) > 5 else 0.0,
                yield_pct=_yield_pct(r[6] if len(r) > 6 else None),
            )
        )
    return out


def read_inv_total_box() -> dict[str, float]:
    """Parse INV_TOTAL_BOX. Returns {processed_sku: total_available_units}.

    Total = Raw→Processed (col F) + INV_PROC (col G). Sheet computes; we sum cols.
    Falls back to col F if col G blank.
    """
    rows = _read_range("INV_TOTAL_BOX!A1:H300")
    out: dict[str, float] = {}
    header_row = None
    for i, r in enumerate(rows):
        if r and any(str(c).strip() == "Processed SKU" for c in r):
            header_row = i
            break
    if header_row is None:
        return out
    for r in rows[header_row + 1:]:
        if not r or len(r) < 4:
            continue
        sku = str(r[3]).strip().upper() if len(r) > 3 else ""
        if not sku:
            continue
        raw_to_proc = _to_float(r[5]) if len(r) > 5 else 0.0
        inv_proc = _to_float(r[6]) if len(r) > 6 else 0.0
        out[sku] = raw_to_proc + inv_proc
    return out


def read_inv_total_tray() -> dict[str, float]:
    """Parse INV_TOTAL_TRAY. Returns {raw_ingredient_name: tray_on_hand_lbs}.

    Schema: A=Component Type, B=Raw Ingredient, C=Pack Size, D=UoM,
            E=Tray Units (Tray%-alloc), F=Tray On-Hand (lbs), G=Used By (TR-*).
    """
    rows = _read_range("INV_TOTAL_TRAY!A1:H400")
    out: dict[str, float] = {}
    header_row = None
    for i, r in enumerate(rows):
        if r and any(str(c).strip() == "Raw Ingredient" for c in r):
            header_row = i
            break
    if header_row is None:
        return out
    for r in rows[header_row + 1:]:
        if not r or len(r) < 6:
            continue
        raw = str(r[1]).strip() if len(r) > 1 else ""
        if not raw:
            continue
        lbs = _to_float(r[5]) if len(r) > 5 else 0.0
        out[raw] = lbs
    return out


def snapshot_date() -> str | None:
    """Read the Snapshot date cell from INV_TOTAL_BOX (row 4, A:B).

    Cell is formula-rendered as a Sheets serial date (days since 1899-12-30).
    """
    from datetime import date, timedelta
    rows = _read_range("INV_TOTAL_BOX!A4:B4")
    if not rows or len(rows[0]) < 2:
        return None
    val = rows[0][1]
    if isinstance(val, (int, float)):
        try:
            return (date(1899, 12, 30) + timedelta(days=int(val))).isoformat()
        except (OverflowError, ValueError):
            return None
    return str(val)


def read_all() -> dict:
    """Single LTF batch — used as one frozen snapshot per run (per plan risk #9)."""
    return {
        "recipe_box": read_recipe_box(),
        "recipe_tray": read_recipe_tray(),
        "inv_total_box": read_inv_total_box(),
        "inv_total_tray": read_inv_total_tray(),
        "snapshot_date": snapshot_date(),
    }
