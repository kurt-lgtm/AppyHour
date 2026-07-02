"""Opt-in pandera QC schemas for AppyHour ops artifacts.

Additive + isolated. Nothing in live ops imports this yet. See QC_SCHEMAS_RULES.md
(constraints SSOT) before changing — especially: LAZY validation only (collect ALL
failing rows, never first-fail raise), and column names are copied from real builders.

Usage:
    from qc_schemas import ROUTING_TAB1_SCHEMA, validate
    ok, failures = validate(df, ROUTING_TAB1_SCHEMA)
    if not ok:
        print(failures)  # every failing row/cell, not just the first
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

# US state code check (2-letter upper). Kept generic — do not encode a hub->state
# routing rule here; this is dataframe-shape QC, not routing legality (qc_audit.py owns that).
_US_STATE = Check.str_matches(r"^[A-Z]{2}$")
# Shopify order names are like "#12345" or an RC-/pseudo-order id used by ShipRouting.
_ORDER_NAME = Check.str_matches(r"^(#\d+|RC-.+)$", error="order name must be '#<digits>' or 'RC-<id>'")
# US zip: 5 digits or ZIP+4. String (leading zeros matter — never coerce to int).
_ZIP = Check.str_matches(r"^\d{5}(-\d{4})?$", error="zip must be 5-digit or ZIP+4 string")
# Serviceability flag columns in ShipRouting build.py tab1 hold yes/no-ish text.
_YESNO = Check.isin(["YES", "NO", "Y", "N", "yes", "no", "", "-"])


# ── Routing assignment sheet, tab1 (serviceability flags) ────────────────────
# Header copied verbatim from ShipRouting/build.py ~L164:
#   tab1 = [["Order Number", "State", "Zip Code", "OnTrac", "Veho"]]
ROUTING_TAB1_SCHEMA = DataFrameSchema(
    {
        "Order Number": Column(str, _ORDER_NAME, nullable=False),
        "State": Column(str, _US_STATE, nullable=False),
        "Zip Code": Column(str, _ZIP, nullable=False),
        "OnTrac": Column(str, _YESNO, nullable=False),
        "Veho": Column(str, _YESNO, nullable=False),
    },
    strict=False,  # tolerate extra columns future builds may append
    coerce=False,  # never coerce — a lost leading-zero zip should FAIL, not be masked
    name="routing_tab1_serviceability",
)


# ── Cut-order line rows (EXAMPLE shape) ──────────────────────────────────────
# 📝 TODO: build_cut_order_xlsx_v2.py writes a styled worksheet cell-by-cell (openpyxl),
# NOT one flat DataFrame, so there is no single canonical cut-order column list to pin.
# This is a GENERIC example schema for a simple SKU/name/qty line table. Confirm the real
# per-line contract with the builder owner before using it as a live gate. See rules doc.
CUT_ORDER_LINE_SCHEMA = DataFrameSchema(
    {
        "SKU": Column(str, Check.str_length(min_value=1), nullable=False),
        "Name": Column(str, nullable=True),
        "Qty": Column(int, Check.ge(0), nullable=False),
    },
    strict=False,
    coerce=False,
    name="cut_order_line_example",
)


def validate(df: pd.DataFrame, schema: DataFrameSchema) -> tuple[bool, pd.DataFrame]:
    """Lazily validate ``df`` against ``schema``.

    Returns ``(ok, failures_df)``. On success ``ok`` is True and ``failures_df`` is empty.
    On failure ``ok`` is False and ``failures_df`` holds EVERY failing case (pandera
    ``failure_cases``: schema_context, column, check, check_number, failure_case, index) —
    it never raises and never stops at the first bad row. See QC_SCHEMAS_RULES.md.
    """
    try:
        schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as err:
        return False, err.failure_cases
    return True, pd.DataFrame()
