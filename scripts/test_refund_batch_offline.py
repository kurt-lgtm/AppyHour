"""Offline stub tests for refund_batch amount math + idempotency. NO network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refund_batch import (
    absent_sku_refusal, actual_paid_for_sku, has_existing_refund, near_miss_candidates,
    note_keyword, select_cohort,
)


def _order(lines, refunds=None):
    return {"line_items": lines, "refunds": refunds or []}


def li(id, sku, qty, price, disc=0.0, tax=0.0):
    return {"id": id, "sku": sku, "quantity": qty, "price": str(price),
            "discount_allocations": [{"amount": str(disc)}] if disc else [],
            "tax_lines": [{"price": str(tax)}] if tax else []}


def test_actual_paid_net_of_discount_incl_tax():
    o = _order([li(1, "CH-FONT", 2, 20.00, disc=5.00, tax=2.10)])
    assert actual_paid_for_sku(o, "CH-FONT") == 37.10  # 40 - 5 + 2.10, NOT list 40


def test_missing_sku_returns_none():
    assert actual_paid_for_sku(_order([li(1, "CH-FONT", 1, 10)]), "CH-FONTAL") is None


def test_removed_line_excluded():
    # fully refunded line → active_line_items drops it → None (never list price)
    o = _order([li(1, "CH-FONT", 1, 10)],
               refunds=[{"refund_line_items": [{"line_item_id": 1, "quantity": 1}]}])
    assert actual_paid_for_sku(o, "CH-FONT") is None


def test_partial_removal_prorated():
    o = _order([li(1, "CH-FONT", 2, 10.00, tax=1.00)],
               refunds=[{"refund_line_items": [{"line_item_id": 1, "quantity": 1}]}])
    assert actual_paid_for_sku(o, "CH-FONT") == 10.50  # (20+1)/2


def test_idempotency_note_match():
    note = "WK0810 short — $12 refund issued"
    assert has_existing_refund([{"note": "WK0810 short — $12 refund issued"}], note)
    assert not has_existing_refund([{"note": "some other refund"}], note)
    assert not has_existing_refund([], note)
    assert note_keyword(note) == "WK0810 short"


def test_join_zero_guard_refuses_near_miss_sku():
    # Live 2026-08-08: PR-CJAM matched 0 of 2321 orders; the real SKU is PR-CJAM-GEN.
    present = {"PR-CJAM-GEN", "CH-FONT"}
    msg = absent_sku_refusal("PR-CJAM", present, 2321, "_SHIP_2026-08-10")
    assert msg and "PR-CJAM-GEN" in msg and "ZERO of 2321" in msg
    assert near_miss_candidates("PR-CJAM", present) == ["PR-CJAM-GEN"]
    assert absent_sku_refusal("CH-FONT", present, 2321, "_SHIP_2026-08-10") is None


def _cohort_order(name, tags, lines, cancelled=None):
    return {"id": name, "name": f"#{name}", "tags": tags, "line_items": lines,
            "refunds": [], "cancelled_at": cancelled}


def test_select_cohort_matches_tag_where_semantics():
    """🔴 644-vs-1 (2026-08-09): refund_batch reported ~1 CEX-EC order on _SHIP_2026-08-10 while
    tag_where reported 644. Selection was never wrong — it paged the WHOLE order history with no
    server-side `tag`, so agreement depended on running all ~680 pages to completion. This pins
    the selection predicate itself: tag membership + not-cancelled + active_line_items SKU."""
    tag = "_SHIP_2026-08-10"
    orders = [
        _cohort_order("1", f"foo,{tag},bar", [li(1, "CEX-EC", 1, 0)]),
        _cohort_order("2", f" {tag} ", [li(2, "CEX-EC", 1, 5)]),          # whitespace-padded tag
        _cohort_order("3", tag, [li(3, "CH-FONT", 1, 5)]),                # on tag, other SKU
        _cohort_order("4", "_SHIP_2026-08-03", [li(4, "CEX-EC", 1, 5)]),  # wrong tag
        _cohort_order("5", tag, [li(5, "CEX-EC", 1, 5)], cancelled="2026-08-01"),
        _cohort_order("6", f"{tag}_HOLD", [li(6, "CEX-EC", 1, 5)]),       # NOT a substring match
    ]
    hits, present, cohort_n = select_cohort(orders, tag, "CEX-EC")
    assert [o["name"] for o in hits] == ["#1", "#2"]
    assert cohort_n == 3                       # 1,2,3 — cancelled and off-tag excluded
    assert present == {"CEX-EC", "CH-FONT"}    # SKU vocabulary feeds the join-zero guard


def test_zero_paid_sku_is_matched_then_skipped_not_unmatched():
    """The 644 CEX-EC lines were all price 0.00 in-box components: selection HITS, amount math
    owes $0. Skipping must never be confusable with 'the filter matched nothing'."""
    o = _order([li(1, "CEX-EC", 1, 0.00)])
    assert actual_paid_for_sku(o, "CEX-EC") == 0.0    # found, worth $0
    assert actual_paid_for_sku(o, "CEX-XX") is None   # genuinely absent — a different outcome


def test_join_zero_guard_no_candidates():
    msg = absent_sku_refusal("MT-ZZZ", {"CH-FONT"}, 10, "_SHIP_2026-08-10")
    assert msg and "no similar SKU" in msg


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  PASS {name}")
    print("all offline tests passed")
