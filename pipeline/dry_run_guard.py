"""Dry-run enforcement guard.

React equivalent: isDryRun flag checked before any Shopify mutation call.

Exports:
    DryRunGuard         — context manager that blocks mutations in dry-run mode
    DryRunViolationError — raised when mutation attempted while dry_run=True
"""

from __future__ import annotations


class DryRunViolationError(Exception):
    """Raised when a Shopify mutation is attempted while dry_run=True."""


class DryRunGuard:
    """Context manager that blocks Shopify mutations in dry-run mode.

    React equivalent: feature flag check before any POST/PUT to Shopify API.

    Usage:
        with DryRunGuard(dry_run=True):
            # safe: read-only calls allowed
            orders = fetch_orders(...)
            # raises DryRunViolationError:
            guard.assert_can_mutate()  # would mutate Shopify

        # To allow mutations:
        with DryRunGuard(dry_run=False) as guard:
            guard.assert_can_mutate()  # allowed
    """

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    def assert_can_mutate(self) -> None:
        """Call before any Shopify mutation.

        Raises DryRunViolationError if dry_run=True — no Shopify mutation
        will be made until dry_run=False (or --execute flag) is explicitly set.
        """
        if self.dry_run:
            raise DryRunViolationError(
                "Dry-run mode is active — no Shopify mutations will be made. "
                "Pass dry_run=False (or --execute flag) to execute."
            )

    def __enter__(self) -> DryRunGuard:
        return self

    def __exit__(self, *args: object) -> None:
        pass
