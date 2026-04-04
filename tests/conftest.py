"""Shared pytest fixtures for Matrix Commander sync tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline.pipeline_state import PassProgress


@pytest.fixture
def mock_shopify_graphql():
    """Patch matrix_commander._shopify_graphql for all sync tests."""
    with patch("matrix_commander._shopify_graphql") as mock:
        yield mock


def sample_order(order_id: str = "1001", name: str = "#1001") -> dict:
    """Return a minimal Shopify REST order dict."""
    return {
        "id": order_id,
        "name": name,
        "tags": "",
        "line_items": [],
    }


def sample_pass_progress(
    succeeded: list[str] | None = None,
    failed: list[str] | None = None,
    skipped: list[str] | None = None,
    commit_pending: list[str] | None = None,
    errors: dict[str, str] | None = None,
) -> PassProgress:
    """Return a PassProgress with optional pre-filled lists."""
    return PassProgress(
        succeeded=succeeded or [],
        failed=failed or [],
        skipped=skipped or [],
        commit_pending=commit_pending or [],
        errors=errors or {},
    )
