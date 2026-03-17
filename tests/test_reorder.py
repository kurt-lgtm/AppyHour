"""Tests for appyhour.reorder — reorder point and demand calculations."""

import pytest

from appyhour.reorder import (
    apply_churn_rate,
    calculate_reorder_point,
    calculate_total_lead_time,
    compute_reorder_status,
    compute_wheel_supply,
    decompose_bundles,
)


class TestCalculateReorderPoint:
    def test_basic_calculation(self):
        # 10 units/day * 14 days lead + 20 safety = 160
        assert calculate_reorder_point(10, 14, 20) == pytest.approx(160.0)

    def test_zero_safety_stock(self):
        assert calculate_reorder_point(5, 7, 0) == pytest.approx(35.0)

    def test_zero_usage(self):
        assert calculate_reorder_point(0, 14, 20) == pytest.approx(20.0)


class TestCalculateTotalLeadTime:
    def test_all_components(self):
        assert calculate_total_lead_time(3, 5, 2) == 10

    def test_zero_components(self):
        assert calculate_total_lead_time(0, 0, 0) == 0


class TestDecomposeBundles:
    def test_known_bundle(self):
        bundle_map = {"AHB-MED": [("CH-MCPC", 1), ("MT-LONZ", 1), ("AC-DTCH", 1)]}
        result = decompose_bundles("AHB-MED", 2, bundle_map)
        assert result == [("CH-MCPC", 2), ("MT-LONZ", 2), ("AC-DTCH", 2)]

    def test_unknown_sku_returns_itself(self):
        result = decompose_bundles("CH-MCPC", 5, {})
        assert result == [("CH-MCPC", 5)]

    def test_single_quantity(self):
        bundle_map = {"BUNDLE-A": [("COMP-1", 3), ("COMP-2", 1)]}
        result = decompose_bundles("BUNDLE-A", 1, bundle_map)
        assert result == [("COMP-1", 3), ("COMP-2", 1)]


class TestApplyChurnRate:
    def test_five_percent_churn(self):
        assert apply_churn_rate(100, 5) == pytest.approx(95.0)

    def test_zero_churn(self):
        assert apply_churn_rate(100, 0) == pytest.approx(100.0)

    def test_hundred_percent_churn(self):
        assert apply_churn_rate(100, 100) == pytest.approx(0.0)


class TestComputeWheelSupply:
    def test_standard_calculation(self):
        # 5 lbs * 10 wheels * 2.67 = 133.5 slices
        assert compute_wheel_supply(5, 10) == pytest.approx(133.5)

    def test_custom_factor(self):
        assert compute_wheel_supply(5, 10, slice_factor=3.0) == pytest.approx(150.0)

    def test_zero_count(self):
        assert compute_wheel_supply(5, 0) == pytest.approx(0.0)


class TestComputeReorderStatus:
    def test_out_of_stock(self):
        assert compute_reorder_status(0, 100, 10) == "OUT_OF_STOCK"

    def test_critical(self):
        # on_hand=40 <= 100*0.5=50 -> CRITICAL
        assert compute_reorder_status(40, 100, 10) == "CRITICAL"

    def test_reorder(self):
        # on_hand=80 <= 100 but > 50 -> REORDER
        assert compute_reorder_status(80, 100, 10) == "REORDER"

    def test_ok(self):
        # on_hand=150 > 100 but <= 300 -> OK
        assert compute_reorder_status(150, 100, 10) == "OK"

    def test_overstock(self):
        # on_hand=400 > 100*3=300 -> OVERSTOCK
        assert compute_reorder_status(400, 100, 10) == "OVERSTOCK"

    def test_no_demand_no_stock(self):
        # on_hand=0 but daily_usage=0 -> not OUT_OF_STOCK
        assert compute_reorder_status(0, 0, 0) == "OK"
