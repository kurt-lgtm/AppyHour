"""compute_allocation must NEVER cap made-to-order structural SKUs (PK-/MR-/TR-).

Regression (Kurt 2026-07-29): the skip literal was ("PK-", "MR-") only, so trays fell through and
84 TR- `available` pushes landed live through 2026-07-28. MATRIX_RULES rule 13.
"""

from __future__ import annotations

from unittest.mock import patch

import matrix_commander as mc


def _zv(skus):
    return {s: {"item": f"gid://shopify/InventoryItem/{i}"} for i, s in enumerate(skus)}


def _alloc(have, orders, zv_skus):
    with (
        patch.object(mc, "_fetch_orders_by_tag", return_value=orders),
        patch.object(mc, "_variant_catalog_prices", return_value={}),
        patch.object(mc, "_zero_variant_items", return_value=_zv(zv_skus)),
    ):
        return mc.compute_allocation("_SHIP_2026-07-27", have, "base", {})


def test_structural_prefixes_never_capped():
    skus = ["CH-QOTA", "PK-BOX", "MR-JRNL", "TR-TRUFF", "TR-AAB"]
    have = dict.fromkeys(skus, 10)
    orders = [{"line_items": [{"sku": "CH-QOTA", "fulfillable_quantity": 3, "variant_id": "1"}]}]

    alloc = _alloc(have, orders, skus)

    pushed = {r["sku"] for r in alloc["rows"]}
    assert pushed == {"CH-QOTA"}
    assert set(alloc["skipped_structural"]) == {"PK-BOX", "MR-JRNL", "TR-TRUFF", "TR-AAB"}


def test_no_tr_sku_reaches_rows_even_when_short():
    """A tray whose HAVE < NEED must still be skipped, not pushed as a shortage row."""
    skus = ["TR-ICTRY"]
    orders = [{"line_items": [{"sku": "TR-ICTRY", "fulfillable_quantity": 99, "variant_id": "1"}]}]

    alloc = _alloc({"TR-ICTRY": 1}, orders, skus)

    assert alloc["rows"] == []
    assert alloc["shorts"] == []
    assert alloc["skipped_structural"] == ["TR-ICTRY"]
