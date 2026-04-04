"""Tests for cmd_sync sequential loop, pass filtering, retry-failed, and pass gate.

Plan: 02-02 — Shopify Sync Battle-Test
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from unittest.mock import MagicMock, call, patch

import pytest

from pipeline.dry_run_guard import DryRunViolationError
from pipeline.pipeline_state import PassProgress, PipelineStage, PipelineState, _active_prefixes
from tests.conftest import sample_order, sample_pass_progress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(stage: PipelineStage = PipelineStage.IDLE, **kwargs) -> PipelineState:
    return PipelineState(pipeline_id="test-2026-04-04", stage=stage, **kwargs)


def _shopify_graphql_side_effect(base, headers, query, variables):
    """Returns minimal valid Shopify GraphQL responses for edit mutations."""
    if "orderEditBegin" in query:
        return {"orderEditBegin": {"calculatedOrder": {"id": "gid://shopify/CalculatedOrder/999"}, "userErrors": []}}
    if "orderEditAddVariant" in query:
        return {
            "orderEditAddVariant": {"calculatedOrder": {"id": "gid://shopify/CalculatedOrder/999"}, "userErrors": []}
        }
    if "orderEditCommit" in query:
        return {"orderEditCommit": {"order": {"id": "gid://shopify/Order/1001"}, "userErrors": []}}
    return {}


# ---------------------------------------------------------------------------
# Test 1: Sequential loop calls limiter
# ---------------------------------------------------------------------------


def test_sequential_loop_calls_limiter():
    """LeakyBucketLimiter.wait(cost=10) is called before each GraphQL mutation."""
    from matrix_commander import sync_order_to_shopify, SyncResult
    from pipeline.rate_limiter import LeakyBucketLimiter
    from pipeline.dry_run_guard import DryRunGuard

    mock_limiter = MagicMock(spec=LeakyBucketLimiter)
    guard = DryRunGuard(dry_run=False)

    order = sample_order("1001", "#1001")
    matrix_skus = {"CH-MCPC": 1, "MT-LONZ": 1}
    variant_gids = {"CH-MCPC": "gid://shopify/ProductVariant/111", "MT-LONZ": "gid://shopify/ProductVariant/222"}

    with patch("matrix_commander._shopify_graphql", side_effect=_shopify_graphql_side_effect):
        result = sync_order_to_shopify(
            "https://shop.myshopify.com",
            {},
            order,
            matrix_skus,
            variant_gids,
            mode="smart",
            limiter=mock_limiter,
            guard=guard,
            active_prefixes=("CH-", "MT-", "AC-", "PK-", "TR-"),
        )

    # orderEditBegin + 2x orderEditAddVariant + orderEditCommit = 4 calls
    assert mock_limiter.wait.call_count == 4
    for c in mock_limiter.wait.call_args_list:
        assert c == call(cost=10)
    assert result.status == "updated"


# ---------------------------------------------------------------------------
# Test 2: DryRunGuard blocks mutation
# ---------------------------------------------------------------------------


def test_dry_run_guard_blocks_mutation():
    """DryRunGuard.assert_can_mutate() raises DryRunViolationError when dry_run=True."""
    from matrix_commander import sync_order_to_shopify
    from pipeline.dry_run_guard import DryRunGuard

    guard = DryRunGuard(dry_run=True)
    order = sample_order("1001", "#1001")
    matrix_skus = {"CH-MCPC": 1}
    variant_gids = {"CH-MCPC": "gid://shopify/ProductVariant/111"}

    with patch("matrix_commander._shopify_graphql", side_effect=_shopify_graphql_side_effect):
        with pytest.raises(DryRunViolationError):
            sync_order_to_shopify(
                "https://shop.myshopify.com",
                {},
                order,
                matrix_skus,
                variant_gids,
                mode="smart",
                guard=guard,
                active_prefixes=("CH-", "MT-", "AC-", "PK-", "TR-"),
            )


# ---------------------------------------------------------------------------
# Test 3: Checkpoint saved after each order
# ---------------------------------------------------------------------------


def test_checkpoint_saved_after_each_order():
    """CheckpointStore.save() is called once per order processed."""
    from matrix_commander import cmd_sync

    orders = [sample_order("1001", "#1001"), sample_order("1002", "#1002")]
    shopify_orders_raw = [
        {"id": "1001", "name": "#1001", "tags": "", "line_items": []},
        {"id": "1002", "name": "#1002", "tags": "", "line_items": []},
    ]

    fake_parse_result = [
        MagicMock(order_id="1001", assignments={"CH-MCPC": 1}),
        MagicMock(order_id="1002", assignments={"MT-LONZ": 1}),
    ]
    variant_gids = {
        "CH-MCPC": "gid://shopify/ProductVariant/111",
        "MT-LONZ": "gid://shopify/ProductVariant/222",
    }

    mock_store = MagicMock()
    mock_store.load.return_value = None

    with (
        patch("matrix_commander.parse_matrix", return_value=(fake_parse_result, None, None)),
        patch("matrix_commander._get_shopify_auth", return_value=("https://shop.myshopify.com", {})),
        patch("matrix_commander._fetch_orders_by_tag", return_value=shopify_orders_raw),
        patch("matrix_commander._lookup_zero_variant_gids", return_value=variant_gids),
        patch("matrix_commander._shopify_graphql", side_effect=_shopify_graphql_side_effect),
        patch("matrix_commander.CheckpointStore", return_value=mock_store),
    ):
        result = cmd_sync("fake.xlsx", "RMFG_TEST", dry_run=False, pass_number=1)

    # save() called once per order + once for stage advance = at least 2 times
    assert mock_store.save.call_count >= 2


# ---------------------------------------------------------------------------
# Test 4: Pass 1 filters PR-CJAM only
# ---------------------------------------------------------------------------


def test_pass1_filters_pr_cjam_only():
    """_active_prefixes(1) returns only ('PR-CJAM',); non-PR-CJAM SKUs are skipped."""
    from matrix_commander import sync_order_to_shopify
    from pipeline.dry_run_guard import DryRunGuard

    prefixes = _active_prefixes(1)
    assert prefixes == ("PR-CJAM",)

    guard = DryRunGuard(dry_run=False)
    order = sample_order("1001", "#1001")
    # Only CH- SKU in matrix — should be skipped by Pass 1
    matrix_skus = {"CH-MCPC": 1, "PR-CJAM-BRIE": 1}
    variant_gids = {
        "CH-MCPC": "gid://shopify/ProductVariant/111",
        "PR-CJAM-BRIE": "gid://shopify/ProductVariant/333",
    }

    graphql_calls: list[str] = []

    def tracking_graphql(base, headers, query, variables):
        graphql_calls.append(query)
        return _shopify_graphql_side_effect(base, headers, query, variables)

    with patch("matrix_commander._shopify_graphql", side_effect=tracking_graphql):
        result = sync_order_to_shopify(
            "https://shop.myshopify.com",
            {},
            order,
            matrix_skus,
            variant_gids,
            mode="smart",
            guard=guard,
            active_prefixes=prefixes,
        )

    # Only PR-CJAM-BRIE added — CH-MCPC filtered out
    assert result.status == "updated"
    assert "PR-CJAM-BRIE" in result.added_skus
    assert "CH-MCPC" not in result.added_skus


# ---------------------------------------------------------------------------
# Test 5: Pass 2 excludes PR-CJAM
# ---------------------------------------------------------------------------


def test_pass2_excludes_pr_cjam():
    """_active_prefixes(2) excludes PR-CJAM; only CH-/MT-/AC-/PK-/TR- are processed."""
    from matrix_commander import sync_order_to_shopify
    from pipeline.dry_run_guard import DryRunGuard

    prefixes = _active_prefixes(2)
    assert "PR-CJAM" not in prefixes
    assert "CH-" in prefixes

    guard = DryRunGuard(dry_run=False)
    order = sample_order("1001", "#1001")
    matrix_skus = {"CH-MCPC": 1, "PR-CJAM-BRIE": 1}
    variant_gids = {
        "CH-MCPC": "gid://shopify/ProductVariant/111",
        "PR-CJAM-BRIE": "gid://shopify/ProductVariant/333",
    }

    with patch("matrix_commander._shopify_graphql", side_effect=_shopify_graphql_side_effect):
        result = sync_order_to_shopify(
            "https://shop.myshopify.com",
            {},
            order,
            matrix_skus,
            variant_gids,
            mode="smart",
            guard=guard,
            active_prefixes=prefixes,
        )

    assert result.status == "updated"
    assert "CH-MCPC" in result.added_skus
    assert "PR-CJAM-BRIE" not in result.added_skus


# ---------------------------------------------------------------------------
# Test 6: retry_failed skips succeeded orders
# ---------------------------------------------------------------------------


def test_retry_failed_skips_succeeded():
    """--retry-failed: orders in progress.succeeded are skipped; only failed orders run."""
    from matrix_commander import cmd_sync

    shopify_orders_raw = [
        {"id": "1001", "name": "#1001", "tags": "", "line_items": []},
        {"id": "1002", "name": "#1002", "tags": "", "line_items": []},
    ]
    fake_parse_result = [
        MagicMock(order_id="1001", assignments={"PR-CJAM-BRIE": 1}),
        MagicMock(order_id="1002", assignments={"PR-CJAM-GOUDA": 1}),
    ]
    variant_gids = {
        "PR-CJAM-BRIE": "gid://shopify/ProductVariant/111",
        "PR-CJAM-GOUDA": "gid://shopify/ProductVariant/222",
    }

    # Order 1001 already succeeded; 1002 failed — only 1002 should be retried
    existing_progress = sample_pass_progress(succeeded=["1001"], failed=["1002"])
    existing_state = _make_state(
        stage=PipelineStage.IDLE,
        pass1=existing_progress,
    )

    mock_store = MagicMock()
    mock_store.load.return_value = existing_state

    graphql_call_order_ids: list[str] = []

    def tracking_graphql(base, headers, query, variables):
        if "orderEditBegin" in query:
            graphql_call_order_ids.append(variables.get("id", ""))
        return _shopify_graphql_side_effect(base, headers, query, variables)

    with (
        patch("matrix_commander.parse_matrix", return_value=(fake_parse_result, None, None)),
        patch("matrix_commander._get_shopify_auth", return_value=("https://shop.myshopify.com", {})),
        patch("matrix_commander._fetch_orders_by_tag", return_value=shopify_orders_raw),
        patch("matrix_commander._lookup_zero_variant_gids", return_value=variant_gids),
        patch("matrix_commander._shopify_graphql", side_effect=tracking_graphql),
        patch("matrix_commander.CheckpointStore", return_value=mock_store),
    ):
        cmd_sync("fake.xlsx", "RMFG_TEST", dry_run=False, pass_number=1, retry_failed=True)

    # Only order 1002 (failed) should have triggered a GraphQL begin call
    assert all("1001" not in oid for oid in graphql_call_order_ids), (
        "Order 1001 (already succeeded) should not have been retried"
    )
    assert any("1002" in oid for oid in graphql_call_order_ids), "Order 1002 (failed) should have been retried"


# ---------------------------------------------------------------------------
# Test 7: Pass gate blocks Pass 2 when not PASS1_COMPLETE
# ---------------------------------------------------------------------------


def test_pass_gate_blocks_pass2():
    """Pass 2 returns False immediately when pipeline stage is not PASS1_COMPLETE."""
    from matrix_commander import cmd_sync

    # State is INVENTORY_SYNCED (stage 1) — not yet PASS1_COMPLETE (stage 2)
    existing_state = _make_state(stage=PipelineStage.INVENTORY_SYNCED)

    mock_store = MagicMock()
    mock_store.load.return_value = existing_state

    graphql_called = False

    def fail_if_called(*args, **kwargs):
        nonlocal graphql_called
        graphql_called = True
        return {}

    with (
        patch("matrix_commander.parse_matrix", return_value=([], None, None)),
        patch("matrix_commander._get_shopify_auth", return_value=("https://shop.myshopify.com", {})),
        patch("matrix_commander._fetch_orders_by_tag", return_value=[]),
        patch("matrix_commander._lookup_zero_variant_gids", return_value={}),
        patch("matrix_commander._shopify_graphql", side_effect=fail_if_called),
        patch("matrix_commander.CheckpointStore", return_value=mock_store),
    ):
        result = cmd_sync("fake.xlsx", "RMFG_TEST", dry_run=False, pass_number=2)

    assert result is False, "cmd_sync should return False when Pass 2 attempted without PASS1_COMPLETE"
    assert not graphql_called, "No GraphQL calls should be made when pass gate blocks"
