"""Ingest artifacts are per-run CLI parameters — never baked-in dated literals.

🔴 The class under test is silent-stale fallback: a dated path compiled into a canon
script quietly runs NEXT week against LAST week's artifact (the 6/23 stale-Downloads
HAVE burn; check7's wk0831 literal). These tests pin the fail-LOUD contract: no path →
nonzero exit naming the flag, missing file → nonzero exit, never an empty-dict shrug.
All offline — nothing here touches Shopify/Recharge.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from order_checks.check7 import HAVE_OVERRIDE, load_have  # noqa: E402


# --- check7 load_have -------------------------------------------------------------

def test_load_have_no_path_fails_loud_naming_the_flag():
    with pytest.raises(SystemExit) as e:
        load_have(None)
    assert "--have" in str(e.value)


def test_load_have_missing_file_fails_loud():
    with pytest.raises(SystemExit) as e:
        load_have(r"C:\nope\does_not_exist.csv")
    assert "not found" in str(e.value)


def test_load_have_parses_csv_and_applies_overrides(tmp_path, capsys):
    p = tmp_path / "have.csv"
    p.write_text("SKU,RMFG Have 8/29\nCH-TEST,12\nAC-TEST,3\n", encoding="utf-8")
    have = load_have(str(p))
    assert have["CH-TEST"] == 12 and have["AC-TEST"] == 3
    for sku, qty in HAVE_OVERRIDE.items():        # overrides land on ANY file
        assert have[sku] == qty
    out = capsys.readouterr().out                 # resolved path printed loudly
    assert str(p) in out


# --- entry points refuse to run without --have ------------------------------------

def test_run_all_requires_have_flag(capsys):
    from order_checks import run_all
    with pytest.raises(SystemExit) as e:
        run_all.main(["--tag", "X", "--ship", "Y", "--sheet", "z.xlsx"])
    assert e.value.code == 2                      # argparse usage error, nonzero
    assert "--have" in capsys.readouterr().err


def test_check7_main_requires_have_flag(capsys):
    from order_checks import check7
    with pytest.raises(SystemExit) as e:
        check7.main(["--tag", "X", "--sheet", "z.xlsx"])
    assert e.value.code == 2
    assert "--have" in capsys.readouterr().err


# --- audit_distvol_drift: no baked-in cohort paths --------------------------------

def test_audit_distvol_drift_requires_csv_and_cache(capsys):
    import audit_distvol_drift
    with pytest.raises(SystemExit) as e:
        audit_distvol_drift.main([])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "--csv" in err and "--cache" in err


# --- box_simulation --distvol: CLI validates, import stays side-effect free -------

def test_box_simulation_distvol_missing_file_fails_loud():
    import box_simulation
    with pytest.raises(SystemExit) as e:
        box_simulation.main(["_SHIP_TEST", "--distvol", r"C:\nope\missing.xlsx"])
    assert "--distvol" in str(e.value)
