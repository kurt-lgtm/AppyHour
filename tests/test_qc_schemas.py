"""Tests for the opt-in pandera QC schemas (AppyHour/qc_schemas.py).

Sample/synthetic dataframes only — no API, no shipping.db.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qc_schemas import ROUTING_TAB1_SCHEMA, ROUTING_TAB5_SCHEMA, validate  # noqa: E402


def _good_routing_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Order Number": "#1001", "State": "CA", "Zip Code": "90210", "OnTrac": "YES", "Veho": "NO"},
            {"Order Number": "#1002", "State": "TX", "Zip Code": "75001-1234", "OnTrac": "NO", "Veho": "YES"},
            {"Order Number": "RC-abc123", "State": "NY", "Zip Code": "10001", "OnTrac": "-", "Veho": "-"},
        ]
    )


def test_good_routing_sample_passes():
    ok, failures = validate(_good_routing_df(), ROUTING_TAB1_SCHEMA)
    assert ok is True
    assert failures.empty


def test_bad_routing_sample_surfaces_all_failures():
    bad = pd.DataFrame(
        [
            {"Order Number": "#1001", "State": "CA", "Zip Code": "90210", "OnTrac": "YES", "Veho": "NO"},  # ok
            {"Order Number": "1002", "State": "California", "Zip Code": "9021", "OnTrac": "MAYBE", "Veho": "NO"},  # 4 bad
        ]
    )
    ok, failures = validate(bad, ROUTING_TAB1_SCHEMA)
    assert ok is False
    assert not failures.empty

    # LAZY: all four bad cells on the second row are collected, not just the first-fail.
    bad_cols = set(failures["column"].tolist())
    assert {"Order Number", "State", "Zip Code", "OnTrac"} <= bad_cols

    # The good row's cells must NOT appear as failures.
    assert "90210" not in failures["failure_case"].astype(str).tolist()


def test_validate_never_raises_on_bad_data():
    junk = pd.DataFrame([{"Order Number": None, "State": None, "Zip Code": None, "OnTrac": None, "Veho": None}])
    # Must return, not raise.
    ok, failures = validate(junk, ROUTING_TAB1_SCHEMA)
    assert ok is False
    assert isinstance(failures, pd.DataFrame)


# ── tab5 (Final Routing Tag) — mirrors build.py ~L375 / live cache row shapes ──

_TAB5_COLS = ["Order Number", "State", "Zip Code", "Final Routing Tag", "TNT (effective)",
              "Lane (or rate-battle lanes)", "Ice Config", "Extra Gel"]


def _tab5(rows: list[list]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_TAB5_COLS)


def _good_tab5_df() -> pd.DataFrame:
    return _tab5([
        # value shapes copied from a real routing_tab5_rows.json (2026-07-02)
        ["#157864", "AL", "35007", "!UPS Ground - Dallas_AHB!", 2,
         "UPS Ground @ Dallas", "2x 48oz + 1x 24oz", "!ExtraGel24oz! + !ExtraGel48oz!"],
        ["#156990", "MI", "48230", "!Veho Ground Plus - Indianapolis_AHB!", 1,
         "Veho Ground @ Indianapolis", "2x 48oz", "!ExtraGel48oz!"],
        # leading-zero NJ zip on a last-mile carrier (legal: OnTrac @ Nashville)
        ["#157001", "NJ", "07001", "!OnTrac Ground - Nashville_AHB!", 2,
         "OnTrac Ground @ Nashville", "2x 48oz + 1x 24oz", "!ExtraGel24oz! + !ExtraGel48oz!"],
        # open rate-battle: comma-joined !NO fences
        ["#157002", "TN", "37201", "!NO FedEx - Nashville_AHB!, !NO FedEx - Dallas_AHB!", 2,
         "rate battle", "2x 48oz", "!ExtraGel48oz!"],
        # no-tag row (tab5 lists ALL orders; unrouted rows are all-blank after zip)
        ["#157003", "GA", "30301", "", "", "", "", ""],
    ])


def test_good_tab5_sample_passes():
    ok, failures = validate(_good_tab5_df(), ROUTING_TAB5_SCHEMA)
    assert ok is True, failures.to_string()
    assert failures.empty


def test_bad_tab5_seeded_rows_surface_in_failures():
    bad = _tab5([
        ["#158000", "CA", "90210", "!OnTrac Ground - Anaheim_AHB!", 1,
         "OnTrac Ground @ Anaheim", "2x 48oz", "!ExtraGel48oz!"],                    # clean
        ["#158001", "TX", "75001", "!Veho Ground Plus - Dallas_AHB!", 2,
         "", "", ""],                                                                # illegal pair (Veho@Dallas)
        ["#158002", "NJ", "8901", "!UPS Ground - Dallas_AHB!", 2,
         "", "", ""],                                                                # 4-char zip (lost leading zero)
        ["#158003", "FL", "33101", "!UPS Ground Dallas!", 2, "", "", ""],            # malformed tag (no ' - ' / _AHB!)
        ["#158004", "OH", "44101", "!FedEx Home Delivery - Nashville_AHB!", 4,
         "", "", ""],                                                                # TNT > 2 (late/warm class)
    ])
    ok, failures = validate(bad, ROUTING_TAB5_SCHEMA)
    assert ok is False
    checks = failures["check"].astype(str).str.cat(sep=" | ")
    cases = failures["failure_case"].astype(str).tolist()

    # pandera's failure_cases "check" column carries the registered error strings
    assert "illegal carrier-hub pair" in checks        # Veho@Dallas illegal carrier-hub pair
    assert "comma-joined" in checks                    # malformed tag (grammar check)
    assert "8901" in cases                             # leading-zero zip failure
    assert "4" in cases                                # TNT 4 flagged
    # the clean row must not appear anywhere in the failure set
    assert "90210" not in cases
    row_idx = failures["index"].dropna().astype(int).tolist()
    assert 0 not in row_idx


def test_tab5_lastmile_zip_check_requires_zip_for_veho_ontrac():
    # positive Veho tag with a garbage zip -> serviceability-class failure (row-level check),
    # on top of the Zip Code column failure itself.
    bad = _tab5([
        ["#158100", "MI", "unknown", "!Veho Ground Plus - Indianapolis_AHB!", 1, "", "", ""],
    ])
    ok, failures = validate(bad, ROUTING_TAB5_SCHEMA)
    assert ok is False
    assert "positive Veho/OnTrac tag" in failures["check"].astype(str).str.cat(sep=" | ")
