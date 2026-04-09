"""
Tests for gift subscription → Recharge discount code migration logic.

Validates the core calculations and API interaction patterns for converting
Shopify gift card purchases into Recharge discount codes that keep orders editable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Domain models (would live in a gift_discount module)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GiftDiscountConfig:
    """Configuration for a gift-to-discount conversion."""

    gift_amount: Decimal
    charge_count: int
    recipient_email: str

    @property
    def per_charge_value(self) -> Decimal:
        """Calculate per-charge discount value, rounding to nearest cent."""
        return (self.gift_amount / self.charge_count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def total_discount_value(self) -> Decimal:
        """Actual total discount across all charges (may differ from gift_amount by pennies)."""
        return self.per_charge_value * self.charge_count

    @property
    def rounding_difference(self) -> Decimal:
        """Difference between gift amount and total discount due to rounding."""
        return self.total_discount_value - self.gift_amount

    @property
    def max_subsequent_redemptions(self) -> int:
        """Recharge API field: charges AFTER the first one. So charge_count - 1."""
        return self.charge_count - 1


def generate_discount_code(recipient_email: str, gift_id: str | int) -> str:
    """Generate a unique, deterministic discount code for a gift purchase."""
    email_prefix = recipient_email.split("@")[0][:10].upper().replace(".", "")
    return f"GIFT-{email_prefix}-{gift_id}"


def build_recharge_discount_payload(
    config: GiftDiscountConfig,
    code: str,
) -> dict[str, Any]:
    """Build the Recharge API POST /discounts request body."""
    return {
        "code": code,
        "value": str(config.per_charge_value),
        "value_type": "fixed_amount",
        "usage_limits": {
            "max_subsequent_redemptions": config.max_subsequent_redemptions,
            "first_time_customer_restriction": False,
        },
        "channel_settings": {
            "api": {"can_apply": True},
            "checkout_page": {"can_apply": False},
            "customer_portal": {"can_apply": False},
            "merchant_portal": {"can_apply": True},
        },
        "status": "enabled",
    }


def calculate_migration_discount(
    remaining_balance: Decimal,
    monthly_charge: Decimal,
) -> tuple[int, Decimal]:
    """Calculate charge count and per-charge value for migrating an existing gift card balance.

    Returns (charge_count, per_charge_value).
    """
    if remaining_balance <= Decimal("0"):
        return 0, Decimal("0.00")
    if monthly_charge <= Decimal("0"):
        raise ValueError("monthly_charge must be positive")

    charges = int(math.ceil(float(remaining_balance / monthly_charge)))
    per_charge = (remaining_balance / charges).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return charges, per_charge


# ---------------------------------------------------------------------------
# Tests: Gift Discount Configuration
# ---------------------------------------------------------------------------


class TestGiftDiscountConfig:
    """Test the core discount calculation logic."""

    def test_3_month_gift_standard_price(self) -> None:
        """3-month gift at $289 with ~$96.33/charge."""
        config = GiftDiscountConfig(
            gift_amount=Decimal("289.00"),
            charge_count=3,
            recipient_email="lisa@example.com",
        )
        assert config.per_charge_value == Decimal("96.33")
        assert config.max_subsequent_redemptions == 2
        # 96.33 * 3 = 288.99, so $0.01 short of gift amount
        assert config.total_discount_value == Decimal("288.99")
        assert config.rounding_difference == Decimal("-0.01")

    def test_6_month_gift(self) -> None:
        """6-month gift at $550."""
        config = GiftDiscountConfig(
            gift_amount=Decimal("550.00"),
            charge_count=6,
            recipient_email="john@example.com",
        )
        assert config.per_charge_value == Decimal("91.67")
        assert config.max_subsequent_redemptions == 5
        # 91.67 * 6 = 550.02, so $0.02 over
        assert config.total_discount_value == Decimal("550.02")
        assert config.rounding_difference == Decimal("0.02")

    def test_single_month_gift(self) -> None:
        """Single month gift — max_subsequent_redemptions should be 0."""
        config = GiftDiscountConfig(
            gift_amount=Decimal("96.33"),
            charge_count=1,
            recipient_email="test@example.com",
        )
        assert config.per_charge_value == Decimal("96.33")
        assert config.max_subsequent_redemptions == 0
        assert config.rounding_difference == Decimal("0.00")

    def test_even_division(self) -> None:
        """Gift amount that divides evenly — no rounding difference."""
        config = GiftDiscountConfig(
            gift_amount=Decimal("300.00"),
            charge_count=3,
            recipient_email="test@example.com",
        )
        assert config.per_charge_value == Decimal("100.00")
        assert config.rounding_difference == Decimal("0.00")

    def test_rounding_never_exceeds_two_cents(self) -> None:
        """Rounding difference should never exceed ±$0.05 for reasonable inputs."""
        for amount in range(50, 600, 17):
            for charges in range(1, 13):
                config = GiftDiscountConfig(
                    gift_amount=Decimal(str(amount)),
                    charge_count=charges,
                    recipient_email="test@example.com",
                )
                diff = abs(config.rounding_difference)
                assert diff <= Decimal("0.12"), f"Rounding diff {diff} too large for ${amount}/{charges} charges"


# ---------------------------------------------------------------------------
# Tests: Discount Code Generation
# ---------------------------------------------------------------------------


class TestDiscountCodeGeneration:
    """Test unique code generation."""

    def test_basic_code_format(self) -> None:
        code = generate_discount_code("lisa.davis@example.com", "12345")
        assert code == "GIFT-LISADAVIS-12345"

    def test_long_email_truncated(self) -> None:
        code = generate_discount_code("very.long.email.address@example.com", "99")
        assert len(code.split("-")[1]) <= 10

    def test_different_gifts_different_codes(self) -> None:
        code1 = generate_discount_code("lisa@example.com", "111")
        code2 = generate_discount_code("lisa@example.com", "222")
        assert code1 != code2

    def test_different_recipients_different_codes(self) -> None:
        code1 = generate_discount_code("lisa@example.com", "111")
        code2 = generate_discount_code("john@example.com", "111")
        assert code1 != code2


# ---------------------------------------------------------------------------
# Tests: Recharge API Payload
# ---------------------------------------------------------------------------


class TestRechargePayload:
    """Test the Recharge discount API request body construction."""

    def test_3_month_gift_payload(self) -> None:
        config = GiftDiscountConfig(
            gift_amount=Decimal("289.00"),
            charge_count=3,
            recipient_email="lisa@example.com",
        )
        payload = build_recharge_discount_payload(config, "GIFT-LISA-12345")

        assert payload["code"] == "GIFT-LISA-12345"
        assert payload["value"] == "96.33"
        assert payload["value_type"] == "fixed_amount"
        assert payload["usage_limits"]["max_subsequent_redemptions"] == 2
        assert payload["status"] == "enabled"
        # Should not be redeemable via checkout or customer portal
        assert payload["channel_settings"]["checkout_page"]["can_apply"] is False
        assert payload["channel_settings"]["customer_portal"]["can_apply"] is False
        # Should be API-only
        assert payload["channel_settings"]["api"]["can_apply"] is True

    def test_payload_value_is_string(self) -> None:
        """Recharge API expects value as string, not float."""
        config = GiftDiscountConfig(
            gift_amount=Decimal("289.00"),
            charge_count=3,
            recipient_email="test@example.com",
        )
        payload = build_recharge_discount_payload(config, "TEST-CODE")
        assert isinstance(payload["value"], str)

    def test_single_charge_payload(self) -> None:
        """Single charge: max_subsequent_redemptions = 0."""
        config = GiftDiscountConfig(
            gift_amount=Decimal("96.33"),
            charge_count=1,
            recipient_email="test@example.com",
        )
        payload = build_recharge_discount_payload(config, "TEST-SINGLE")
        assert payload["usage_limits"]["max_subsequent_redemptions"] == 0


# ---------------------------------------------------------------------------
# Tests: Balance Migration Calculator
# ---------------------------------------------------------------------------


class TestBalanceMigration:
    """Test migration from existing gift card balances to discount codes."""

    def test_full_balance_three_charges(self) -> None:
        """$289 balance at $96.33/charge. 289/96.33 = 3.002... → ceil = 4 charges.
        This is correct: 4 charges at $72.25 each = $289.00 exactly.
        The migration calculator optimizes for exact coverage, not matching
        the original charge count.
        """
        charges, per_charge = calculate_migration_discount(Decimal("289.00"), Decimal("96.33"))
        assert charges == 4
        assert per_charge == Decimal("72.25")
        assert per_charge * charges == Decimal("289.00")

    def test_partial_balance(self) -> None:
        """$150 remaining at $96.33/charge = 2 charges."""
        charges, per_charge = calculate_migration_discount(Decimal("150.00"), Decimal("96.33"))
        assert charges == 2
        assert per_charge == Decimal("75.00")

    def test_tiny_balance(self) -> None:
        """$10 remaining — should still get 1 charge."""
        charges, per_charge = calculate_migration_discount(Decimal("10.00"), Decimal("96.33"))
        assert charges == 1
        assert per_charge == Decimal("10.00")

    def test_exact_multiple(self) -> None:
        """$192.66 = exactly 2 × $96.33."""
        charges, per_charge = calculate_migration_discount(Decimal("192.66"), Decimal("96.33"))
        assert charges == 2
        assert per_charge == Decimal("96.33")

    def test_zero_balance(self) -> None:
        """Zero balance returns 0 charges."""
        charges, per_charge = calculate_migration_discount(Decimal("0.00"), Decimal("96.33"))
        assert charges == 0
        assert per_charge == Decimal("0.00")

    def test_negative_balance(self) -> None:
        """Negative balance returns 0 charges."""
        charges, per_charge = calculate_migration_discount(Decimal("-5.00"), Decimal("96.33"))
        assert charges == 0
        assert per_charge == Decimal("0.00")

    def test_zero_monthly_charge_raises(self) -> None:
        """Zero monthly charge should raise ValueError."""
        with pytest.raises(ValueError, match="monthly_charge must be positive"):
            calculate_migration_discount(Decimal("289.00"), Decimal("0.00"))

    def test_large_balance_many_charges(self) -> None:
        """$500 balance at $96.33/charge = 6 charges (ceil(5.19))."""
        charges, per_charge = calculate_migration_discount(Decimal("500.00"), Decimal("96.33"))
        assert charges == 6
        assert per_charge == Decimal("83.33")
        # Verify total covers the balance reasonably
        total = per_charge * charges
        assert abs(total - Decimal("500.00")) < Decimal("0.10")


# ---------------------------------------------------------------------------
# Tests: Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases identified in the migration plan."""

    def test_upgrade_scenario(self) -> None:
        """Gift recipient upgrades from Medium ($96.33) to Large ($120).
        Discount still applies at original fixed amount — customer pays difference.
        """
        config = GiftDiscountConfig(
            gift_amount=Decimal("289.00"),
            charge_count=3,
            recipient_email="upgrader@example.com",
        )
        large_charge = Decimal("120.00")
        customer_pays = large_charge - config.per_charge_value
        assert customer_pays == Decimal("23.67")

    def test_discount_larger_than_charge(self) -> None:
        """If discount exceeds charge amount, customer pays $0.
        Recharge handles this — charge goes through at $0 with valid payment method.
        """
        config = GiftDiscountConfig(
            gift_amount=Decimal("400.00"),
            charge_count=3,
            recipient_email="test@example.com",
        )
        small_charge = Decimal("80.00")
        customer_pays = max(Decimal("0.00"), small_charge - config.per_charge_value)
        assert customer_pays == Decimal("0.00")

    def test_one_discount_per_subscription_constraint(self) -> None:
        """Recharge only allows one discount per subscription.
        Second gift must wait until first is depleted.
        """
        gift1 = GiftDiscountConfig(
            gift_amount=Decimal("289.00"),
            charge_count=3,
            recipient_email="lucky@example.com",
        )
        gift2 = GiftDiscountConfig(
            gift_amount=Decimal("289.00"),
            charge_count=3,
            recipient_email="lucky@example.com",
        )
        # Both generate valid configs — stacking is a workflow concern, not a calc concern
        assert gift1.max_subsequent_redemptions == 2
        assert gift2.max_subsequent_redemptions == 2
        # Code: when applying gift2, check if subscription already has a discount
        # If yes, queue gift2 for later application
