"""Swap balance-invariant tests (Kurt 2026-07-09 — order #157930 shipped a
tray short after an edit removed qty 2 but added qty 1 back).

Covers the three swap writers:
- AppyHourMCP/tools/order_edit.py  _swap_order_skus  (MCP appyhour_swap_order_skus)
- fulfillment_web/shopify_swap.py  execute_swap       (canonical /swap path)
- fulfillment_web/shopify_swap.py  execute_conditional_swap

All Shopify GraphQL calls are mocked; no network.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "AppyHourMCP"))
sys.path.insert(0, str(ROOT / "InventoryReorder" / "fulfillment_web"))


# ---------------------------------------------------------------------------
# Fake GraphQL layer
# ---------------------------------------------------------------------------

def _begin_response(items):
    """items = [(li_id, sku, qty, rc_bundle)]"""
    edges = []
    for li_id, sku, qty, rc in items:
        attrs = [{"key": "_rc_bundle", "value": "1"}] if rc else []
        edges.append({"node": {"id": li_id, "sku": sku, "quantity": qty,
                               "customAttributes": attrs}})
    return {"orderEditBegin": {"calculatedOrder": {
        "id": "gid://shopify/CalculatedOrder/1",
        "lineItems": {"edges": edges}}, "userErrors": []}}


class FakeGQL:
    """Records mutations; answers begin/verify/commit."""

    def __init__(self, items, paid=None):
        self.items = items  # [(li_id, sku, qty, rc_bundle)]
        self.paid = paid or {}  # sku -> actual-paid amount (default 0.0)
        self.set_qty_calls = []   # (li_id, qty)
        self.add_calls = []       # (variant_gid, qty)
        self.committed = False

    def __call__(self, base, headers, query, variables=None, **kwargs):
        variables = variables or {}
        if "discountedUnitPriceAfterAllDiscountsSet" in query:
            # paid-item-guard pre-query (Kurt 2026-07-10)
            nodes = [{"sku": sku, "quantity": qty,
                      "discountedUnitPriceAfterAllDiscountsSet":
                          {"shopMoney": {"amount": str(self.paid.get(sku, 0.0))}},
                      "variant": {"id": f"gid://v/orig-{li_id}"}}
                     for li_id, sku, qty, _ in self.items]
            return {"order": {"lineItems": {"nodes": nodes}}}
        if "orderEditBegin" in query:
            return _begin_response(self.items)
        if "orderEditSetQuantity" in query:
            self.set_qty_calls.append((variables["lineItemId"], variables["quantity"]))
            return {"orderEditSetQuantity": {"calculatedOrder": {"id": "c1"}, "userErrors": []}}
        if "orderEditAddVariant" in query:
            self.add_calls.append((variables["variantId"], variables["quantity"]))
            return {"orderEditAddVariant": {"calculatedOrder": {"id": "c1"}, "userErrors": []}}
        if "orderEditCommit" in query:
            self.committed = True
            return {"orderEditCommit": {"order": {"id": "o1", "name": "#TEST"}, "userErrors": []}}
        if "CalculatedOrder" in query and "node(" in query:
            # pre-commit verify: reflect zeroed lines
            zeroed = {li for li, q in self.set_qty_calls if q == 0}
            edges = [{"node": {"id": li_id, "sku": sku, "quantity": 0 if li_id in zeroed else qty}}
                     for li_id, sku, qty, _ in self.items]
            return {"node": {"lineItems": {"edges": edges}}}
        raise AssertionError(f"unexpected query: {query[:80]}")


# ---------------------------------------------------------------------------
# order_edit._swap_order_skus (MCP path)
# ---------------------------------------------------------------------------

def _run_mcp_swap(monkeypatch, items, swaps, gids, tmp_path):
    from tools import order_edit
    fake = FakeGQL(items)
    monkeypatch.setattr(order_edit, "shopify_graphql", fake)
    monkeypatch.setattr(order_edit, "_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    result = order_edit._swap_order_skus(
        "base", {}, "gid://shopify/Order/1", swaps, gids, rc_bundle_only=True)
    return fake, result


def test_mcp_swap_carries_qty(monkeypatch, tmp_path):
    """qty-2 line swaps to qty-2 add — never 1."""
    fake, result = _run_mcp_swap(
        monkeypatch,
        items=[("li1", "TR-ICTRY", 2, True)],
        swaps={"TR-ICTRY": "TR-TRUFF"},
        gids={"TR-TRUFF": "gid://v/9"},
        tmp_path=tmp_path,
    )
    assert fake.add_calls == [("gid://v/9", 2)]
    assert fake.committed
    assert result == ["TR-ICTRY->TR-TRUFF(qty=2)"]


def test_mcp_swap_duplicate_sku_lines_all_swapped(monkeypatch, tmp_path):
    """Two lines of the same SKU: BOTH zeroed, total qty added (dict bug fix)."""
    fake, result = _run_mcp_swap(
        monkeypatch,
        items=[("li1", "TR-TAPAS", 1, True), ("li2", "TR-TAPAS", 2, True)],
        swaps={"TR-TAPAS": "TR-TRUFF"},
        gids={"TR-TRUFF": "gid://v/9"},
        tmp_path=tmp_path,
    )
    assert sorted(li for li, q in fake.set_qty_calls if q == 0) == ["li1", "li2"]
    assert sum(q for _, q in fake.add_calls) == 3
    assert fake.committed


def test_mcp_swap_audit_written(monkeypatch, tmp_path):
    _run_mcp_swap(
        monkeypatch,
        items=[("li1", "TR-ICTRY", 2, True)],
        swaps={"TR-ICTRY": "TR-TRUFF"},
        gids={"TR-TRUFF": "gid://v/9"},
        tmp_path=tmp_path,
    )
    log = (tmp_path / "audit.jsonl").read_text()
    assert '"result": "OK"' in log
    assert '"qty_removed": 2' in log


# ---------------------------------------------------------------------------
# shopify_swap.execute_swap (canonical /swap path)
# ---------------------------------------------------------------------------

def _run_exec_swap(monkeypatch, items, tmp_path):
    import shopify_swap
    fake = FakeGQL(items)
    monkeypatch.setattr(shopify_swap, "_gql", fake)
    monkeypatch.setattr(shopify_swap, "_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(shopify_swap.time, "sleep", lambda s: None)
    result = shopify_swap.execute_swap(
        "store", "token", "gid://shopify/Order/1", "TR-ICTRY", "gid://v/9")
    return fake, result


def test_execute_swap_carries_qty(monkeypatch, tmp_path):
    fake, result = _run_exec_swap(
        monkeypatch, items=[("li1", "TR-ICTRY", 2, True)], tmp_path=tmp_path)
    assert result["success"]
    assert fake.add_calls == [("gid://v/9", 2)]


def test_execute_swap_multi_line_same_sku(monkeypatch, tmp_path):
    """Both lines zeroed, add = summed qty (was: first line only)."""
    fake, result = _run_exec_swap(
        monkeypatch,
        items=[("li1", "TR-ICTRY", 1, True), ("li2", "TR-ICTRY", 2, True)],
        tmp_path=tmp_path,
    )
    assert result["success"]
    assert sorted(li for li, q in fake.set_qty_calls if q == 0) == ["li1", "li2"]
    assert fake.add_calls == [("gid://v/9", 3)]


# ---------------------------------------------------------------------------
# shopify_swap.execute_conditional_swap — balance guard
# ---------------------------------------------------------------------------

def _run_conditional(monkeypatch, items, removes, adds, tmp_path):
    import shopify_swap
    fake = FakeGQL(items)
    monkeypatch.setattr(shopify_swap, "_gql", fake)
    monkeypatch.setattr(shopify_swap, "_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(shopify_swap.time, "sleep", lambda s: None)
    result = shopify_swap.execute_conditional_swap(
        "store", "token", "gid://shopify/Order/1", removes, adds, dry_run=False)
    return fake, result


def test_conditional_unbalanced_aborts(monkeypatch, tmp_path):
    """#157930 signature: remove qty-2 line, add qty 1 -> ABORT, no mutation."""
    fake, result = _run_conditional(
        monkeypatch,
        items=[("li1", "TR-ICTRY", 2, True)],
        removes=["TR-ICTRY"],
        adds=[("gid://v/9", 1)],
        tmp_path=tmp_path,
    )
    assert not result["success"]
    assert "unbalanced" in result["error"]
    assert fake.set_qty_calls == []
    assert fake.add_calls == []
    assert not fake.committed
    assert "ABORT:unbalanced" in (tmp_path / "audit.jsonl").read_text()


def test_conditional_balanced_commits(monkeypatch, tmp_path):
    fake, result = _run_conditional(
        monkeypatch,
        items=[("li1", "TR-ICTRY", 2, True)],
        removes=["TR-ICTRY"],
        adds=[("gid://v/9", 2)],
        tmp_path=tmp_path,
    )
    assert result["success"]
    assert fake.committed
    assert fake.add_calls == [("gid://v/9", 2)]
    assert "OK:conditional" in (tmp_path / "audit.jsonl").read_text()


# ---------------------------------------------------------------------------
# Paid-item guard (Kurt 2026-07-10 — never touch paid lines; manual accounting
# repair after a paid PINA/CCCS line slipped into a rotation batch)
# ---------------------------------------------------------------------------

def test_execute_swap_refuses_paid_line(monkeypatch, tmp_path):
    import shopify_swap
    fake = FakeGQL([("li1", "AC-PINA", 1, False)], paid={"AC-PINA": 5.0})
    monkeypatch.setattr(shopify_swap, "_gql", fake)
    monkeypatch.setattr(shopify_swap, "_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(shopify_swap.time, "sleep", lambda s: None)
    result = shopify_swap.execute_swap(
        "store", "token", "gid://shopify/Order/1", "AC-PINA", "gid://v/9")
    assert not result["success"]
    assert "paid-item guard" in result["error"]
    assert fake.set_qty_calls == [] and fake.add_calls == [] and not fake.committed
    assert "REFUSED:paid-item-guard" in (tmp_path / "audit.jsonl").read_text()


def test_execute_swap_allow_paid_overrides(monkeypatch, tmp_path):
    import shopify_swap
    fake = FakeGQL([("li1", "AC-PINA", 1, False)], paid={"AC-PINA": 5.0})
    monkeypatch.setattr(shopify_swap, "_gql", fake)
    monkeypatch.setattr(shopify_swap, "_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(shopify_swap.time, "sleep", lambda s: None)
    result = shopify_swap.execute_swap(
        "store", "token", "gid://shopify/Order/1", "AC-PINA", "gid://v/9", allow_paid=True)
    assert result["success"] and fake.committed


def test_execute_swap_audit_records_old_variant(monkeypatch, tmp_path):
    """Reverts must restore the EXACT variant removed — audit carries it."""
    fake, result = _run_exec_swap(
        monkeypatch, items=[("li1", "TR-ICTRY", 2, True)], tmp_path=tmp_path)
    assert result["success"]
    assert "gid://v/orig-li1" in (tmp_path / "audit.jsonl").read_text()


def test_mcp_swap_skips_paid_line(monkeypatch, tmp_path):
    from tools import order_edit
    fake = FakeGQL([("li1", "AC-PINA", 1, False), ("li2", "MT-CCCS", 1, False)],
                   paid={"AC-PINA": 5.0})
    monkeypatch.setattr(order_edit, "shopify_graphql", fake)
    monkeypatch.setattr(order_edit, "_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    result = order_edit._swap_order_skus(
        "base", {}, "gid://shopify/Order/1",
        {"AC-PINA": "AC-APR", "MT-CCCS": "MT-SFEN"},
        {"AC-APR": "gid://v/8", "MT-SFEN": "gid://v/9"},
        rc_bundle_only=False)
    assert result == ["MT-CCCS->MT-SFEN(qty=1)"]  # paid PINA line skipped
    assert "SKIP:paid-item-guard" in (tmp_path / "audit.jsonl").read_text()


def test_mcp_swap_unbalanced_impossible_by_construction(monkeypatch, tmp_path):
    """MCP path derives add qty from removed qty — assert guard still present
    by checking module source carries the invariant."""
    from tools import order_edit
    import inspect
    src = inspect.getsource(order_edit._swap_order_skus)
    assert "qty_removed != qty_added" in src
