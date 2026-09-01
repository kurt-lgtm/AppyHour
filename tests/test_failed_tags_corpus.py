"""RMFG's rejection files as labelled regression ground truth.

🔴 This is the strongest validation available anywhere in the routing stack: RMFG classifies our
failures for us and returns them in `AHB_Failed Tags_*.xlsx`. If the coverage authority ever stops
reproducing their verdict, the pre-send check has a hole and a whole sheet is at risk.

A MISS here is not a flaky test — it means RMFG rejected a row our own authority calls legal.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AppyHour" / "scripts"))

from failed_tags_corpus import classify, find_files, parse  # noqa: E402

pytestmark = pytest.mark.skipif(not find_files(), reason="no AHB_Failed Tags_*.xlsx available")


@pytest.fixture(scope="module")
def rows():
    out = []
    for p in find_files():
        out.extend(parse(p))
    return out


def test_corpus_is_non_empty(rows):
    """Zero is a claim — an empty corpus means the parser broke, not that RMFG is happy."""
    assert rows, "rejection files found but zero rows parsed — parser regression"


def test_every_row_has_a_tag_and_order(rows):
    for r in rows:
        assert r["order"], f"row without OrderID: {r}"
        assert r["tag"], f"row without DeliveryTag: {r}"


def test_authority_reproduces_rmfg_verdict(rows):
    """🔴 THE test. Validated 53/53 on wk0803 (AHB_Failed Tags_8-3-26.xlsx) 2026-08-09.

    Every row RMFG rejected must be independently flagged by the coverage authority
    (lib.zip_loaders.load_ontrac per-hub cell + lib.features.CARRIER_HUBS). A MISS is a real gap
    in the pre-send check.
    """
    reproduced, missed = classify(rows)
    assert not missed, (
        f"{len(missed)} row(s) rejected by RMFG that our authority calls LEGAL — "
        f"pre-send check has a hole: {[(m['order'], m['zip'], m['tag']) for m in missed[:10]]}")
    assert len(reproduced) == len(rows)


def test_wk0803_known_baseline(rows):
    """Pin the known-good numbers so a silent parser/authority change is visible."""
    wk0803 = [r for r in rows if r["cohort"] == "8-3-26"]
    if not wk0803:
        pytest.skip("wk0803 rejection file not present")
    assert len(wk0803) == 53
    # every wk0803 rejection was an OnTrac lane to an uncovered zip
    assert all("OnTrac" in r["tag"] for r in wk0803)


def test_missing_file_is_not_evidence_of_acceptance():
    """🔴 Doctrine, pinned as a test: --require must FAIL for a cohort with no rejection file,
    rather than silently reporting success. Absence of a row is not a zero."""
    from failed_tags_corpus import main
    assert main(["--require", "99-99-99"]) == 1
