"""Tests for DryRunGuard mutation enforcement layer.

Proves: mutations blocked in dry-run, allowed in execute mode, default is dry_run=True.
"""

from __future__ import annotations

import pytest

from pipeline.dry_run_guard import DryRunGuard, DryRunViolationError


class TestDryRunGuardDirectUsage:
    def test_assert_can_mutate_raises_when_dry_run_true(self):
        guard = DryRunGuard(dry_run=True)
        with pytest.raises(DryRunViolationError):
            guard.assert_can_mutate()

    def test_assert_can_mutate_does_not_raise_when_dry_run_false(self):
        guard = DryRunGuard(dry_run=False)
        guard.assert_can_mutate()  # must not raise

    def test_default_is_dry_run_true(self):
        guard = DryRunGuard()
        with pytest.raises(DryRunViolationError):
            guard.assert_can_mutate()

    def test_error_message_contains_dry_run(self):
        guard = DryRunGuard(dry_run=True)
        with pytest.raises(DryRunViolationError, match="(?i)dry-run"):
            guard.assert_can_mutate()

    def test_error_message_explains_no_mutations(self):
        guard = DryRunGuard(dry_run=True)
        with pytest.raises(DryRunViolationError, match="mutation"):
            guard.assert_can_mutate()


class TestDryRunGuardContextManager:
    def test_context_manager_dry_run_true_blocks_mutation(self):
        with DryRunGuard(dry_run=True) as guard:
            with pytest.raises(DryRunViolationError):
                guard.assert_can_mutate()

    def test_context_manager_dry_run_false_allows_mutation(self):
        with DryRunGuard(dry_run=False) as guard:
            guard.assert_can_mutate()  # must not raise

    def test_context_manager_returns_guard_instance(self):
        with DryRunGuard(dry_run=True) as guard:
            assert isinstance(guard, DryRunGuard)

    def test_context_manager_exit_is_noop(self):
        """Exiting context manager should not raise."""
        with DryRunGuard(dry_run=True):
            pass  # no assertion, just verify no exception on exit
