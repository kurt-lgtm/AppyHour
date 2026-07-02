"""Opt-in pandera QC schemas for AppyHour ops artifacts.

Additive + isolated. ONE live caller: ShipRouting/scripts/qc_audit.py `schema_gate()`
uses ROUTING_TAB5_SCHEMA as a LOG-ONLY gate (never changes qc_audit's exit code).
See QC_SCHEMAS_RULES.md (constraints SSOT) before changing — especially: LAZY validation
only (collect ALL failing rows, never first-fail raise), and column names are copied
from real builders.

Usage:
    from qc_schemas import ROUTING_TAB1_SCHEMA, validate
    ok, failures = validate(df, ROUTING_TAB1_SCHEMA)
    if not ok:
        print(failures)  # every failing row/cell, not just the first
"""

from __future__ import annotations

import re

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


# ── Routing final sheet, tab5 (Final resolved tag — the artifact apply.py acts on) ──
# Header copied verbatim from ShipRouting/build.py ~L375 (VERIFIED 2026-07-02 against
# _outputs/cache/routing_tab5_rows.json, 1331 live rows):
#   tab5 = [["Order Number", "State", "Zip Code", "Final Routing Tag", "TNT (effective)",
#            "Lane (or rate-battle lanes)", "Ice Config", "Extra Gel"]]
# Value shapes observed: TNT is int (1/2) or "" on no-tag rows; Final Routing Tag is "",
# ONE positive `!<Carrier Service> - <Hub>_AHB!` tag, or a comma-joined `!NO ...` fence list.

# One routing-tag token: `!<Carrier Service> - <Hub>_AHB!`, optionally `!NO `-prefixed (fence).
_TAG_TOKEN_RE = re.compile(r"^!(?:NO )?[^!,]+ - [^!,]+_AHB!$")

# Carrier × hub LEGALITY — mirrors ShipRouting/ROUTING_RULES.md §0 (the physically possible
# lanes). Duplicated here ON PURPOSE: this module must stay import-side-effect-free (no
# ShipRouting import). If §0 changes, update this dict in the SAME commit.
_CARRIER_HUBS = {
    "Veho": {"Nashville", "Indianapolis"},
    "UPS": {"Dallas"},
    "OnTrac": {"Anaheim", "Nashville", "Dallas"},
    "FedEx": {"Dallas", "Nashville", "Anaheim", "Indianapolis"},
}
_LAST_MILE = ("Veho", "OnTrac")  # zip-footprint carriers (serviceability bug class)


def _tag_tokens(cell) -> list[str]:
    return [t.strip() for t in str("" if cell is None else cell).split(",") if t.strip()]


def _carrier_hub(token: str) -> tuple[str | None, str | None]:
    """Parse one tag token -> (carrier, hub). Mirrors ShipRouting qc_audit.carrier_hub."""
    body = token.strip("!")
    if body.startswith("NO "):
        body = body[3:]
    body = body.replace("_AHB", "")
    if " - " not in body:
        return None, None
    left, hub = body.split(" - ", 1)
    car = ("FedEx" if "FedEx" in left else "Veho" if "Veho" in left else
           "UPS" if "UPS" in left else "OnTrac" if "OnTrac" in left else None)
    return car, hub.strip()


def _tag_grammar_ok(cell) -> bool:
    s = str("" if cell is None else cell)
    if s == "":
        return True
    return all(_TAG_TOKEN_RE.match(t) for t in _tag_tokens(s))


def _tnt_ok(v) -> bool:
    # Blank = no tag assigned (allowed). Numeric must be 1..2 — a 3-day lane on the final
    # sheet is the late/warm bug class (summer hard max; winter cold-state 3-day ground
    # will log here too — review before promoting this to a hard gate).
    if v is None or v == "":
        return True
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 1 <= v <= 2


def _row_lanes_legal(df: pd.DataFrame) -> pd.Series:
    """Every tag token on the row names a physically-possible carrier×hub lane."""
    def ok(cell) -> bool:
        for t in _tag_tokens(cell):
            car, hub = _carrier_hub(t)
            if car and hub and hub not in _CARRIER_HUBS[car]:
                return False
        return True
    return df["Final Routing Tag"].map(ok)


def _row_lastmile_has_zip(df: pd.DataFrame) -> pd.Series:
    """A positive Veho/OnTrac tag requires a real 5-digit dest zip (serviceability class)."""
    zip_ok = df["Zip Code"].map(lambda z: bool(re.match(r"^\d{5}", str(z or ""))))
    def lastmile(cell) -> bool:
        return any(_carrier_hub(t)[0] in _LAST_MILE
                   for t in _tag_tokens(cell) if not t.startswith("!NO"))
    return ~df["Final Routing Tag"].map(lastmile) | zip_ok


ROUTING_TAB5_SCHEMA = DataFrameSchema(
    {
        "Order Number": Column(str, _ORDER_NAME, nullable=False),
        "State": Column(str, _US_STATE, nullable=False),
        "Zip Code": Column(str, _ZIP, nullable=False),
        "Final Routing Tag": Column(
            str,
            Check(_tag_grammar_ok, element_wise=True, name="tag_grammar",
                  error="tag must be '' or comma-joined '!(NO )<Carrier Service> - <Hub>_AHB!' tokens"),
            nullable=False),
        "TNT (effective)": Column(
            None,  # mixed int/"" (object dtype) — dtype unchecked, value check below
            Check(_tnt_ok, element_wise=True, name="tnt_le_2",
                  error="TNT (effective) must be '' or 1-2 (>2 = late/warm class)"),
            nullable=True),
        "Lane (or rate-battle lanes)": Column(str, nullable=True, required=False),
        "Ice Config": Column(str, nullable=True, required=False),
        "Extra Gel": Column(str, nullable=True, required=False),
    },
    checks=[
        Check(_row_lanes_legal, name="legal_lane",
              error="illegal carrier-hub pair (Veho=Nash/Indy, UPS=Dallas, OnTrac=Anaheim/Nash/Dallas)"),
        Check(_row_lastmile_has_zip, name="lastmile_zip",
              error="positive Veho/OnTrac tag without a 5-digit dest zip (serviceability class)"),
    ],
    strict=False,  # tolerate extra columns future builds may append
    coerce=False,  # never coerce — a lost leading-zero zip should FAIL, not be masked
    name="routing_tab5_final",
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
