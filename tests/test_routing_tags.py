"""Tests for routing tag helpers in gel_pack_shopify.py."""

import pytest
from gel_pack_shopify import (
    get_routing_tag_prefix,
    shorten_routing_tag,
    shorten_routing_tags,
    validate_routing_tag_combo,
)

# ── get_routing_tag_prefix ───────────────────────────────────────────────

class TestGetRoutingTagPrefix:
    @pytest.mark.parametrize("tag,expected", [
        ("!NO UPS - Dallas_AHB!", "NO"),
        ("!ANY - Dallas_AHB!", "ANY"),
        ("!FedEx - Nashville_AHB!", "FedEx"),
        ("!UPS - Anaheim_AHB!", "UPS"),
        ("!OnTrac - Anaheim_AHB!", "OnTrac"),
    ])
    def test_known_prefixes(self, tag, expected):
        assert get_routing_tag_prefix(tag) == expected

    def test_unknown_prefix_returns_other(self):
        assert get_routing_tag_prefix("!SomeOther - Tag!") == "OTHER"


# ── shorten_routing_tag ──────────────────────────────────────────────────

class TestShortenRoutingTag:
    def test_strips_bang_and_ahb_suffix(self):
        assert shorten_routing_tag("!NO UPS - Dallas_AHB!") == "NO UPS - DAL"

    def test_abbreviates_hub_names(self):
        assert shorten_routing_tag("!ANY - Nashville_AHB!") == "ANY - NAS"
        assert shorten_routing_tag("!FedEx - Anaheim_AHB!") == "FedEx - ANA"
        assert shorten_routing_tag("!UPS - Indianapolis_AHB!") == "UPS - IND"

    def test_plain_tag_with_only_bang(self):
        assert shorten_routing_tag("!ExtraGel24oz!") == "ExtraGel24oz"

    def test_no_bangs_passthrough(self):
        assert shorten_routing_tag("plaintext") == "plaintext"


# ── shorten_routing_tags ─────────────────────────────────────────────────

class TestShortenRoutingTags:
    def test_multiple_tags(self):
        result = shorten_routing_tags(["!NO UPS - Dallas_AHB!", "!NO FedEx - Dallas_AHB!"])
        assert result == "NO UPS - DAL, NO FedEx - DAL"

    def test_empty_list(self):
        assert shorten_routing_tags([]) == ""

    def test_none(self):
        assert shorten_routing_tags(None) == ""


# ── validate_routing_tag_combo ───────────────────────────────────────────

class TestValidateRoutingTagCombo:
    def test_empty_tags_valid(self):
        valid, msg = validate_routing_tag_combo([])
        assert valid is True
        assert msg == ""

    def test_single_fedex_valid(self):
        valid, _ = validate_routing_tag_combo(["!FedEx - Dallas_AHB!"])
        assert valid is True

    def test_single_ups_valid(self):
        valid, _ = validate_routing_tag_combo(["!UPS - Nashville_AHB!"])
        assert valid is True

    def test_single_any_valid(self):
        valid, _ = validate_routing_tag_combo(["!ANY - Dallas_AHB!"])
        assert valid is True

    def test_single_no_valid(self):
        valid, _ = validate_routing_tag_combo(["!NO UPS - Dallas_AHB!"])
        assert valid is True

    def test_multiple_no_tags_valid(self):
        valid, _ = validate_routing_tag_combo([
            "!NO UPS - Dallas_AHB!",
            "!NO FedEx - Dallas_AHB!",
        ])
        assert valid is True

    def test_exclusive_carrier_cannot_combine(self):
        valid, msg = validate_routing_tag_combo([
            "!FedEx - Dallas_AHB!",
            "!NO UPS - Dallas_AHB!",
        ])
        assert valid is False
        assert "FedEx" in msg

    def test_any_cannot_combine_with_no(self):
        valid, msg = validate_routing_tag_combo([
            "!ANY - Dallas_AHB!",
            "!NO UPS - Dallas_AHB!",
        ])
        assert valid is False

    def test_multiple_any_invalid(self):
        valid, msg = validate_routing_tag_combo([
            "!ANY - Dallas_AHB!",
            "!ANY - Nashville_AHB!",
        ])
        assert valid is False
        assert "one !ANY" in msg
