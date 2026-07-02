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

from qc_schemas import ROUTING_TAB1_SCHEMA, validate  # noqa: E402


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
