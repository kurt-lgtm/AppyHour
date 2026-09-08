"""MFG-name column resolution. Kurt 2026-09-08: "the most important issue is the mfg names".

Both failures below were live on RMFG_20260908:

  * 'Maple Frais Fromage' / 'Sottocenere with Truffles' sat in mfg_names_authoritative.csv
    the whole time and still came back UNMATCHED, because nothing read the file. An
    unmatched column is SILENTLY DROPPED from columns_sku, so every order carrying it
    compared clean -- the check reported 0 where it could not see.
  * The authority carries two SKUs whose names differ only by a trailing period,
    CH-BRZ 'Prairie Breeze' and CH-PRBZ 'Prairie Breeze.'. Folded by clean_title(), a
    last-write-wins reverse map chose CH-PRBZ and turned 26 correct orders into c2 diffs.
    An ambiguous name must resolve to NOTHING and defer to the live line items.
"""
from order_checks.sheet import _mfg_authority, resolve_columns


def _order(*pairs):
    return {"lineItems": {"edges": [{"node": {"sku": s, "title": t}} for s, t in pairs]}}


def test_authority_has_both_prairie_breeze_skus():
    a = _mfg_authority()
    assert a["CH-BRZ"] == "AHB (S_REG): Prairie Breeze"
    assert a["CH-PRBZ"] == "AHB (S_REG): Prairie Breeze."


def test_duplicate_mfg_name_never_picks_a_winner():
    sheet = {"1": {"columns": {"Prairie Breeze": 1}}}
    orders = {"1": _order(("CH-BRZ", "Prairie Breeze Cheddar"))}
    col, unmatched = resolve_columns(sheet, orders)
    assert col["Prairie Breeze"] == "CH-BRZ"
    assert unmatched == []


def test_authority_fills_a_column_no_live_order_carries():
    sheet = {"1": {"columns": {"Sottocenere with Truffles": 1, "Maple Frais Fromage": 1}}}
    orders = {"1": _order(("MT-PRO", "Prosciutto"))}
    col, unmatched = resolve_columns(sheet, orders)
    assert col["Sottocenere with Truffles"] == "CH-SOT"
    assert col["Maple Frais Fromage"] == "CH-NMMAP"
    assert unmatched == []


def test_unmatched_column_is_reported_not_dropped_silently():
    sheet = {"1": {"columns": {"Not A Real Cheese Xyzzy": 1}}}
    orders = {"1": _order(("MT-PRO", "Prosciutto"))}
    col, unmatched = resolve_columns(sheet, orders)
    assert unmatched == ["Not A Real Cheese Xyzzy"]
    assert "Not A Real Cheese Xyzzy" not in col
