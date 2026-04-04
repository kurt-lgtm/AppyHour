"""Tests for cmd_sync sequential loop, pass filtering, retry-failed, and pass gate.

Plan: 02-02 — Shopify Sync Battle-Test
"""

from __future__ import annotations


def test_sequential_loop_calls_limiter():
    """LeakyBucketLimiter.wait() is called before each GraphQL mutation."""
    raise NotImplementedError("implement in Task 3")


def test_dry_run_guard_blocks_mutation():
    """DryRunGuard.assert_can_mutate() raises DryRunViolationError in dry-run mode."""
    raise NotImplementedError("implement in Task 3")


def test_checkpoint_saved_after_each_order():
    """CheckpointStore.save() is called after every order regardless of outcome."""
    raise NotImplementedError("implement in Task 3")


def test_pass1_filters_pr_cjam_only():
    """Pass 1 processes only PR-CJAM prefixed SKUs; non-PR-CJAM SKUs are skipped."""
    raise NotImplementedError("implement in Task 3")


def test_pass2_excludes_pr_cjam():
    """Pass 2 processes CH-/MT-/AC-/PK-/TR- prefixes but not PR-CJAM."""
    raise NotImplementedError("implement in Task 3")


def test_retry_failed_skips_succeeded():
    """--retry-failed flag skips orders that already succeeded in the checkpoint."""
    raise NotImplementedError("implement in Task 3")


def test_pass_gate_blocks_pass2():
    """Pass 2 is blocked when pipeline stage is not PASS1_COMPLETE."""
    raise NotImplementedError("implement in Task 3")
