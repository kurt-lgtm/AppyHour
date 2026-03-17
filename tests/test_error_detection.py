"""Tests for error detection logic in find_new_errors.py.

Tests cover the pure helper functions and the analyze_order() orchestrator
for error Classes 4-12.
"""

# find_new_errors.py loads settings at import time — we need to mock that.
# Instead of importing the module directly, we'll extract and test the pure
# logic by patching the settings load.
import json
import sys
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

# We need the Errors dir on sys.path
ERRORS_DIR = str(Path(__file__).parent.parent / "InventoryReorder" / "Errors")
if ERRORS_DIR not in sys.path:
    sys.path.insert(0, ERRORS_DIR)

# Mock settings before importing the module
_MOCK_SETTINGS = {
    "shopify_store_url": "test-store",
    "shopify_access_token": "test-token",
    "curation_recipes": {
        "MONG": [("CH-BLR", 1), ("CH-WWDI", 1), ("MT-LONZ", 1), ("AC-DTCH", 1), ("CH-MCPC", 1)],
        "MDT": [("CH-MCPC", 1), ("CH-MSMG", 1), ("MT-TUSC", 1), ("AC-PRPE", 1), ("CH-TTBRIE", 1)],
        "OWC": [("CH-WMANG", 1), ("CH-UCONE", 1), ("MT-LONZ", 1), ("AC-TCRISP", 1), ("CH-MCPC", 1)],
    },
    "pr_cjam": {},
    "cex_ec": {},
}


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Patch the settings file read so find_new_errors can import."""
    pass  # Settings are patched at module level below


# Patch the open() call and import the module
with patch("builtins.open", mock_open(read_data=json.dumps(_MOCK_SETTINGS))):
    import find_new_errors as fne


# ── Helper function tests ────────────────────────────────────────────────

class TestExtractSkus:
    def test_basic_extraction(self):
        line_items = [
            {"sku": "CH-MCPC", "quantity": 1, "title": "Manchego", "price": "12.00"},
            {"sku": "MT-LONZ", "quantity": 1, "title": "Lonza", "price": "8.00"},
        ]
        result = fne.extract_skus(line_items)
        assert result == [("CH-MCPC", 1, "Manchego", 12.0), ("MT-LONZ", 1, "Lonza", 8.0)]

    def test_missing_sku_becomes_empty_string(self):
        result = fne.extract_skus([{"quantity": 1, "title": "Unknown", "price": "0"}])
        assert result[0][0] == ""

    def test_none_sku_becomes_empty_string(self):
        result = fne.extract_skus([{"sku": None, "quantity": 1, "title": "X", "price": "0"}])
        assert result[0][0] == ""

    def test_strips_whitespace(self):
        result = fne.extract_skus([{"sku": " CH-MCPC ", "quantity": 1, "title": "X", "price": "5"}])
        assert result[0][0] == "CH-MCPC"

    def test_empty_line_items(self):
        assert fne.extract_skus([]) == []

    def test_missing_price_defaults_to_zero(self):
        result = fne.extract_skus([{"sku": "CH-MCPC", "quantity": 1, "title": "X"}])
        assert result[0][3] == 0.0


class TestGetCurationFromBox:
    @pytest.mark.parametrize("sku,expected", [
        ("AHB-MCUST-MDT", "MDT"),
        ("AHB-MCUST-MONG", "MONG"),
        ("AHB-LCUST-OWC", "OWC"),
        ("AHB-MCUST-HHIGH", "HHIGH"),
        ("AHB-LCUST-ISUN", "ISUN"),
    ])
    def test_extracts_curation_from_custom_box(self, sku, expected):
        assert fne.get_curation_from_box(sku) == expected

    def test_returns_none_for_monthly_box(self):
        assert fne.get_curation_from_box("AHB-MED") is None
        assert fne.get_curation_from_box("AHB-LGE") is None

    def test_returns_none_for_non_box(self):
        assert fne.get_curation_from_box("CH-MCPC") is None


class TestGetCurationFromPr:
    @pytest.mark.parametrize("sku,expected", [
        ("PR-CJAM-MDT", "MDT"),
        ("PR-CJAM-MONG", "MONG"),
        ("PR-CJAM-GEN", "GEN"),
    ])
    def test_extracts_curation(self, sku, expected):
        assert fne.get_curation_from_pr(sku) == expected

    def test_returns_none_for_non_pr(self):
        assert fne.get_curation_from_pr("CH-MCPC") is None


class TestIsReship:
    def test_detects_reship_tag(self):
        assert fne.is_reship("reship, priority") is True

    def test_case_insensitive(self):
        assert fne.is_reship("Reship") is True

    def test_returns_false_without_tag(self):
        assert fne.is_reship("Subscription First Order") is False

    def test_handles_none(self):
        assert fne.is_reship(None) is False

    def test_handles_empty(self):
        assert fne.is_reship("") is False


class TestIsFirstOrder:
    def test_detects_first_order(self):
        assert fne.is_first_order("Subscription First Order") is True

    def test_case_sensitive(self):
        assert fne.is_first_order("subscription first order") is False

    def test_returns_false_without_tag(self):
        assert fne.is_first_order("reship") is False

    def test_handles_none(self):
        assert fne.is_first_order(None) is False


class TestIsSpecialtyBox:
    def test_detects_specialty(self):
        skus = [("AHB-XMAS-MED", 1, "Holiday Box", 79.0)]
        assert fne.is_specialty_box(skus) is True

    def test_non_specialty(self):
        skus = [("AHB-MCUST-MONG", 1, "Custom Box", 79.0)]
        assert fne.is_specialty_box(skus) is False


# ── analyze_order() integration tests ────────────────────────────────────

def _make_order(line_items, tags="", name="#1001"):
    """Helper to build a minimal Shopify order dict."""
    return {
        "id": 1001,
        "name": name,
        "tags": tags,
        "line_items": [
            {"sku": sku, "quantity": qty, "title": title, "price": str(price)}
            for sku, qty, title, price in line_items
        ],
    }


class TestAnalyzeOrderReshipsSkipped:
    def test_reship_orders_return_no_errors(self):
        order = _make_order(
            [("CH-MCPC", 1, "Manchego", 12.0)],
            tags="reship",
        )
        assert fne.analyze_order(order) == []


class TestAnalyzeOrderSpecialtySkipped:
    def test_specialty_box_returns_no_errors(self):
        order = _make_order([
            ("AHB-XMAS-MED", 1, "Holiday Box", 79.0),
            ("CH-MCPC", 1, "Manchego", 12.0),
        ])
        assert fne.analyze_order(order) == []


class TestClass4DoubleCuration:
    def test_detects_duplicated_food_items(self):
        # Same 3 food items appearing twice = 6 items total
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Box", 79.0),
            ("CH-MCPC", 1, "Manchego", 0), ("MT-LONZ", 1, "Lonza", 0), ("AC-DTCH", 1, "Dutch", 0),
            ("CH-MCPC", 1, "Manchego", 0), ("MT-LONZ", 1, "Lonza", 0), ("AC-DTCH", 1, "Dutch", 0),
        ])
        errors = fne.analyze_order(order)
        class4 = [e for e in errors if "Class 4" in e[0]]
        assert len(class4) >= 1

    def test_detects_duplicate_box_sku(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Box", 79.0),
            ("AHB-MCUST-MONG", 1, "Box", 79.0),
            ("CH-MCPC", 1, "Manchego", 0),
        ])
        errors = fne.analyze_order(order)
        class4 = [e for e in errors if "Class 4" in e[0]]
        assert len(class4) >= 1

    def test_detects_qty_greater_than_1(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Box", 79.0),
            ("CH-MCPC", 3, "Manchego", 0),
        ])
        errors = fne.analyze_order(order)
        class4 = [e for e in errors if "Class 4" in e[0] and "qty" in e[0].lower()]
        assert len(class4) == 1


class TestClass5MonthlyBoxWithFoodItems:
    def test_detects_food_on_monthly_box(self):
        order = _make_order([
            ("AHB-MED", 1, "Monthly Med", 69.0),
            ("CH-MCPC", 1, "Manchego", 0),
        ])
        errors = fne.analyze_order(order)
        class5 = [e for e in errors if "Class 5" in e[0]]
        assert len(class5) == 1

    def test_no_error_for_monthly_without_food(self):
        order = _make_order([
            ("AHB-MED", 1, "Monthly Med", 69.0),
            ("PR-CJAM-GEN", 1, "Bonus Pairing", 0),
        ])
        errors = fne.analyze_order(order)
        class5 = [e for e in errors if "Class 5" in e[0]]
        assert len(class5) == 0


class TestClass7MissingPrCjam:
    def test_detects_missing_pr_cjam(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Box for Life", 79.0),
            ("CH-BLR", 1, "Blue", 0),
            ("CH-WWDI", 1, "Wensleydale", 0),
        ])
        errors = fne.analyze_order(order)
        class7 = [e for e in errors if "Class 7" in e[0]]
        assert len(class7) == 1

    def test_no_error_when_pr_cjam_present(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Box for Life", 79.0),
            ("CH-BLR", 1, "Blue", 0),
            ("PR-CJAM-MONG", 1, "Bonus Pairing", 0),
        ])
        errors = fne.analyze_order(order)
        class7 = [e for e in errors if "Class 7" in e[0]]
        assert len(class7) == 0


class TestClass8StaleCexEc:
    def test_detects_multiple_cex_ec_variants(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Box", 79.0),
            ("CEX-EC-MONG", 1, "Extra Cheese MONG", 0),
            ("CEX-EC-MDT", 1, "Extra Cheese MDT", 0),
            ("CH-BLR", 1, "Blue", 0),
        ])
        errors = fne.analyze_order(order)
        class8 = [e for e in errors if "Class 8" in e[0]]
        assert len(class8) >= 1

    def test_detects_bare_cex_alongside_resolved(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Box", 79.0),
            ("CEX-EC", 1, "Extra Cheese", 0),
            ("CEX-EC-MONG", 1, "Extra Cheese MONG", 0),
            ("CH-BLR", 1, "Blue", 0),
        ])
        errors = fne.analyze_order(order)
        class8 = [e for e in errors if "Class 8" in e[0] and "Bare" in e[0]]
        assert len(class8) == 1


class TestClass9MultipleBoxSkus:
    def test_detects_multiple_box_types(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Med", 79.0),
            ("AHB-LGE", 1, "Monthly Large", 99.0),
            ("CH-MCPC", 1, "Manchego", 0),
        ])
        errors = fne.analyze_order(order)
        class9 = [e for e in errors if "Class 9" in e[0]]
        assert len(class9) == 1

    def test_no_error_for_single_box(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Med", 79.0),
            ("CH-MCPC", 1, "Manchego", 0),
        ])
        errors = fne.analyze_order(order)
        class9 = [e for e in errors if "Class 9" in e[0]]
        assert len(class9) == 0


class TestClass10GhostItems:
    def test_detects_zero_qty_item(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Box", 79.0),
            ("CH-MCPC", 0, "Manchego", 12.0),
        ])
        errors = fne.analyze_order(order)
        class10 = [e for e in errors if "Class 10" in e[0]]
        assert len(class10) == 1


class TestClass11Underfilled:
    def test_detects_underfilled_medium_box(self):
        # MCUST expects 5+ food items
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Med", 79.0),
            ("CH-MCPC", 1, "Manchego", 0),
            ("CH-BLR", 1, "Blue", 0),
        ])
        errors = fne.analyze_order(order)
        class11 = [e for e in errors if "Class 11" in e[0]]
        assert len(class11) == 1
        assert "2 food items" in class11[0][1]

    def test_detects_underfilled_large_box(self):
        # LCUST expects 7+ food items
        order = _make_order([
            ("AHB-LCUST-MONG", 1, "Custom Large", 99.0),
            ("CH-MCPC", 1, "A", 0), ("CH-BLR", 1, "B", 0), ("MT-LONZ", 1, "C", 0),
        ])
        errors = fne.analyze_order(order)
        class11 = [e for e in errors if "Class 11" in e[0]]
        assert len(class11) == 1

    def test_no_error_when_properly_filled(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Med", 79.0),
            ("CH-MCPC", 1, "A", 0), ("CH-BLR", 1, "B", 0), ("MT-LONZ", 1, "C", 0),
            ("AC-DTCH", 1, "D", 0), ("CH-WWDI", 1, "E", 0),
        ])
        errors = fne.analyze_order(order)
        class11 = [e for e in errors if "Class 11" in e[0]]
        assert len(class11) == 0


class TestClass12MissingTastingGuide:
    def test_detects_missing_pk(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Med", 79.0),
            ("CH-MCPC", 1, "Manchego", 0),
        ])
        errors = fne.analyze_order(order)
        class12 = [e for e in errors if "Class 12" in e[0]]
        assert len(class12) == 1

    def test_no_error_with_pk(self):
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Med", 79.0),
            ("CH-MCPC", 1, "Manchego", 0),
            ("PK-TGUIDE", 1, "Tasting Guide", 0),
        ])
        errors = fne.analyze_order(order)
        class12 = [e for e in errors if "Class 12" in e[0]]
        assert len(class12) == 0

    def test_no_error_for_first_order(self):
        order = _make_order(
            [("AHB-MCUST-MONG", 1, "Box", 79.0), ("CH-MCPC", 1, "Manchego", 0)],
            tags="Subscription First Order",
        )
        errors = fne.analyze_order(order)
        class12 = [e for e in errors if "Class 12" in e[0]]
        assert len(class12) == 0


class TestCleanOrder:
    def test_clean_order_returns_no_errors(self):
        """A properly formed order should produce zero errors."""
        order = _make_order([
            ("AHB-MCUST-MONG", 1, "Custom Med for Life", 79.0),
            ("CH-BLR", 1, "Blue", 0),
            ("CH-WWDI", 1, "Wensleydale", 0),
            ("MT-LONZ", 1, "Lonza", 0),
            ("AC-DTCH", 1, "Dutch", 0),
            ("CH-MCPC", 1, "Manchego", 0),
            ("PR-CJAM-MONG", 1, "Bonus Pairing", 0),
            ("CEX-EC-MONG", 1, "Extra Cheese", 0),
            ("PK-TGUIDE", 1, "Tasting Guide", 0),
        ])
        errors = fne.analyze_order(order)
        assert errors == [], f"Expected no errors but got: {errors}"
