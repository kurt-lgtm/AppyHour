#!/usr/bin/env python

# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client", "google-auth", "openpyxl", "requests"]
# ///

"""
Cut Order Excel v2 — three-tab polished workbook.

Tab 1: "Cut Order" — urgency-grouped SKU demand with SUMIF-linked assignments
Tab 2: "Assignments" — PR-CJAM, CEX-EC, and MONTHLY slot tables
Tab 3: "Raw Materials" — cheese wheels and bulk accompaniment potential

Pulls demand from Recharge (subscriptions) and Shopify (orders).
"""

from __future__ import annotations

import argparse
import csv as _csv
import io
import json
import os
import re as _re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import openpyxl
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from inventory_demand_report import (
    INV_CSV,
    PICKABLE_PREFIXES,
    SAT_DEPLETION,
    TUE_DEPLETION,
    WK1_END,
    WK1_START,
    WK2_END,
    WK2_START,
    fetch_recharge_api,
    fetch_shopify_orders,
    load_inventory_csv,
    load_settings,
    parse_depletion_xlsx,
)
from fulfillment_web.invoice_processor import extract_bulk_weights

# ── Design Tokens ────────────────────────────────────────────────────

HEADER_BG = "1E293B"
HEADER_FG = "FFFFFF"
SHORTAGE_BG = "FEE2E2"
SHORTAGE_FG = "991B1B"
TIGHT_BG = "FEF3C7"
TIGHT_FG = "92400E"
OK_BG = "F0FDF4"
OK_FG = "166534"
INPUT_BG = "EEF2FF"
INPUT_FG = "3730A3"
SURFACE = "F8FAFC"
MUTED = "94A3B8"
WHEEL_POT = "7C3AED"
SECTION_ACCENT = "0F172A"

# ── Fonts ────────────────────────────────────────────────────────────

F_SKU = Font(name="Space Mono", size=10)
F_NAME = Font(name="DM Sans", size=10)
F_NUM = Font(name="Rajdhani", size=12)
F_NUM_BOLD = Font(name="Rajdhani", size=12, bold=True)
F_NUM_MUTED = Font(name="Rajdhani", size=12, color=MUTED)
F_NUM_WHEEL = Font(name="Rajdhani", size=12, color=WHEEL_POT)
F_HDR = Font(name="Space Mono", size=11, bold=True, color=HEADER_FG)
F_SECTION = Font(name="Space Mono", size=10, bold=True)
F_INPUT = Font(name="Rajdhani", size=12, bold=True, color=INPUT_FG)
F_GOOD = Font(name="Space Mono", size=9, bold=True)
F_EDIT = Font(name="DM Sans", size=10, bold=True, color=INPUT_FG)
F_TITLE = Font(name="Space Mono", size=14, bold=True, color=HEADER_FG)
F_SUBTITLE = Font(name="DM Sans", size=10, color=MUTED)

# ── Fills ────────────────────────────────────────────────────────────

FILL_HEADER = PatternFill("solid", fgColor=HEADER_BG)
FILL_SHORTAGE = PatternFill("solid", fgColor=SHORTAGE_BG)
FILL_TIGHT = PatternFill("solid", fgColor=TIGHT_BG)
FILL_OK = PatternFill("solid", fgColor=OK_BG)
FILL_INPUT = PatternFill("solid", fgColor=INPUT_BG)
FILL_SURFACE = PatternFill("solid", fgColor=SURFACE)

# ── Alignment ────────────────────────────────────────────────────────

A_RIGHT = Alignment(horizontal="right", vertical="center")
A_CENTER = Alignment(horizontal="center", vertical="center")
A_LEFT = Alignment(horizontal="left", vertical="center")


# ── Helpers ──────────────────────────────────────────────────────────


def _sku_category(sku: str) -> str:
    if sku.startswith("CH-"):
        return "CHEESE"
    if sku.startswith("MT-"):
        return "MEAT"
    if sku.startswith("AC-"):
        return "ACCOMPANIMENTS"
    return "OTHER"


def _set_col_widths(ws: Worksheet, widths: dict[int, float]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _dark_header_row(ws: Worksheet, row: int, headers: list[str], col_start: int = 1) -> None:
    for ci, h in enumerate(headers):
        c = ws.cell(row=row, column=col_start + ci, value=h)
        c.font = F_HDR
        c.fill = FILL_HEADER
        c.alignment = A_CENTER


def _merge_title_bar(ws: Worksheet, row: int, text: str, last_col: int) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
    c = ws.cell(row=row, column=1, value=text)
    c.font = F_TITLE
    c.fill = FILL_HEADER
    c.alignment = A_LEFT
    # Fill merged range
    for ci in range(2, last_col + 1):
        ws.cell(row=row, column=ci).fill = FILL_HEADER


def _merge_subtitle(ws: Worksheet, row: int, text: str, last_col: int) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
    c = ws.cell(row=row, column=1, value=text)
    c.font = F_SUBTITLE
    c.fill = FILL_HEADER
    c.alignment = A_LEFT
    for ci in range(2, last_col + 1):
        ws.cell(row=row, column=ci).fill = FILL_HEADER


def _section_header(ws: Worksheet, row: int, text: str, bg: str, fg: str, last_col: int) -> None:
    fill = PatternFill("solid", fgColor=bg)
    font = Font(name="Space Mono", size=10, bold=True, color=fg)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
    c = ws.cell(row=row, column=1, value=text)
    c.font = font
    c.fill = fill
    for ci in range(2, last_col + 1):
        ws.cell(row=row, column=ci).fill = fill


# ── Data Fetching (copied from v1 with cleanup) ─────────────────────


def _fetch_all_data(settings: dict) -> dict:
    """Fetch inventory, Recharge, and Shopify data. Returns a dict of all data needed."""
    sku_translations = settings.get("sku_translations", {})
    recharge_token = settings.get("recharge_api_token", "")
    inv_settings = settings.get("inventory", {})

    def sku_name(sku: str) -> str:
        data = inv_settings.get(sku, {})
        return data.get("name", "") if isinstance(data, dict) else ""

    # -- Load inventory --
    print("Loading inventory...")
    try:
        inventory = load_inventory_csv(INV_CSV)
    except (KeyError, ValueError):
        inventory = {}

    if not inventory:
        with open(INV_CSV, encoding="utf-8-sig") as _f:
            reader = _csv.reader(_f)
            hdr = next(reader)
            avail_col = next((i for i, h in enumerate(hdr) if "available" in (h or "").lower()), 4)
            for row in reader:
                sku = (row[0] if row else "").strip()
                if sku and sku.startswith(PICKABLE_PREFIXES):
                    try:
                        inventory[sku] = int(float(row[avail_col] or 0))
                    except (ValueError, IndexError):
                        pass
        print(f"  Loaded {len(inventory)} SKUs from template check (col {avail_col})")

    # -- Load CSV as DictReader for bulk weights --
    csv_rows: list[dict] = []
    try:
        with open(INV_CSV, encoding="utf-8-sig") as _f:
            csv_rows = list(_csv.DictReader(_f))
    except Exception as e:
        print(f"  Warning: Could not load CSV as DictReader: {e}")

    bulk_weights = extract_bulk_weights(csv_rows) if csv_rows else {}

    # -- Depletions --
    print("Parsing depletions...")
    sat_dep: dict[str, int] = {}
    tue_dep: dict[str, int] = {}
    if SAT_DEPLETION:
        sat_dep, _, _, _, _ = parse_depletion_xlsx(SAT_DEPLETION, sku_translations)
    if TUE_DEPLETION:
        tue_dep, _, _, _, _ = parse_depletion_xlsx(TUE_DEPLETION, sku_translations)

    available: dict[str, int] = {}
    all_inv_skus = set(inventory.keys()) | set(sat_dep.keys()) | set(tue_dep.keys())
    for sku in all_inv_skus:
        available[sku] = inventory.get(sku, 0) - sat_dep.get(sku, 0) - tue_dep.get(sku, 0)

    # -- Recharge --
    print("Fetching Recharge charges...")
    (
        rc_wk1,
        rc_wk2,
        rc_wk1_curations,
        rc_wk2_curations,
        rc_wk1_large,
        rc_wk2_large,
        _,
        _,
        rc_wk1_med_monthly,
        rc_wk2_med_monthly,
        rc_wk1_cmed_monthly,
        rc_wk2_cmed_monthly,
        rc_wk1_lge_monthly,
        rc_wk2_lge_monthly,
        monthly_by_week_month,
    ) = fetch_recharge_api(recharge_token)

    rc_wk1_total = sum(rc_wk1.values())
    rc_wk2_total = sum(rc_wk2.values())
    rc_wk1_curs = sum(rc_wk1_curations.values())
    rc_wk2_curs = sum(rc_wk2_curations.values())
    print(f"  Recharge WK1: {rc_wk1_total} pickable SKUs, {rc_wk1_curs} curation charges")
    print(f"  Recharge WK2: {rc_wk2_total} pickable SKUs, {rc_wk2_curs} curation charges")

    # -- Shopify --
    print("Fetching Shopify orders...")
    (
        sh_wk1_addon,
        sh_wk2_addon,
        sh_wk1_curations,
        sh_wk2_curations,
        sh_wk1_large,
        sh_wk2_large,
        sh_wk1_med,
        sh_wk2_med,
        sh_wk1_lge,
        sh_wk2_lge,
    ) = fetch_shopify_orders(settings)

    sh_wk1_total = sum(sh_wk1_addon.values())
    sh_wk2_total = sum(sh_wk2_addon.values())
    print(f"  Shopify WK1: {sh_wk1_total} addon SKUs, {sum(sh_wk1_curations.values())} curations")
    print(f"  Shopify WK2: {sh_wk2_total} addon SKUs, {sum(sh_wk2_curations.values())} curations")

    # -- First-order projection (MONG) --
    import requests as _req

    store_url = settings.get("shopify_store_url", "")
    shop_token = settings.get("shopify_access_token", "")
    fo_count = 0
    if store_url and shop_token:
        if not store_url.startswith("http"):
            store_url = f"https://{store_url}.myshopify.com"
        _cutoff = (datetime.now() - timedelta(days=3)).isoformat()
        _fo_url = f"{store_url}/admin/api/2024-01/orders.json"
        _fo_params = {
            "status": "any",
            "limit": 250,
            "created_at_min": _cutoff,
            "fields": "id,tags,line_items",
        }
        _fo_orders: list[dict] = []
        _fo_page_url: str | None = _fo_url
        while _fo_page_url:
            _fo_resp = _req.get(
                _fo_page_url,
                headers={"X-Shopify-Access-Token": shop_token, "Content-Type": "application/json"},
                params=_fo_params if _fo_page_url == _fo_url else None,
                timeout=30,
            )
            _fo_data = _fo_resp.json()
            for _o in _fo_data.get("orders", []):
                if "Subscription First Order" in (_o.get("tags") or ""):
                    _fo_orders.append(_o)
            _fo_page_url = None
            _link = _fo_resp.headers.get("Link", "")
            if 'rel="next"' in _link:
                _m = _re.search(r'<([^>]+)>;\s*rel="next"', _link)
                if _m:
                    _fo_page_url = _m.group(1)
            time.sleep(0.3)

        fo_count = len(_fo_orders)
        _daily_rate = fo_count / 3.0 if _fo_orders else 0
        _days_to_friday = max(0, (5 - datetime.now().weekday()))
        _projected = int(_daily_rate * _days_to_friday)

        _mong_fo = [o for o in _fo_orders if any("MONG" in (li.get("sku") or "") for li in o.get("line_items", []))]
        _fo_skus: dict[str, float] = defaultdict(float)
        if _mong_fo:
            for _o in _mong_fo:
                for _li in _o.get("line_items", []):
                    _sku = (_li.get("sku") or "").strip()
                    if _sku.startswith(PICKABLE_PREFIXES):
                        _fo_skus[_sku] += (_li.get("quantity", 1)) / len(_mong_fo)

        _mong_pct = len(_mong_fo) / fo_count if fo_count else 0
        _mong_projected = int(_projected * _mong_pct)
        print(
            f"  First-order projection: {fo_count} in 3d, "
            f"{_daily_rate:.0f}/day, {_projected} projected, "
            f"{_mong_projected} MONG ({_mong_pct:.0%})"
        )
        for _sku, _rate in _fo_skus.items():
            _add = int(_rate * _mong_projected)
            if _add > 0:
                sh_wk1_addon[_sku] = sh_wk1_addon.get(_sku, 0) + _add

        if _mong_projected > 0:
            _sh_mong_lg = sh_wk1_large.get("MONG", 0)
            _sh_mong_total = sh_wk1_curations.get("MONG", 1)
            _lg_ratio = _sh_mong_lg / _sh_mong_total if _sh_mong_total > 0 else 0.0
            _proj_lg = int(_mong_projected * _lg_ratio)
            sh_wk1_curations["MONG"] = sh_wk1_curations.get("MONG", 0) + _mong_projected
            sh_wk1_large["MONG"] = sh_wk1_large.get("MONG", 0) + _proj_lg

    # -- Merge curation counts --
    wk1_curations: dict[str, int] = defaultdict(int)
    wk2_curations: dict[str, int] = defaultdict(int)
    wk1_large: dict[str, int] = defaultdict(int)
    wk2_large: dict[str, int] = defaultdict(int)
    for d_rc, d_sh, d_out in [
        (rc_wk1_curations, sh_wk1_curations, wk1_curations),
        (rc_wk2_curations, sh_wk2_curations, wk2_curations),
        (rc_wk1_large, sh_wk1_large, wk1_large),
        (rc_wk2_large, sh_wk2_large, wk2_large),
    ]:
        for k, v in d_rc.items():
            d_out[k] += v
        for k, v in d_sh.items():
            d_out[k] += v

    # Box size counts per curation
    wk1_med: dict[str, int] = defaultdict(int, sh_wk1_med)
    wk2_med: dict[str, int] = defaultdict(int, sh_wk2_med)
    wk1_lge: dict[str, int] = defaultdict(int, sh_wk1_lge)
    wk2_lge: dict[str, int] = defaultdict(int, sh_wk2_lge)
    for cur, ct in rc_wk1_curations.items():
        lg = rc_wk1_large.get(cur, 0)
        wk1_med[cur] += ct - lg
        wk1_lge[cur] += lg
    for cur, ct in rc_wk2_curations.items():
        lg = rc_wk2_large.get(cur, 0)
        wk2_med[cur] += ct - lg
        wk2_lge[cur] += lg

    # MONTHLY boxes
    wk1_med["MONTHLY"] = wk1_med.get("MONTHLY", 0) + rc_wk1_med_monthly
    wk2_med["MONTHLY"] = wk2_med.get("MONTHLY", 0) + rc_wk2_med_monthly
    wk1_med["CMED"] = wk1_med.get("CMED", 0) + rc_wk1_cmed_monthly
    wk2_med["CMED"] = wk2_med.get("CMED", 0) + rc_wk2_cmed_monthly
    wk1_lge["MONTHLY"] = wk1_lge.get("MONTHLY", 0) + rc_wk1_lge_monthly
    wk2_lge["MONTHLY"] = wk2_lge.get("MONTHLY", 0) + rc_wk2_lge_monthly

    # Active pickable SKUs
    report_skus: set[str] = set()
    for d in (available, rc_wk1, rc_wk2, sh_wk1_addon, sh_wk2_addon):
        report_skus.update(d.keys())
    active_skus = sorted(
        sku
        for sku in report_skus
        if any(sku.startswith(p) for p in PICKABLE_PREFIXES)
        and (
            available.get(sku, 0) != 0
            or rc_wk1.get(sku, 0) > 0
            or rc_wk2.get(sku, 0) > 0
            or sh_wk1_addon.get(sku, 0) > 0
            or sh_wk2_addon.get(sku, 0) > 0
        )
    )

    all_curations = sorted(
        set(
            list(wk1_curations.keys())
            + list(wk2_curations.keys())
            + list(wk1_large.keys())
            + list(wk2_large.keys())
            + list(wk1_med.keys())
            + list(wk2_med.keys())
            + list(wk1_lge.keys())
            + list(wk2_lge.keys())
        )
    )

    return {
        "available": available,
        "rc_wk1": rc_wk1,
        "rc_wk2": rc_wk2,
        "sh_wk1_addon": sh_wk1_addon,
        "sh_wk2_addon": sh_wk2_addon,
        "wk1_curations": dict(wk1_curations),
        "wk2_curations": dict(wk2_curations),
        "wk1_large": dict(wk1_large),
        "wk2_large": dict(wk2_large),
        "wk1_med": dict(wk1_med),
        "wk2_med": dict(wk2_med),
        "wk1_lge": dict(wk1_lge),
        "wk2_lge": dict(wk2_lge),
        "monthly_by_week_month": monthly_by_week_month,
        "active_skus": active_skus,
        "all_curations": all_curations,
        "sku_name": sku_name,
        "bulk_weights": bulk_weights,
        "csv_rows": csv_rows,
        "rc_wk1_curs": rc_wk1_curs,
        "rc_wk2_curs": rc_wk2_curs,
        "sh_wk1_total": sum(sh_wk1_curations.values()),
        "sh_wk2_total": sum(sh_wk2_curations.values()),
    }


# ── Tab 2: Assignments ──────────────────────────────────────────────

# Slot definitions for MONTHLY boxes
AHB_MED_SLOTS = [
    ("Cheese 1", "CH-"),
    ("Cheese 2", "CH-"),
    ("Meat 1", "MT-"),
    ("Meat 2", "MT-"),
    ("Crackers", "AC-"),
    ("Accompaniment 1", "AC-"),
    ("Accompaniment 2", "AC-"),
    ("PR-CJAM-GEN Cheese", "CH-"),
    ("PR-CJAM-GEN Jam", "AC-"),
]
AHB_CMED_SLOTS = [
    ("Cheese 1", "CH-"),
    ("Cheese 2", "CH-"),
    ("Cheese 3", "CH-"),
    ("Cheese 4", "CH-"),
    ("Crackers", "AC-"),
    ("Accompaniment 1", "AC-"),
    ("Accompaniment 2", "AC-"),
    ("PR-CJAM-GEN Cheese", "CH-"),
    ("PR-CJAM-GEN Jam", "AC-"),
]
AHB_LGE_SLOTS = [
    ("Cheese 1", "CH-"),
    ("Cheese 2", "CH-"),
    ("Cheese 3", "CH-"),
    ("Meat 1", "MT-"),
    ("Meat 2", "MT-"),
    ("Meat 3", "MT-"),
    ("Crackers", "AC-"),
    ("Accompaniment 1", "AC-"),
    ("Accompaniment 2", "AC-"),
    ("PR-CJAM-GEN Cheese", "CH-"),
    ("PR-CJAM-GEN Jam", "AC-"),
]


def _write_assignments_on_cut_order(ws: Worksheet, data: dict, settings: dict) -> int:
    """Write assignment tables onto the Cut Order tab starting at column W (23).

    Layout:
      V (22) = spacer
      W-Z (23-26) = PR-CJAM table (Curation, Cheese SKU, W1 Count, W2 Count)
      AA (27) = spacer
      AB-AE (28-31) = CEX-EC table (Curation, Cheese SKU, W1 Lg Count, W2 Lg Count)
      MONTHLY slot tables below PR-CJAM/CEX-EC (whichever is taller), in W-Z area.

    PR-CJAM cheese SKU col = X (24), counts = Y (25), Z (26)
    CEX-EC cheese SKU col  = AC (29), counts = AD (30), AE (31)

    MONTHLY slot SKU col = X (24), counts = Y (25), Z (26) — contiguous with PR-CJAM.

    Returns the last row of the contiguous SKU range (PR-CJAM + CEX-EC + MONTHLY slots)
    used for SUMIF lookups. The SUMIF range covers col X for SKU, Y for W1, Z for W2
    across PR-CJAM rows AND MONTHLY slot rows (one contiguous block).
    CEX-EC is separate (col AC for SKU, AD for W1, AE for W2).
    """
    pr_cjam_cfg = settings.get("pr_cjam", {})
    cex_ec_cfg = settings.get("cex_ec", {})
    all_curations = data["all_curations"]
    wk1_curations = data["wk1_curations"]
    wk2_curations = data["wk2_curations"]
    wk1_large = data["wk1_large"]
    wk2_large = data["wk2_large"]
    monthly_by_week_month = data["monthly_by_week_month"]

    # Column constants
    COL_V = 22  # spacer
    COL_W = 23  # PR-CJAM: Curation
    COL_X = 24  # PR-CJAM: Cheese SKU (SUMIF lookup)
    COL_Y = 25  # PR-CJAM: W1 Count
    COL_Z = 26  # PR-CJAM: W2 Count
    COL_AA = 27  # spacer
    COL_AB = 28  # CEX-EC: Curation
    COL_AC = 29  # CEX-EC: Cheese SKU (SUMIF lookup)
    COL_AD = 30  # CEX-EC: W1 Lg Count
    COL_AE = 31  # CEX-EC: W2 Lg Count

    # Column widths for assignment area
    _set_col_widths(
        ws,
        {
            COL_V: 2,
            COL_W: 14,
            COL_X: 18,
            COL_Y: 10,
            COL_Z: 10,
            COL_AA: 2,
            COL_AB: 14,
            COL_AC: 18,
            COL_AD: 10,
            COL_AE: 10,
        },
    )

    # MONTHLY and CMED use PR-CJAM-GEN (handled by MONTHLY slot tables below),
    # so exclude them from the per-curation PR-CJAM and CEX-EC assignment rows.
    PRCJAM_GEN_CURATIONS = {"MONTHLY", "CMED"}
    prcjam_curations = [c for c in all_curations if c not in PRCJAM_GEN_CURATIONS]

    # ── PR-CJAM Section (W-Z, starting row 1) ──
    _dark_header_row(ws, 1, ["PR-CJAM ASSIGNMENTS", "", "", ""], col_start=COL_W)
    _dark_header_row(ws, 2, ["Curation", "Cheese SKU", "W1 Count", "W2 Count"], col_start=COL_W)

    prcjam_data_start = 3
    for i, cur in enumerate(prcjam_curations):
        r = prcjam_data_start + i
        cheese = ""
        cfg = pr_cjam_cfg.get(cur)
        if isinstance(cfg, dict):
            cheese = cfg.get("cheese", "")
        ws.cell(row=r, column=COL_W, value=cur).font = F_NAME
        c_cheese = ws.cell(row=r, column=COL_X, value=cheese)
        c_cheese.font = F_EDIT
        c_cheese.fill = FILL_INPUT
        ws.cell(row=r, column=COL_Y, value=wk1_curations.get(cur, 0)).font = F_NUM
        ws.cell(row=r, column=COL_Y).alignment = A_RIGHT
        ws.cell(row=r, column=COL_Z, value=wk2_curations.get(cur, 0)).font = F_NUM
        ws.cell(row=r, column=COL_Z).alignment = A_RIGHT

    prcjam_end = prcjam_data_start + len(prcjam_curations) - 1

    # ── CEX-EC Section (AB-AE, starting row 1) ──
    _dark_header_row(ws, 1, ["CEX-EC ASSIGNMENTS", "", "", ""], col_start=COL_AB)
    _dark_header_row(ws, 2, ["Curation", "Cheese SKU", "W1 Lg Count", "W2 Lg Count"], col_start=COL_AB)

    cexec_data_start = 3
    for i, cur in enumerate(prcjam_curations):
        r = cexec_data_start + i
        cheese = cex_ec_cfg.get(cur, "")
        ws.cell(row=r, column=COL_AB, value=cur).font = F_NAME
        c_cheese = ws.cell(row=r, column=COL_AC, value=cheese)
        c_cheese.font = F_EDIT
        c_cheese.fill = FILL_INPUT
        ws.cell(row=r, column=COL_AD, value=wk1_large.get(cur, 0)).font = F_NUM
        ws.cell(row=r, column=COL_AD).alignment = A_RIGHT
        ws.cell(row=r, column=COL_AE, value=wk2_large.get(cur, 0)).font = F_NUM
        ws.cell(row=r, column=COL_AE).alignment = A_RIGHT

    cexec_end = cexec_data_start + len(prcjam_curations) - 1

    # ── MONTHLY Box Slot Tables (below PR-CJAM in W-Z columns) ──
    # These go right after PR-CJAM rows so the SKU column (X) is contiguous
    # with PR-CJAM cheese SKUs for a single SUMIF range.
    month_counts: dict[str, dict[str, dict[str, int]]] = {}
    for (week, month), counts in monthly_by_week_month.items():
        if month not in month_counts:
            month_counts[month] = {
                "MED": {"wk1": 0, "wk2": 0},
                "CMED": {"wk1": 0, "wk2": 0},
                "LGE": {"wk1": 0, "wk2": 0},
            }
        wk_key = "wk1" if week == "WK1" else "wk2"
        for box_type in ("MED", "CMED", "LGE"):
            month_counts[month][box_type][wk_key] += counts.get(box_type, 0)

    # Start MONTHLY slots right after PR-CJAM data (no gap — contiguous for SUMIF)
    slot_row = prcjam_end + 1

    def _write_slot_table(
        ws_: Worksheet,
        start_row: int,
        label: str,
        slots: list[tuple[str, str]],
        w1_count: int,
        w2_count: int,
        col_start: int,
    ) -> int:
        ws_.merge_cells(
            start_row=start_row,
            start_column=col_start,
            end_row=start_row,
            end_column=col_start + 3,
        )
        c = ws_.cell(row=start_row, column=col_start, value=f"{label} ({w1_count} W1 / {w2_count} W2)")
        c.font = Font(name="Space Mono", size=10, bold=True, color=OK_FG)
        c.fill = PatternFill("solid", fgColor=OK_BG)
        for ci in range(col_start + 1, col_start + 4):
            ws_.cell(row=start_row, column=ci).fill = PatternFill("solid", fgColor=OK_BG)
        r = start_row
        for slot_name, _prefix in slots:
            r += 1
            ws_.cell(row=r, column=col_start, value=slot_name).font = F_NAME
            c_sku = ws_.cell(row=r, column=col_start + 1)
            c_sku.font = F_EDIT
            c_sku.fill = FILL_INPUT
            ws_.cell(row=r, column=col_start + 2, value=w1_count).font = F_NUM
            ws_.cell(row=r, column=col_start + 2).alignment = A_RIGHT
            ws_.cell(row=r, column=col_start + 3, value=w2_count).font = F_NUM
            ws_.cell(row=r, column=col_start + 3).alignment = A_RIGHT
        return r

    for month in sorted(month_counts.keys()):
        mc = month_counts[month]
        for box_type, slots, label_prefix in [
            ("MED", AHB_MED_SLOTS, "AHB-MED"),
            ("CMED", AHB_CMED_SLOTS, "AHB-CMED"),
            ("LGE", AHB_LGE_SLOTS, "AHB-LGE"),
        ]:
            if mc[box_type]["wk1"] + mc[box_type]["wk2"] > 0:
                slot_row = _write_slot_table(
                    ws,
                    slot_row,
                    f"{label_prefix} ({month})",
                    slots,
                    mc[box_type]["wk1"],
                    mc[box_type]["wk2"],
                    col_start=COL_W,
                )
                slot_row += 1  # next table starts on the row after last slot row
                # Note: no extra gap — keep contiguous for SUMIF

    # The last data row in the W-Z contiguous block (PR-CJAM + MONTHLY slots)
    prcjam_monthly_last_row = max(slot_row - 1, prcjam_end)

    # ── MONTHLY Box Counts summary (below the taller of the two table groups) ──
    summary_start = max(prcjam_monthly_last_row, cexec_end) + 3
    if monthly_by_week_month:
        _dark_header_row(ws, summary_start, ["MONTHLY BOX COUNTS", "", "", ""], col_start=COL_W)
        summary_start += 1
        _dark_header_row(ws, summary_start, ["Box Type", "Month", "W1 Count", "W2 Count"], col_start=COL_W)
        summary_start += 1
        for month in sorted(month_counts.keys()):
            mc = month_counts[month]
            for bt in ("MED", "CMED", "LGE"):
                if mc[bt]["wk1"] + mc[bt]["wk2"] > 0:
                    ws.cell(row=summary_start, column=COL_W, value=f"AHB-{bt}").font = F_NAME
                    ws.cell(row=summary_start, column=COL_X, value=month).font = F_NAME
                    ws.cell(row=summary_start, column=COL_Y, value=mc[bt]["wk1"]).font = F_NUM
                    ws.cell(row=summary_start, column=COL_Y).alignment = A_RIGHT
                    ws.cell(row=summary_start, column=COL_Z, value=mc[bt]["wk2"]).font = F_NUM
                    ws.cell(row=summary_start, column=COL_Z).alignment = A_RIGHT
                    summary_start += 1

    # Return info needed for SUMIF references:
    # prcjam_monthly_last_row = last row of contiguous PR-CJAM + MONTHLY slot SKU data in col X
    # cexec_end = last row of CEX-EC data in col AC
    return prcjam_monthly_last_row, cexec_end


# ── Tab 3: Raw Materials ─────────────────────────────────────────────


def _build_raw_materials_tab(wb: openpyxl.Workbook, data: dict, settings: dict) -> None:
    ws = wb.create_sheet("Raw Materials")
    bulk_weights = data["bulk_weights"]
    csv_rows = data["csv_rows"]
    sku_name_fn = data["sku_name"]
    bulk_conversions = settings.get("bulk_conversions", {})

    _set_col_widths(ws, {1: 28, 2: 14, 3: 14, 4: 14, 5: 16, 6: 10})

    # ── Cheese Wheels Section ──
    row = 1
    _dark_header_row(ws, row, ["CHEESE WHEELS & BLOCKS", "", "", "", "", ""])
    row = 2
    _dark_header_row(ws, row, ["Cheese Name", "SKU", "On Hand", "Weight (lbs)", "Potential Slices", "Status"])

    # Sort by potential yield descending
    wheel_items = sorted(bulk_weights.items(), key=lambda x: x[1].get("potential_yield", 0), reverse=True)
    row = 3
    for sku, wt in wheel_items:
        name = sku_name_fn(sku) or sku
        count = wt.get("count", 0)
        weight_lbs = wt.get("weight_lbs", 0)
        potential = wt.get("potential_yield", 0)

        if potential > 200:
            status = "HIGH"
            status_fill = PatternFill("solid", fgColor=OK_BG)
            status_font = Font(name="Space Mono", size=9, bold=True, color=OK_FG)
        elif potential >= 50:
            status = "MED"
            status_fill = PatternFill("solid", fgColor=TIGHT_BG)
            status_font = Font(name="Space Mono", size=9, bold=True, color=TIGHT_FG)
        elif potential > 0:
            status = "LOW"
            status_fill = PatternFill("solid", fgColor=SHORTAGE_BG)
            status_font = Font(name="Space Mono", size=9, bold=True, color=SHORTAGE_FG)
        else:
            status = "EMPTY"
            status_fill = PatternFill("solid", fgColor="F1F5F9")
            status_font = Font(name="Space Mono", size=9, bold=True, color=MUTED)

        ws.cell(row=row, column=1, value=name).font = F_NAME
        ws.cell(row=row, column=2, value=sku).font = F_SKU
        c_count = ws.cell(row=row, column=3, value=count)
        c_count.font = F_NUM
        c_count.alignment = A_RIGHT
        c_wt = ws.cell(row=row, column=4, value=round(weight_lbs * count, 1) if weight_lbs else 0)
        c_wt.font = F_NUM
        c_wt.alignment = A_RIGHT
        c_pot = ws.cell(row=row, column=5, value=potential)
        c_pot.font = Font(name="Rajdhani", size=12, bold=True, color=WHEEL_POT)
        c_pot.alignment = A_RIGHT
        c_st = ws.cell(row=row, column=6, value=status)
        c_st.font = status_font
        c_st.fill = status_fill
        c_st.alignment = A_CENTER
        row += 1

    if not wheel_items:
        ws.cell(row=row, column=1, value="No cheese wheel/block data found").font = F_NUM_MUTED
        row += 1

    # ── Bulk Accompaniments Section ──
    row += 2
    _dark_header_row(ws, row, ["BULK ACCOMPANIMENTS", "", "", "", "", ""])
    row += 1
    _dark_header_row(ws, row, ["Ingredient", "SKU", "On Hand (oz)", "Packet Size (oz)", "Potential Packets", "Status"])
    row += 1

    for ingredient_name, conv in sorted(bulk_conversions.items()):
        target_sku = conv.get("sku", "")
        packet_oz = conv.get("packet_oz", 1)

        # Scan CSV rows for this ingredient
        total_on_hand = 0.0
        for csv_row in csv_rows:
            ing = csv_row.get("Ingredient", "").strip()
            if ingredient_name.lower() in ing.lower():
                try:
                    raw_total = float(csv_row.get("Total", 0) or 0)
                except (ValueError, TypeError):
                    raw_total = 0
                # Check unit — convert lbs to oz if needed
                unit = csv_row.get("Unit1", "").strip().lower()
                if "lb" in unit:
                    total_on_hand += raw_total * 16.0
                elif "kg" in unit:
                    total_on_hand += raw_total * 35.274
                else:
                    total_on_hand += raw_total  # assume oz

        potential_packets = int(total_on_hand / packet_oz) if packet_oz > 0 else 0

        if potential_packets > 200:
            status = "HIGH"
            status_fill = PatternFill("solid", fgColor=OK_BG)
            status_font = Font(name="Space Mono", size=9, bold=True, color=OK_FG)
        elif potential_packets >= 50:
            status = "MED"
            status_fill = PatternFill("solid", fgColor=TIGHT_BG)
            status_font = Font(name="Space Mono", size=9, bold=True, color=TIGHT_FG)
        elif potential_packets > 0:
            status = "LOW"
            status_fill = PatternFill("solid", fgColor=SHORTAGE_BG)
            status_font = Font(name="Space Mono", size=9, bold=True, color=SHORTAGE_FG)
        else:
            status = "EMPTY"
            status_fill = PatternFill("solid", fgColor="F1F5F9")
            status_font = Font(name="Space Mono", size=9, bold=True, color=MUTED)

        ws.cell(row=row, column=1, value=ingredient_name).font = F_NAME
        ws.cell(row=row, column=2, value=target_sku).font = F_SKU
        c_oh = ws.cell(row=row, column=3, value=round(total_on_hand, 1))
        c_oh.font = F_NUM
        c_oh.alignment = A_RIGHT
        c_ps = ws.cell(row=row, column=4, value=packet_oz)
        c_ps.font = F_NUM
        c_ps.alignment = A_RIGHT
        c_pp = ws.cell(row=row, column=5, value=potential_packets)
        c_pp.font = Font(name="Rajdhani", size=12, bold=True, color=WHEEL_POT)
        c_pp.alignment = A_RIGHT
        c_st = ws.cell(row=row, column=6, value=status)
        c_st.font = status_font
        c_st.fill = status_fill
        c_st.alignment = A_CENTER
        row += 1

    if not bulk_conversions:
        ws.cell(row=row, column=1, value="No bulk conversions configured in settings").font = F_NUM_MUTED

    ws.freeze_panes = "A3"
    ws.sheet_properties.tabColor = "7C3AED"


# ── Tab 1: Cut Order (main sheet) ───────────────────────────────────


def _build_cut_order_tab(
    wb: openpyxl.Workbook,
    data: dict,
    settings: dict,
    prcjam_monthly_last_row: int,
    cexec_last_row: int,
) -> None:
    """Build the main Cut Order tab with urgency-grouped rows and inline assignments."""
    ws = wb.active
    ws.title = "Cut Order"
    ws.sheet_properties.tabColor = "1E293B"

    inv_settings = settings.get("inventory", {})
    pr_cjam_cfg = settings.get("pr_cjam", {})
    cex_ec_cfg = settings.get("cex_ec", {})

    available = data["available"]
    rc_wk1 = data["rc_wk1"]
    rc_wk2 = data["rc_wk2"]
    sh_wk1_addon = data["sh_wk1_addon"]
    sh_wk2_addon = data["sh_wk2_addon"]
    active_skus = data["active_skus"]
    sku_name_fn = data["sku_name"]
    bulk_weights = data["bulk_weights"]

    # Column widths: A-U
    _set_col_widths(
        ws,
        {
            1: 14,
            2: 30,
            3: 8,
            4: 9,
            5: 10,  # SKU, Name, Avail, Wheel Pot., Total Supply
            6: 1,  # spacer
            7: 8,
            8: 8,
            9: 8,
            10: 10,
            11: 10,
            12: 9,
            13: 9,  # W1: RC, SH, +Assign, =Demand, After, Cut, Good?
            14: 1,  # spacer
            15: 8,
            16: 8,
            17: 8,
            18: 10,
            19: 10,
            20: 9,
            21: 9,  # W2: RC, SH, +Assign, =Demand, After, Cut, Good?
        },
    )
    LAST_COL = 21

    # ── Title bar ──
    _merge_title_bar(ws, 1, f"CUT ORDER \u2014 Week of {WK1_END}", LAST_COL)

    rc_total = data["rc_wk1_curs"]
    sh_total = data["sh_wk1_total"]
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    _merge_subtitle(ws, 2, f"Generated {gen_time}  |  RC: {rc_total} charges  |  SH: {sh_total} orders", LAST_COL)

    # Row 3: blank
    # Row 4: column headers
    HDR_ROW = 4
    headers = [
        "SKU",
        "Name",
        "Avail",
        "Wheel Pot.",
        "Total Supply",
        "",  # spacer
        "RC W1",
        "SH W1",
        "+Assign W1",
        "=Demand W1",
        "After W1",
        "Cut W1",
        "Good?",
        "",  # spacer
        "RC W2",
        "SH W2",
        "+Assign W2",
        "=Demand W2",
        "After W2",
        "Cut W2",
        "Good?",
    ]
    _dark_header_row(ws, HDR_ROW, headers)

    # SUMIF references — same sheet, two ranges:
    # 1) PR-CJAM + MONTHLY slots: col X (SKU), Y (W1 count), Z (W2 count) rows 3..prcjam_monthly_last_row
    # 2) CEX-EC: col AC (SKU), AD (W1 lg count), AE (W2 lg count) rows 3..cexec_last_row
    # +Assign = SUMIF(pr_cjam_monthly_sku, A{row}, pr_cjam_monthly_w) + SUMIF(cexec_sku, A{row}, cexec_w)
    prcjam_sku_range = f"$X$3:$X${prcjam_monthly_last_row}"
    prcjam_w1_range = f"$Y$3:$Y${prcjam_monthly_last_row}"
    prcjam_w2_range = f"$Z$3:$Z${prcjam_monthly_last_row}"
    cexec_sku_range = f"$AC$3:$AC${cexec_last_row}"
    cexec_w1_range = f"$AD$3:$AD${cexec_last_row}"
    cexec_w2_range = f"$AE$3:$AE${cexec_last_row}"

    # ── Pre-compute urgency for each SKU ──
    # We need to calculate actual numeric values for sorting, not just formulas
    # For sorting: estimate assignment demand from settings (won't match SUMIF exactly but close enough)
    def _estimate_assign_demand(sku: str, curations: dict, large: dict, pr_cfg: dict, cex_cfg: dict) -> int:
        total = 0
        for cur, ct in curations.items():
            cfg = pr_cfg.get(cur)
            if isinstance(cfg, dict) and cfg.get("cheese") == sku:
                total += ct
        for cur, ct in large.items():
            if cex_cfg.get(cur) == sku:
                total += ct
        return total

    wk1_curs = data["wk1_curations"]
    wk2_curs = data["wk2_curations"]
    wk1_lg = data["wk1_large"]
    wk2_lg = data["wk2_large"]

    sku_rows: list[dict] = []
    for sku in active_skus:
        avail = available.get(sku, 0)
        wheel_pot = bulk_weights.get(sku, {}).get("potential_yield", 0)
        total_supply = avail + wheel_pot

        rc1 = rc_wk1.get(sku, 0)
        sh1 = sh_wk1_addon.get(sku, 0)
        assign1 = _estimate_assign_demand(sku, wk1_curs, wk1_lg, pr_cjam_cfg, cex_ec_cfg)
        demand1 = rc1 + sh1 + assign1

        rc2 = rc_wk2.get(sku, 0)
        sh2 = sh_wk2_addon.get(sku, 0)
        assign2 = _estimate_assign_demand(sku, wk2_curs, wk2_lg, pr_cjam_cfg, cex_ec_cfg)
        demand2 = rc2 + sh2 + assign2

        after1 = total_supply - demand1
        after2 = after1 - demand2  # after W1, no cut yet

        cat = _sku_category(sku)

        # Urgency classification
        if after1 < 0:
            urgency = "SHORTAGE"
        elif after1 - demand2 < 0:
            urgency = "TIGHT"
        else:
            urgency = "OK"

        sku_rows.append(
            {
                "sku": sku,
                "name": sku_name_fn(sku),
                "avail": avail,
                "wheel_pot": wheel_pot,
                "total_supply": total_supply,
                "rc1": rc1,
                "sh1": sh1,
                "rc2": rc2,
                "sh2": sh2,
                "demand1": demand1,
                "demand2": demand2,
                "after1": after1,
                "after2": after2,
                "cat": cat,
                "urgency": urgency,
                "sort_ratio": (after1 / demand1) if demand1 > 0 else 9999,
            }
        )

    # Category sort order
    CAT_ORDER = {"CHEESE": 0, "MEAT": 1, "ACCOMPANIMENTS": 2, "OTHER": 3}

    # Group by urgency
    shortages = [r for r in sku_rows if r["urgency"] == "SHORTAGE"]
    tight = [r for r in sku_rows if r["urgency"] == "TIGHT"]
    ok = [r for r in sku_rows if r["urgency"] == "OK"]

    # Sort within groups
    shortages.sort(key=lambda r: (CAT_ORDER.get(r["cat"], 9), r["after1"]))
    tight.sort(key=lambda r: (CAT_ORDER.get(r["cat"], 9), r["sort_ratio"]))
    ok.sort(key=lambda r: (CAT_ORDER.get(r["cat"], 9), r["sku"]))

    # ── Write rows ──
    row = HDR_ROW  # will increment before writing

    def _write_sku_row(ws_: Worksheet, row_num: int, sr: dict) -> None:
        sku = sr["sku"]
        # A: SKU
        ws_.cell(row=row_num, column=1, value=sku).font = F_SKU
        # B: Name
        ws_.cell(row=row_num, column=2, value=sr["name"]).font = F_NAME
        # C: Avail
        c_avail = ws_.cell(row=row_num, column=3, value=sr["avail"])
        c_avail.font = F_NUM
        c_avail.alignment = A_RIGHT
        # D: Wheel Pot.
        c_wp = ws_.cell(row=row_num, column=4, value=sr["wheel_pot"] if sr["wheel_pot"] > 0 else "")
        c_wp.font = F_NUM_WHEEL
        c_wp.alignment = A_RIGHT
        # E: Total Supply = C + D
        ws_[f"E{row_num}"] = f'=C{row_num}+IF(D{row_num}="",0,D{row_num})'
        c_ts = ws_.cell(row=row_num, column=5)
        c_ts.font = F_NUM_BOLD
        c_ts.alignment = A_RIGHT

        # F: spacer

        # G: RC W1
        c_rc1 = ws_.cell(row=row_num, column=7, value=sr["rc1"])
        c_rc1.font = F_NUM
        c_rc1.alignment = A_RIGHT
        # H: SH W1
        c_sh1 = ws_.cell(row=row_num, column=8, value=sr["sh1"])
        c_sh1.font = F_NUM
        c_sh1.alignment = A_RIGHT
        # I: +Assign W1 = SUMIF(PR-CJAM+MONTHLY) + SUMIF(CEX-EC)
        ws_[f"I{row_num}"] = (
            f"=SUMIF({prcjam_sku_range},A{row_num},{prcjam_w1_range})"
            f"+SUMIF({cexec_sku_range},A{row_num},{cexec_w1_range})"
        )
        ws_.cell(row=row_num, column=9).font = F_NUM
        ws_.cell(row=row_num, column=9).alignment = A_RIGHT
        # J: =Demand W1 = G + H + I
        ws_[f"J{row_num}"] = f"=G{row_num}+H{row_num}+I{row_num}"
        ws_.cell(row=row_num, column=10).font = F_NUM_BOLD
        ws_.cell(row=row_num, column=10).alignment = A_RIGHT
        # K: After W1 = E - J
        ws_[f"K{row_num}"] = f"=E{row_num}-J{row_num}"
        ws_.cell(row=row_num, column=11).font = F_NUM_BOLD
        ws_.cell(row=row_num, column=11).alignment = A_RIGHT
        # L: Cut W1 (input)
        ws_.cell(row=row_num, column=12).font = F_INPUT
        ws_.cell(row=row_num, column=12).fill = FILL_INPUT
        ws_.cell(row=row_num, column=12).alignment = A_RIGHT
        # M: Good? W1
        ws_[f"M{row_num}"] = (
            f'=IF(J{row_num}=0,"",IF(K{row_num}+L{row_num}>=0,"OK","NEED "&ABS(K{row_num}+L{row_num})))'
        )
        ws_.cell(row=row_num, column=13).font = F_GOOD
        ws_.cell(row=row_num, column=13).alignment = A_CENTER

        # N: spacer

        # O: RC W2
        c_rc2 = ws_.cell(row=row_num, column=15, value=sr["rc2"])
        c_rc2.font = F_NUM
        c_rc2.alignment = A_RIGHT
        # P: SH W2
        c_sh2 = ws_.cell(row=row_num, column=16, value=sr["sh2"])
        c_sh2.font = F_NUM
        c_sh2.alignment = A_RIGHT
        # Q: +Assign W2 = SUMIF(PR-CJAM+MONTHLY) + SUMIF(CEX-EC)
        ws_[f"Q{row_num}"] = (
            f"=SUMIF({prcjam_sku_range},A{row_num},{prcjam_w2_range})"
            f"+SUMIF({cexec_sku_range},A{row_num},{cexec_w2_range})"
        )
        ws_.cell(row=row_num, column=17).font = F_NUM
        ws_.cell(row=row_num, column=17).alignment = A_RIGHT
        # R: =Demand W2 = O + P + Q
        ws_[f"R{row_num}"] = f"=O{row_num}+P{row_num}+Q{row_num}"
        ws_.cell(row=row_num, column=18).font = F_NUM_BOLD
        ws_.cell(row=row_num, column=18).alignment = A_RIGHT
        # S: After W2 = (K + L) - R
        ws_[f"S{row_num}"] = f"=(K{row_num}+L{row_num})-R{row_num}"
        ws_.cell(row=row_num, column=19).font = F_NUM_BOLD
        ws_.cell(row=row_num, column=19).alignment = A_RIGHT
        # T: Cut W2 (input)
        ws_.cell(row=row_num, column=20).font = F_INPUT
        ws_.cell(row=row_num, column=20).fill = FILL_INPUT
        ws_.cell(row=row_num, column=20).alignment = A_RIGHT
        # U: Good? W2
        ws_[f"U{row_num}"] = (
            f'=IF(R{row_num}=0,"",IF(S{row_num}+T{row_num}>=0,"OK","NEED "&ABS(S{row_num}+T{row_num})))'
        )
        ws_.cell(row=row_num, column=21).font = F_GOOD
        ws_.cell(row=row_num, column=21).alignment = A_CENTER

    def _write_section(ws_: Worksheet, start_row: int, label: str, bg: str, fg: str, rows_: list[dict]) -> int:
        """Write a section header + sub-grouped rows. Returns next available row."""
        if not rows_:
            return start_row
        r = start_row + 1
        _section_header(ws_, r, label, bg, fg, LAST_COL)
        r += 1

        current_cat = None
        cat_start_rows: dict[str, list[int]] = {}  # track rows per category for subtotals

        for sr in rows_:
            cat = sr["cat"]
            if cat != current_cat:
                current_cat = cat
                # Sub-category label row
                r += 1
                c_cat = ws_.cell(row=r, column=1, value=cat)
                c_cat.font = Font(name="Space Mono", size=9, bold=True, color=SECTION_ACCENT)
                r += 1
                if cat not in cat_start_rows:
                    cat_start_rows[cat] = []

            _write_sku_row(ws_, r, sr)
            cat_start_rows[cat].append(r)
            r += 1

        # Subtotals per category
        for cat, data_rows in cat_start_rows.items():
            if not data_rows:
                continue
            r += 1
            ws_.cell(row=r, column=1, value=f"{cat} SUBTOTAL").font = Font(
                name="Space Mono", size=9, bold=True, color=MUTED
            )
            # SUM formulas for key columns
            row_refs = ",".join(f"C{dr}" for dr in data_rows)
            ws_[f"C{r}"] = f"=SUM({','.join(f'C{dr}' for dr in data_rows)})"
            ws_.cell(row=r, column=3).font = F_NUM_MUTED
            ws_.cell(row=r, column=3).alignment = A_RIGHT

            ws_[f"E{r}"] = f"=SUM({','.join(f'E{dr}' for dr in data_rows)})"
            ws_.cell(row=r, column=5).font = F_NUM_MUTED
            ws_.cell(row=r, column=5).alignment = A_RIGHT

            ws_[f"J{r}"] = f"=SUM({','.join(f'J{dr}' for dr in data_rows)})"
            ws_.cell(row=r, column=10).font = F_NUM_MUTED
            ws_.cell(row=r, column=10).alignment = A_RIGHT

            ws_[f"R{r}"] = f"=SUM({','.join(f'R{dr}' for dr in data_rows)})"
            ws_.cell(row=r, column=18).font = F_NUM_MUTED
            ws_.cell(row=r, column=18).alignment = A_RIGHT

        return r

    # Write sections
    row = _write_section(ws, row, "SHORTAGES", SHORTAGE_BG, SHORTAGE_FG, shortages)
    row = _write_section(ws, row, "TIGHT", TIGHT_BG, TIGHT_FG, tight)
    row = _write_section(ws, row, "HEALTHY", OK_BG, OK_FG, ok)

    last_row = row

    # ── Conditional Formatting ──
    data_start = HDR_ROW + 1

    # After W1 (K): red < 0, amber if tight, green if ok
    ws.conditional_formatting.add(
        f"K{data_start}:K{last_row}",
        CellIsRule(
            operator="lessThan",
            formula=["0"],
            fill=PatternFill("solid", fgColor=SHORTAGE_BG),
            font=Font(color=SHORTAGE_FG),
        ),
    )
    ws.conditional_formatting.add(
        f"K{data_start}:K{last_row}",
        CellIsRule(
            operator="greaterThanOrEqual",
            formula=["0"],
            fill=PatternFill("solid", fgColor=OK_BG),
            font=Font(color=OK_FG),
        ),
    )

    # After W2 (S): same pattern
    ws.conditional_formatting.add(
        f"S{data_start}:S{last_row}",
        CellIsRule(
            operator="lessThan",
            formula=["0"],
            fill=PatternFill("solid", fgColor=SHORTAGE_BG),
            font=Font(color=SHORTAGE_FG),
        ),
    )
    ws.conditional_formatting.add(
        f"S{data_start}:S{last_row}",
        CellIsRule(
            operator="greaterThanOrEqual",
            formula=["0"],
            fill=PatternFill("solid", fgColor=OK_BG),
            font=Font(color=OK_FG),
        ),
    )

    # Good? columns
    ws.conditional_formatting.add(
        f"M{data_start}:M{last_row}",
        FormulaRule(formula=[f'M{data_start}="OK"'], fill=PatternFill("solid", fgColor=OK_BG), font=Font(color=OK_FG)),
    )
    ws.conditional_formatting.add(
        f"M{data_start}:M{last_row}",
        FormulaRule(
            formula=[f'LEFT(M{data_start},4)="NEED"'],
            fill=PatternFill("solid", fgColor=SHORTAGE_BG),
            font=Font(color=SHORTAGE_FG),
        ),
    )
    ws.conditional_formatting.add(
        f"U{data_start}:U{last_row}",
        FormulaRule(formula=[f'U{data_start}="OK"'], fill=PatternFill("solid", fgColor=OK_BG), font=Font(color=OK_FG)),
    )
    ws.conditional_formatting.add(
        f"U{data_start}:U{last_row}",
        FormulaRule(
            formula=[f'LEFT(U{data_start},4)="NEED"'],
            fill=PatternFill("solid", fgColor=SHORTAGE_BG),
            font=Font(color=SHORTAGE_FG),
        ),
    )

    # Cut input columns highlight
    ws.conditional_formatting.add(
        f"L{data_start}:L{last_row}",
        CellIsRule(
            operator="greaterThan",
            formula=["0"],
            fill=PatternFill("solid", fgColor=INPUT_BG),
            font=Font(color=INPUT_FG, bold=True),
        ),
    )
    ws.conditional_formatting.add(
        f"T{data_start}:T{last_row}",
        CellIsRule(
            operator="greaterThan",
            formula=["0"],
            fill=PatternFill("solid", fgColor=INPUT_BG),
            font=Font(color=INPUT_FG, bold=True),
        ),
    )

    # Freeze panes: row 4 header + columns A:B
    ws.freeze_panes = "C5"


# ── Main ─────────────────────────────────────────────────────────────


def main() -> str:
    settings = load_settings()
    data = _fetch_all_data(settings)

    wb = openpyxl.Workbook()

    # Tab 2: Raw Materials
    _build_raw_materials_tab(wb, data, settings)

    # Tab 1: Cut Order (main) — build the sheet structure first
    ws_cut = wb.active
    ws_cut.title = "Cut Order"

    # Write assignments onto the Cut Order sheet (columns W-AE)
    prcjam_monthly_last_row, cexec_last_row = _write_assignments_on_cut_order(ws_cut, data, settings)

    # Build the main cut order content (columns A-U) using same-sheet SUMIF refs
    _build_cut_order_tab(wb, data, settings, prcjam_monthly_last_row, cexec_last_row)

    # Reorder tabs: Cut Order first
    wb.move_sheet("Cut Order", offset=-1)

    # Save
    ship_date = WK1_END.isoformat()
    out_path = os.path.join(BASE, f"cut_order_v2_{ship_date}.xlsx")
    wb.save(out_path)

    print(f"\nExcel written to: {out_path}")
    print(f"  {len(data['active_skus'])} active SKUs")
    print(f"  Tab 1: Cut Order (urgency-grouped, SUMIF-linked, assignments at cols W-AE)")
    print(f"  Tab 2: Raw Materials (wheels + bulk accompaniments)")
    print(f"  Blue cells = editable input")

    return out_path


# ── Google Drive Upload ──────────────────────────────────────────────

DRIVE_FOLDER_ID = "1TgvxK10tFAPJqhkYw-6u1Umnvp9wMJ3I"
DRIVE_TOKEN_PATH = os.path.join(BASE, "dist", "drive_oauth_token.json")


def upload_to_drive(file_path: str) -> str:
    """Upload XLSX to shared Google Drive folder using OAuth credentials."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    with open(DRIVE_TOKEN_PATH, encoding="utf-8") as f:
        token_data = json.load(f)

    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )
    if creds.expired:
        creds.refresh(Request())
        token_data["token"] = creds.token
        with open(DRIVE_TOKEN_PATH, "w") as f:
            json.dump(token_data, f, indent=2)

    drive = build("drive", "v3", credentials=creds)
    file_name = os.path.basename(file_path)
    with open(file_path, "rb") as fh:
        media = MediaIoBaseUpload(
            io.BytesIO(fh.read()),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            resumable=True,
        )
        f = (
            drive.files()
            .create(
                body={"name": file_name, "parents": [DRIVE_FOLDER_ID]},
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
    link = f.get("webViewLink", f"https://drive.google.com/file/d/{f['id']}")
    print(f"  Uploaded: {link}")
    return link


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate cut order XLSX v2")
    parser.add_argument("--local", action="store_true", help="Generate locally, don't upload")
    args = parser.parse_args()

    out_path = main()
    if not args.local:
        try:
            upload_to_drive(out_path)
        except Exception as e:
            print(f"  Upload failed: {e}")
            print(f"  File saved locally: {out_path}")
