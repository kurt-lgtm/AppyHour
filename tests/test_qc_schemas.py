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
        ["#156990", "MI", "48230", "!OnTrac Ground - Chicago_AHB!", 1,
         "OnTrac Ground @ Chicago", "2x 48oz", "!ExtraGel48oz!"],
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


def test_tab5_legality_tracks_current_shipping_roster():
    rows = _tab5([
        ["#157010", "IL", "60601", "!OnTrac Ground - Chicago_AHB!", 2, "", "", ""],
        ["#157011", "NJ", "08085", "!OnTrac Ground - Swedesboro_AHB!", 2, "", "", ""],
        ["#157014", "NJ", "08007", "!ANY FedEx - Swedesboro_AHB!", 2, "", "", ""],
        ["#157012", "MI", "48230", "!Veho Ground Plus - Indianapolis_AHB!", 1, "", "", ""],
        ["#157013", "IN", "46201", "!FedEx Home Delivery - Indianapolis_AHB!", 1, "", "", ""],
    ])

    ok, failures = validate(rows, ROUTING_TAB5_SCHEMA)

    assert ok is False
    assert set(failures["index"].dropna().astype(int)) == {3, 4}


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


# ── D6: the mirrors qc_schemas.py DECLARES, held verbatim-equal (2026-09-11 dedupe audit item 3) ──
# qc_schemas restates three ShipRouting facts instead of importing them (it must stay
# import-side-effect-free): the carrier×hub legality dict (`:63-65` "mirrors ROUTING_RULES §0 … update
# in the SAME commit"), `_carrier_hub` (`:80` "Mirrors ShipRouting qc_audit.carrier_hub") and the tab1/tab5
# headers (`:36`, `:53` "copied verbatim from build.py"). Same shape as
# test_pp_origin.py::test_authority_zips_match_shiprouting_hub_roster: read the authority, assert equal.
# ShipRouting is READ as source and parsed, never imported — build.py and qc_audit.py hit Shopify /
# shipping.db on import, and lib.features reads Kori settings.
import ast  # noqa: E402
import os  # noqa: E402

import qc_schemas  # noqa: E402

_SR_ROOT = Path(os.environ.get("SHIPROUTING_ROOT") or r"C:/Users/Work/Claude Projects/ShipRouting")
_sr_present = pytest.mark.skipif(not (_SR_ROOT / "lib" / "features.py").exists(),
                                 reason=f"ShipRouting checkout not present at {_SR_ROOT}")


def _module_level_literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found as a module-level literal in {path}")


def _function_level_literal(path: Path, func: str, name: str):
    """`name = <literal>` inside `def func` — build.py builds its headers inside main()."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == func:
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found inside {func}() in {path}")


@_sr_present
def test_carrier_hubs_mirror_matches_features_baseline():
    """🔴 `_CARRIER_HUBS` mirrors ROUTING_RULES §0; `lib/features.CARRIER_HUBS_BASELINE` is §0 in code.
    Drift here means the LOG-ONLY schema gate judges lanes by a roster the engine no longer runs."""
    baseline = _module_level_literal(_SR_ROOT / "lib" / "features.py", "CARRIER_HUBS_BASELINE")
    assert {c: set(h) for c, h in qc_schemas._CARRIER_HUBS.items()} == \
           {c: set(h) for c, h in baseline.items()}


@_sr_present
def test_carrier_hub_parser_matches_qc_audit():
    """🔴 `_carrier_hub` mirrors `scripts/qc_audit.carrier_hub`. The reference function is lifted out of
    qc_audit.py by AST and executed with the real `lib.canon.CARRIERS` (stdlib-only), then both are
    driven over every tag shape the sheet carries; any token they parse differently is a drift."""
    src = (_SR_ROOT / "scripts" / "qc_audit.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "carrier_hub")
    canon_src = (_SR_ROOT / "lib" / "canon.py").read_text(encoding="utf-8")
    canon_ns: dict = {}
    exec(compile(canon_src, "lib/canon.py", "exec"), canon_ns)                    # noqa: S102 — stdlib-only
    ref_ns: dict = {"_CANON_CARRIERS": canon_ns["CARRIERS"]}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "qc_audit.py", "exec"), ref_ns)  # noqa: S102
    reference = ref_ns["carrier_hub"]

    tokens = []
    for carrier, hubs in qc_schemas._CARRIER_HUBS.items():
        for hub in sorted(hubs) + ["Indianapolis", "Salt Lake City"]:
            for service in (f"{carrier} Ground", f"{carrier} Home Delivery", f"{carrier} Ground Plus",
                            f"{carrier} 2Day", f"ANY {carrier}"):
                tokens += [f"!{service} - {hub}_AHB!", f"!NO {service} - {hub}_AHB!"]
    tokens += ["!ANY - Dallas_AHB!", "!UPS Ground Dallas!", "", "!ExtraGel48oz!", "!NO OnTrac - Dallas_AHB!"]
    diffs = [(t, qc_schemas._carrier_hub(t), reference(t)) for t in tokens
             if qc_schemas._carrier_hub(t) != reference(t)]
    assert not diffs, f"_carrier_hub disagrees with qc_audit.carrier_hub on: {diffs}"


@_sr_present
def test_tab_headers_match_build_py_verbatim():
    """🔴 tab1/tab5 column names are 'copied verbatim from build.py' — a renamed column there turns every
    row into a schema failure (or, worse, `strict=False` lets a missing column pass silently)."""
    build = _SR_ROOT / "build.py"
    tab1 = _function_level_literal(build, "main", "tab1")[0]
    tab5 = _function_level_literal(build, "main", "tab5")[0]
    assert list(ROUTING_TAB1_SCHEMA.columns) == tab1
    assert list(ROUTING_TAB5_SCHEMA.columns) == tab5
