"""Paid-item guard classification — production line shapes only.

🔴 The shapes below are copied from REAL Shopify lines, not invented. A guard test built
on injected shapes is how three fail-opens shipped green (memory: fail-closed-needs-a-
production-shape-test). The wk0824 failure was the mirror image — fail-CLOSED on 107
legitimate lines because catalog price alone was read as "customer paid".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "InventoryReorder" / "fulfillment_web"))


def _is_paid(line):
    """Mirrors the predicate in shopify_swap.execute_swap."""
    return bool(
        line["paid"] > 0
        or (line["catalog_price"] > 0 and line.get("onetime") and not line.get("rc_bundle"))
    )


# --- real shapes -----------------------------------------------------------------
# #176610 AC-GLAW, _SHIP_2026-08-24, uncustomized Subscription First Order.
# Box content. Catalog 6.00 because AC-GLAW also sells standalone; customer paid $0.
RC_BUNDLE_PRICED = {"paid": 0.0, "catalog_price": 6.0, "rc_bundle": True, "onetime": False}

# #163709 MT-CCSP — Recharge onetime add-on. Money collected on the Recharge charge,
# pushed to Shopify at $0. This is the case the catalog check exists for.
RECHARGE_ONETIME = {"paid": 0.0, "catalog_price": 9.0, "rc_bundle": False, "onetime": True}

# A straightforward paid line: customer paid on the Shopify order itself.
PAID_ON_LINE = {"paid": 9.0, "catalog_price": 9.0, "rc_bundle": False, "onetime": True}

# $0 in-box variant, no catalog price at all.
FREE_IN_BOX = {"paid": 0.0, "catalog_price": 0.0, "rc_bundle": True, "onetime": False}


def test_rc_bundle_priced_variant_is_not_paid():
    """The wk0824 regression: 107 swaps refused on paid=0.0 catalog=6.0."""
    assert _is_paid(RC_BUNDLE_PRICED) is False


def test_recharge_onetime_still_caught():
    """#163709 must stay protected — catalog>0 on a real add-on line."""
    assert _is_paid(RECHARGE_ONETIME) is True


def test_paid_on_line_is_paid():
    assert _is_paid(PAID_ON_LINE) is True


def test_free_in_box_is_not_paid():
    assert _is_paid(FREE_IN_BOX) is False


def test_onetime_without_price_is_not_paid():
    """An add-on line at catalog $0 has no money behind it."""
    assert _is_paid({"paid": 0.0, "catalog_price": 0.0, "rc_bundle": False, "onetime": True}) is False


def test_predicate_matches_shipped_source():
    """Guard against the test drifting from the module it mirrors."""
    src = (Path(__file__).resolve().parents[1] / "InventoryReorder" / "fulfillment_web"
           / "shopify_swap.py").read_text(encoding="utf-8")
    assert 'p["catalog_price"] > 0 and p.get("onetime") and not p.get("rc_bundle")' in src


# --- order_edit copy of the predicate stays in sync (fixed 2026-08-29) ----------------

def test_order_edit_predicate_matches_shipped_source():
    """The MCP path (order_edit._paid_skus_on_order) carries the same fixed predicate.

    It halted the 08-31 vF mirror on #174564 (MT-SFEN paid $0, catalog $9, _rc_bundle)
    while shopify_swap was already fixed — two copies, one bug. This pins the ported fix.
    """
    src = (Path(__file__).resolve().parents[1] / "AppyHourMCP" / "tools"
           / "order_edit.py").read_text(encoding="utf-8")
    assert "catalog > 0 and onetime and not rc_bundle" in src
    # the old unconditional form must be gone
    assert "if paid > 0 or catalog > 0:" not in src
