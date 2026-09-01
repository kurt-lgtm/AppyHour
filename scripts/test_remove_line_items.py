"""Offline stub tests for remove_line_items.py — no network, no live writes.

Run: python -m pytest scripts/test_remove_line_items.py -q
"""
import sys
import types

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\scripts")

# Stub the AppyHourMCP utils module BEFORE importing the tool's pure functions,
# so plan_targets' deferred `from utils import active_line_items` hits the stub.
_utils = types.ModuleType("utils")


def _active_line_items(order):
    refunded = {}
    for r in order.get("refunds", []):
        for rli in r.get("refund_line_items", []):
            refunded[rli["line_item_id"]] = refunded.get(rli["line_item_id"], 0) + rli["quantity"]
    return [{**li, "quantity": li.get("quantity", 0) - refunded.get(li["id"], 0)}
            for li in order.get("line_items", [])
            if li.get("quantity", 0) - refunded.get(li["id"], 0) > 0]


_utils.active_line_items = _active_line_items
sys.modules.setdefault("utils", _utils)

from remove_line_items import (  # noqa: E402
    absent_sku_refusal, cohort_skus, near_miss_candidates, paid_gate, plan_targets, verify_diff,
)


def _order(oid, name, tags, lines, refunds=None, cancelled=None):
    return {"id": oid, "name": name, "tags": tags, "line_items": lines,
            "refunds": refunds or [], "email": f"{name}@x.com", "cancelled_at": cancelled}


def _li(lid, sku, qty, fq=None):
    return {"id": lid, "sku": sku, "quantity": qty,
            "fulfillable_quantity": qty if fq is None else fq}


TAG = "_SHIP_2026-08-17"


def test_targeting_matches_tag_and_sku():
    orders = [
        _order(1, "#100", f"{TAG}, x", [_li(11, "PK-FOO", 2)]),
        _order(2, "#101", "other", [_li(21, "PK-FOO", 1)]),          # wrong tag
        _order(3, "#102", TAG, [_li(31, "CH-BAR", 1)]),              # wrong sku
    ]
    t = plan_targets(orders, TAG, "PK-FOO")
    assert [x["name"] for x in t] == ["#100"] and t[0]["qty"] == 2


def test_removed_lines_not_phantom_counted():
    # Refunded-out line stays at original quantity in raw line_items — must be excluded.
    o = _order(1, "#100", TAG, [_li(11, "PK-FOO", 1)],
               refunds=[{"refund_line_items": [{"line_item_id": 11, "quantity": 1}]}])
    assert plan_targets([o], TAG, "PK-FOO") == []


def test_zero_fulfillable_and_cancelled_excluded():
    orders = [
        _order(1, "#100", TAG, [_li(11, "PK-FOO", 1, fq=0)]),            # fulfilled
        _order(2, "#101", TAG, [_li(21, "PK-FOO", 1)], cancelled="2026-08-01"),
    ]
    assert plan_targets(orders, TAG, "PK-FOO") == []


def test_only_if_tagged_filter():
    orders = [
        _order(1, "#100", f"{TAG}, EXTRA", [_li(11, "PK-FOO", 1)]),
        _order(2, "#101", TAG, [_li(21, "PK-FOO", 1)]),
    ]
    t = plan_targets(orders, TAG, "PK-FOO", only_if_tagged="EXTRA")
    assert [x["name"] for x in t] == ["#100"]


def test_paid_refusal_without_allow_paid():
    targets = [{"id": 1, "name": "#100", "email": "", "qty": 1, "tags": []}]
    go, paid_rows, refuse = paid_gate(targets, {"#100": [9.66]}, allow_paid=False)
    assert refuse and go == [] and paid_rows[0]["paid_total"] == 9.66


def test_paid_allowed_with_flag_and_free_passes():
    targets = [{"id": 1, "name": "#100", "email": "", "qty": 1, "tags": []},
               {"id": 2, "name": "#101", "email": "", "qty": 1, "tags": []}]
    go, paid_rows, refuse = paid_gate(targets, {"#100": [9.0]}, allow_paid=True)
    assert not refuse and len(go) == 2 and len(paid_rows) == 1
    go2, pr2, r2 = paid_gate(targets, {}, allow_paid=False)
    assert not r2 and go2 == targets and pr2 == []


def test_join_zero_guard_refuses_near_miss_sku():
    # Live 2026-08-08: --sku PR-CJAM on a cohort whose real SKU is PR-CJAM-GEN.
    orders = [_order(1, "#100", TAG, [_li(11, "PR-CJAM-GEN", 1)]),
              _order(2, "#101", "other", [_li(21, "PR-CJAM", 1)])]  # off-cohort, must not count
    present = cohort_skus(orders, TAG)
    assert present == {"PR-CJAM-GEN"}
    msg = absent_sku_refusal("PR-CJAM", present, len(orders), TAG)
    assert msg and "PR-CJAM-GEN" in msg and "ZERO" in msg
    assert near_miss_candidates("PR-CJAM", present) == ["PR-CJAM-GEN"]
    # present SKU passes through untouched
    assert absent_sku_refusal("PR-CJAM-GEN", present, len(orders), TAG) is None


def test_join_zero_guard_reports_no_candidates_when_unrelated():
    present = cohort_skus([_order(1, "#100", TAG, [_li(11, "CH-FONT", 1)])], TAG)
    msg = absent_sku_refusal("MT-ZZZ", present, 1, TAG)
    assert msg and "no similar SKU" in msg


def test_verify_diff_clean_and_mismatch():
    plan = [{"name": "#100"}, {"name": "#101"}]
    assert verify_diff(plan, {"#100": 0, "#101": 0}, control_ok=True) == []
    probs = verify_diff(plan, {"#100": 2, "#101": None}, control_ok=True)
    assert any("still fulfillable qty=2" in p for p in probs)
    assert any("inconclusive" in p for p in probs)


def test_verify_control_failure_flags_zeros_inconclusive():
    probs = verify_diff([{"name": "#100"}], {"#100": 0}, control_ok=False)
    assert probs and "CONTROL" in probs[0]
