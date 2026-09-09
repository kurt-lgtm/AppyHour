"""Bundle explosion. Kurt 2026-09-08: "this order had a bundle but we didn't find it".

#178568 carried a $28 Simple Bundles parent with sku=None. Every check keys on SKU, so
the line added 0 children, matched no rule, resolved to no parent -- invisible. 262 such
lines were live on RMFG_20260828.
"""
from order_checks.bundles import _offer_parent, is_bundle_line
from order_checks.rules import slot_key


def test_null_sku_line_is_a_bundle_candidate():
    assert is_bundle_line(None)
    assert is_bundle_line("")
    assert is_bundle_line("BL-4FF")
    assert is_bundle_line("AHB-X12SUMS")
    assert not is_bundle_line("CH-BRZ")


def test_cex_and_ex_are_the_same_slot():
    # recipe says EX-EA, order carries CEX-EA -- "effectively the same" (Kurt)
    assert slot_key("CEX-EA") == slot_key("EX-EA")
    assert slot_key("CEX-EC") == slot_key("EX-EC")
    assert slot_key("CEX-EM") == slot_key("EX-EM")
    assert slot_key("CH-BRZ") == "CH-BRZ"


def test_box_offer_variant_title_maps_to_its_ahb_parent():
    assert _offer_parent("Medium (Serves 2-4)") == "AHB-MED"
    assert _offer_parent("Large (Serves 4-6)") == "AHB-LGE"
    assert _offer_parent("Summer Cookout") is None
