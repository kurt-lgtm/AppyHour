"""Regression tests for generate_matrix_xlsx product-column selection.

Guards MATRIX_RULES rule 19: every pickable prefix that can appear on an order
must become a product column. wk0720 (RMFG_20260717) dropped MR-JRNL
("Cheese Journal") from the generated sheet for 38 orders because the
`food_pkg_prefixes` filter omitted MR-.
"""

from __future__ import annotations

from unittest.mock import patch

import openpyxl

import matrix_commander as mc


def _order(name: str, skus_qty: dict[str, int], tags: list[str] | None = None) -> dict:
    """Minimal Shopify order dict in the shape _fetch_orders_graphql returns."""
    edges = [
        {"node": {"sku": sku, "fulfillableQuantity": qty, "quantity": qty}}
        for sku, qty in skus_qty.items()
    ]
    return {
        "name": name,
        "tags": tags or [],
        "lineItems": {"edges": edges},
        "email": "cust@example.com",
        "phone": "5551234567",
        "note": "",
        "shippingAddress": {
            "firstName": "Test",
            "lastName": "Customer",
            "address1": "1 Main St",
            "address2": "",
            "city": "Indianapolis",
            "provinceCode": "IN",
            "zip": "46201",
            "phone": "5551234567",
        },
    }


_TRANSLATIONS = {
    "CH-BLR": "AHB (S_REG): Brie Locale Rouge",
    "MR-JRNL": "AHB (S_REG): Cheese Journal",
}


def _generate(tmp_path, orders):
    with (
        patch.object(mc, "_get_shopify_auth", return_value=("https://shop", {})),
        patch.object(mc, "_fetch_orders_graphql", return_value=orders),
        patch.object(mc, "load_mfg_translations", return_value=_TRANSLATIONS),
    ):
        out = mc.generate_matrix_xlsx(
            "RMFG_20260717", ship_date="2026-07-20", output_dir=str(tmp_path)
        )
    assert out, "generate_matrix_xlsx returned no path"
    ws = openpyxl.load_workbook(out, data_only=True).active
    headers = [str(c.value or "") for c in ws[1]]
    rows = {str(ws.cell(r, 2).value): r for r in range(2, ws.max_row + 1)}
    return ws, headers, rows


def test_mr_jrnl_gets_column(tmp_path):
    """An order carrying MR-JRNL produces a 'Cheese Journal' column with the qty."""
    orders = [
        _order("2001", {"CH-BLR": 2, "MR-JRNL": 1}),
        _order("2002", {"CH-BLR": 1}),
    ]
    ws, headers, _ = _generate(tmp_path, orders)

    jrnl_hdr = "AHB (S_REG): Cheese Journal"
    assert jrnl_hdr in headers, f"MR-JRNL column missing from headers: {headers}"

    # both orders share a customer name, so locate rows by OrderID (col A)
    order_rows = {str(ws.cell(r, 1).value): r for r in range(2, ws.max_row + 1)}

    col = headers.index(jrnl_hdr) + 1  # openpyxl is 1-indexed
    assert ws.cell(order_rows["2001"], col).value == 1
    # order without a journal leaves the cell blank, not 0
    assert ws.cell(order_rows["2002"], col).value in (None, "")

    # col-D Total (rule 0) counts the journal qty too: 2 CH + 1 journal = 3
    total_col = headers.index("Total") + 1
    assert ws.cell(order_rows["2001"], total_col).value == 3
