"""MATRIX_RULES rule 20 — vFGR gift-order REPLACE semantics (Kurt 2026-07-24, wk0727 by hand).

Gift redemption orders are UNEDITABLE in Shopify, so the matrix rows built from Shopify carry
stale/too-few items. The weekly *_vFGR.xlsx is the ITEM-TRUTH:
  (a) A-suffix twin rows (Simple Bundles "Associated Order") fold onto the parent FIRST;
      pre-combined vFGRs (remove=1, no A row) fold nothing — never sum twice.
  (b) REPLACE matrix rows by OrderID: meta + items from vFGR; PRESERVE matrix Tags (engine
      col L) + ProductionDay; Notes blank (rule 18); Total recomputed (rule 0).
  (c) vFGR OrderID missing from the matrix (e.g. _HOLD) = loud GiftMergeError; explicit
      drop only via drop_oids.
"""

from __future__ import annotations

import openpyxl
import pytest

import matrix_commander as mc
from matrix_commander import GiftMergeError

META = ["OrderID", "Name", "Total", "Zip", "Tags", "Notes", "ProductionDay"]
P1 = "AHB (S_REG): Montasio"
P2 = "AHB (S_REG): Barista"
P3 = "AHB (S_REG): Chorizo Seco"
ENGINE_L = "!ExtraGel48oz!, !FedEx Home Delivery - Indianapolis_AHB!, Gift_Redemption"


def _write_xlsx(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Access_LIVE"
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(str(path))
    return str(path)


def _load(path):
    ws = openpyxl.load_workbook(path, data_only=True)["Access_LIVE"]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    return rows[0], {str(r[0]): r for r in rows[1:] if r[0] is not None}


def _main(tmp_path):
    # stale matrix row for gift 164878 (too few items) + a regular order
    return _write_xlsx(
        tmp_path / "main.xlsx",
        META + [P1, P2, P3],
        [
            [164878, "Old Stale Name", 1, "92026", ENGINE_L, "junk note", "SAT", 1, None, None],
            [150000, "Regular", 2, "10001", "!UPS Ground - Dallas_AHB!", None, "SAT", 1, 1, None],
        ],
    )


def test_replace_overwrites_items_preserves_engine_col_l(tmp_path):
    gift = _write_xlsx(
        tmp_path / "gift.xlsx",
        META + [P1, P2],
        [[164878, "True Recipient", 99, "92026", "Gift Redemption, RMFG", "gift note", "SAT", None, 2]],
    )
    headers, rows = _load(mc.merge_gift_xlsx(_main(tmp_path), gift))
    row = rows["164878"]

    assert row[headers.index("Name")] == "True Recipient"           # meta from vFGR
    assert row[headers.index(P1)] is None                            # stale item WIPED
    assert row[headers.index(P2)] == 2                               # item-truth refilled
    assert row[headers.index("Tags")] == ENGINE_L                    # engine col L PRESERVED
    assert row[headers.index("ProductionDay")] == "SAT"
    assert row[headers.index("Notes")] is None                       # rule 18: Notes blank
    assert row[headers.index("Total")] == 2                          # rule 0: recomputed, not 99
    # untouched regular order
    assert rows["150000"][headers.index(P1)] == 1


def test_twin_fold_sums_onto_parent_and_drops_a_row(tmp_path):
    gift = _write_xlsx(
        tmp_path / "gift.xlsx",
        META + [P1, P2, "remove"],
        [
            [164878, "Parent", 0, "92026", "", None, "SAT", 1, 1, None],
            ["164878A", "Twin", 0, "92026", "", None, "SAT", 2, None, None],
        ],
    )
    headers, rows = _load(mc.merge_gift_xlsx(_main(tmp_path), gift))

    assert "164878A" not in rows                                     # twin never ships
    row = rows["164878"]
    assert row[headers.index(P1)] == 3                               # 1 + 2 summed
    assert row[headers.index(P2)] == 1
    assert row[headers.index("Total")] == 4
    assert "remove" not in headers                                   # bookkeeping col not unioned


def test_precombined_remove_flag_never_sums_twice(tmp_path):
    # wk0727 shape: parent already carries the fold, remove=1, NO A row
    gift = _write_xlsx(
        tmp_path / "gift.xlsx",
        META + [P1, "remove"],
        [[164878, "Parent", 16, "92026", "", None, "SAT", 3, 1]],
    )
    headers, rows = _load(mc.merge_gift_xlsx(_main(tmp_path), gift))
    assert rows["164878"][headers.index(P1)] == 3                    # untouched, not doubled
    assert rows["164878"][headers.index("Total")] == 3


def test_orphan_twin_row_is_loud_error(tmp_path):
    gift = _write_xlsx(
        tmp_path / "gift.xlsx", META + [P1], [["164878A", "Twin", 0, "92026", "", None, "SAT", 2]]
    )
    with pytest.raises(GiftMergeError, match="164878A"):
        mc.merge_gift_xlsx(_main(tmp_path), gift)


def test_missing_cohort_oid_is_loud_error(tmp_path):
    # wk0727 #165505: _HOLD order in the vFGR but not the cohort — never silent
    gift = _write_xlsx(
        tmp_path / "gift.xlsx",
        META + [P1],
        [[164878, "P", 0, "92026", "", None, "SAT", 1], [165505, "Held", 0, "91915", "", None, "SAT", 1]],
    )
    with pytest.raises(GiftMergeError, match="165505"):
        mc.merge_gift_xlsx(_main(tmp_path), gift)


def test_explicit_gift_drop_excludes_and_reports(tmp_path, capsys):
    gift = _write_xlsx(
        tmp_path / "gift.xlsx",
        META + [P1],
        [[164878, "P", 0, "92026", "", None, "SAT", 1], [165505, "Held", 0, "91915", "", None, "SAT", 1]],
    )
    headers, rows = _load(mc.merge_gift_xlsx(_main(tmp_path), gift, drop_oids={"165505"}))
    assert "165505" not in rows
    assert rows["164878"][headers.index(P1)] == 1
    assert "165505" in capsys.readouterr().out                       # drop surfaced, never silent
