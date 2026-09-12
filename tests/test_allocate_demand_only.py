"""A SKU the cohort DEMANDS but the HAVE file never lists is HAVE 0 — the worst short there is.

Regression (Kurt 2026-09-11): compute_allocation scoped its universe to `have`, so an absent SKU
produced no row, no shortage and no push — its Shopify `available` stayed whatever it was, and the
week read "nothing short" while ten SKUs (CH-BLR, MT-IBRES, AC-WASP, MT-PSS, AC-ACRISP, CH-QOTA,
CH-TOPR, CH-BBLUE, AC-PMULB, AC-RBOL) were short on the _SHIP_2026-09-14 vF.
"""

from __future__ import annotations

from unittest.mock import patch

import matrix_commander as mc


def _zv(skus):
    return {s: {"item": f"gid://shopify/InventoryItem/{i}"} for i, s in enumerate(sorted(skus))}


def _alloc(have, orders, *, zv_skus=None):
    """_zero_variant_items is patched to echo whatever universe it is handed — the point of the
    test is WHICH skus compute_allocation asks about, not what Shopify answers."""
    seen: dict = {}

    def _fake_zv(base, headers, skus):
        seen["asked"] = set(skus)
        return _zv(zv_skus if zv_skus is not None else skus)

    with (
        patch.object(mc, "_fetch_orders_by_tag", return_value=orders),
        patch.object(mc, "_variant_catalog_prices", return_value={}),
        patch.object(mc, "_zero_variant_items", side_effect=_fake_zv),
    ):
        return mc.compute_allocation("_SHIP_2026-09-14", have, "base", {}), seen["asked"]


def test_demanded_sku_absent_from_have_is_a_short():
    orders = [{"line_items": [
        {"sku": "CH-KNOWN", "fulfillable_quantity": 2, "variant_id": "1"},
        {"sku": "CH-BLR", "fulfillable_quantity": 6, "variant_id": "2"},
    ]}]

    alloc, asked = _alloc({"CH-KNOWN": 10}, orders)

    assert "CH-BLR" in asked, "the absent SKU was never even looked up"
    row = next(r for r in alloc["rows"] if r["sku"] == "CH-BLR")
    assert row["have"] == 0
    assert row["need"] == 6
    assert row["delta"] == -6
    assert row["avail"] == 0, "nothing in stock must push available to 0, not leave it alone"
    assert "CH-BLR" in alloc["shorts"]
    assert alloc["all_covered"] is False
    assert alloc["demand_only_skus"] == ["CH-BLR"]


def test_structural_skus_absent_from_have_stay_skipped():
    """Made-to-order prefixes are never capped — being absent from HAVE does not change that."""
    orders = [{"line_items": [
        {"sku": "TR-ROSE", "fulfillable_quantity": 9, "variant_id": "1"},
        {"sku": "PK-TCUST", "fulfillable_quantity": 9, "variant_id": "2"},
        {"sku": "MR-JRNL", "fulfillable_quantity": 9, "variant_id": "3"},
    ]}]

    alloc, asked = _alloc({}, orders)

    assert asked == set()
    assert alloc["rows"] == []
    assert alloc["demand_only_skus"] == []


def test_have_only_sku_with_no_demand_still_reported():
    """The HAVE side of the union is unchanged: a counted SKU with zero demand still gets a row."""
    orders = [{"line_items": [{"sku": "CH-KNOWN", "fulfillable_quantity": 1, "variant_id": "1"}]}]

    alloc, asked = _alloc({"CH-KNOWN": 5, "CH-IDLE": 7}, orders)

    assert asked == {"CH-KNOWN", "CH-IDLE"}
    idle = next(r for r in alloc["rows"] if r["sku"] == "CH-IDLE")
    assert (idle["need"], idle["avail"]) == (0, 7)
    assert alloc["demand_only_skus"] == []
