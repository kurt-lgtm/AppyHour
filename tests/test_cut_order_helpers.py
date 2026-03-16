"""Tests for pure helper functions in cut_order_generator.py."""

import pytest
from cut_order_generator import (
    is_pickable,
    normalize_sku,
    resolve_curation_from_box_sku,
)

# ── normalize_sku ────────────────────────────────────────────────────────

class TestNormalizeSku:
    def test_uppercases_sku(self):
        assert normalize_sku("ch-mcpc") == "CH-MCPC"

    def test_applies_equiv_mapping(self):
        assert normalize_sku("ch-brie") == "CH-EBRIE"
        assert normalize_sku("CH-BRIE") == "CH-EBRIE"

    def test_returns_original_when_no_mapping(self):
        assert normalize_sku("CH-MCPC") == "CH-MCPC"

    def test_returns_falsy_input_unchanged(self):
        assert normalize_sku(None) is None
        assert normalize_sku("") == ""

    def test_mixed_case_equiv(self):
        assert normalize_sku("Ch-Brie") == "CH-EBRIE"


# ── is_pickable ──────────────────────────────────────────────────────────

class TestIsPickable:
    @pytest.mark.parametrize("sku", ["CH-MCPC", "CH-BLR", "MT-LONZ", "AC-DTCH"])
    def test_food_items_are_pickable(self, sku):
        assert is_pickable(sku) is True

    @pytest.mark.parametrize("sku,reason", [
        ("AHB-MED", "box SKU"),
        ("AHB-MCUST-MONG", "custom box SKU"),
        ("BL-BASIC", "bulk item"),
        ("PK-WRAP", "packaging"),
        ("TR-SHIP", "transport"),
        ("EX-GIFT", "extras"),
        ("PR-CJAM-GEN", "cheese+jam pairing"),
        ("CEX-EC-MONG", "extra cheese assignment"),
    ])
    def test_non_pickable_prefixes(self, sku, reason):
        assert is_pickable(sku) is False, f"Expected {sku} ({reason}) to be non-pickable"

    def test_empty_string_is_not_pickable(self):
        assert is_pickable("") is False

    def test_whitespace_only_is_not_pickable(self):
        assert is_pickable("   ") is False

    def test_case_insensitive(self):
        assert is_pickable("ahb-med") is False
        assert is_pickable("ch-mcpc") is True


# ── resolve_curation_from_box_sku ────────────────────────────────────────

class TestResolveCurationFromBoxSku:
    def test_returns_none_for_empty(self):
        assert resolve_curation_from_box_sku("") is None
        assert resolve_curation_from_box_sku(None) is None

    @pytest.mark.parametrize("sku,expected", [
        ("AHB-MCUST-MONG", "MONG"),
        ("AHB-MCUST-MDT", "MDT"),
        ("AHB-MCUST-OWC", "OWC"),
        ("AHB-MCUST-SPN", "SPN"),
        ("AHB-MCUST-ALPN", "ALPN"),
        ("AHB-MCUST-ALPT", "ALPT"),
        ("AHB-MCUST-ISUN", "ISUN"),
        ("AHB-MCUST-HHIGH", "HHIGH"),
        ("AHB-MCUST-BYO", "BYO"),
        ("AHB-MCUST-SS", "SS"),
        ("AHB-MCUST-GEN", "GEN"),
    ])
    def test_custom_box_resolves_curation(self, sku, expected):
        assert resolve_curation_from_box_sku(sku) == expected

    @pytest.mark.parametrize("sku", [
        "AHB-MED", "AHB-LGE", "AHB-CMED", "AHB-CUR-MS", "AHB-BVAL",
    ])
    def test_monthly_patterns_resolve_to_monthly(self, sku):
        assert resolve_curation_from_box_sku(sku) == "MONTHLY"

    def test_nms_custom_is_monthly(self):
        # AHB-MCUST-NMS is in _MONTHLY_PATTERNS — resolves to MONTHLY, not NMS
        assert resolve_curation_from_box_sku("AHB-MCUST-NMS") == "MONTHLY"

    def test_ms_custom_is_monthly(self):
        # AHB-MCUST-MS is in _MONTHLY_PATTERNS — resolves to MONTHLY, not MS
        assert resolve_curation_from_box_sku("AHB-MCUST-MS") == "MONTHLY"

    def test_case_insensitive(self):
        assert resolve_curation_from_box_sku("ahb-mcust-mong") == "MONG"

    def test_strips_whitespace(self):
        assert resolve_curation_from_box_sku("  AHB-MCUST-MONG  ") == "MONG"

    def test_large_custom_box(self):
        assert resolve_curation_from_box_sku("AHB-LCUST-MONG") == "MONG"
