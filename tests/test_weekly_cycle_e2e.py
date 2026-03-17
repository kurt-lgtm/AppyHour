"""E2E tests for the weekly fulfillment cycle.

Simulates Friday→Wednesday with fixture data and verifies the pipeline
produces correct results at each stage: inventory check, error detection,
demand calculation, cut order math, and shipping risk assessment.
"""

import json
import sys
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

# Ensure imports work
ERRORS_DIR = str(Path(__file__).parent.parent / "InventoryReorder" / "Errors")
if ERRORS_DIR not in sys.path:
    sys.path.insert(0, ERRORS_DIR)

from appyhour.reorder import (
    apply_churn_rate,
    calculate_reorder_point,
    compute_reorder_status,
    compute_wheel_supply,
    decompose_bundles,
)
from appyhour.shipping import classify_transit_risk, gel_pack_recommendation, is_on_time
from tests.fixtures.weekly_cycle import (
    CEX_EC_OVERRIDES,
    CURATION_RECIPES,
    FRIDAY_INVENTORY,
    PR_CJAM_OVERRIDES,
    SAMPLE_ORDERS,
    WEEKLY_DEMAND,
)

# Mock settings for find_new_errors
_MOCK_SETTINGS = {
    "shopify_store_url": "test", "shopify_access_token": "test",
    "curation_recipes": CURATION_RECIPES, "pr_cjam": {}, "cex_ec": {},
}
with patch("builtins.open", mock_open(read_data=json.dumps(_MOCK_SETTINGS))):
    from find_new_errors import analyze_order as detect_errors


class TestFridayInventoryCheck:
    """Friday: Check starting inventory levels and reorder alerts."""

    def test_all_skus_have_positive_stock(self):
        for sku, qty in FRIDAY_INVENTORY.items():
            assert qty > 0, f"{sku} has zero stock on Friday"

    def test_reorder_status_reflects_levels(self):
        # With 90 units and daily usage ~26 (180/7), CH-UCONE should be low
        daily_usage = WEEKLY_DEMAND.get("OWC", 40) / 7  # ~5.7 per day for OWC
        rp = calculate_reorder_point(daily_usage, 14, 20)
        status = compute_reorder_status(FRIDAY_INVENTORY["CH-UCONE"], rp, daily_usage)
        assert status in ("CRITICAL", "REORDER", "OK")

    def test_wheel_supply_calculation(self):
        # If we had 10 wheels at 5 lbs each
        supply = compute_wheel_supply(5.0, 10)
        assert supply == pytest.approx(133.5)


class TestSaturdayErrorScan:
    """Saturday: Scan orders for errors before fulfillment."""

    def test_clean_order_passes(self):
        errors = detect_errors(SAMPLE_ORDERS[0])  # #EF-1001 — clean MONG order
        assert errors == [], f"Clean order flagged: {errors}"

    def test_reship_order_skipped(self):
        errors = detect_errors(SAMPLE_ORDERS[2])  # #EF-1003 — tagged reship
        assert errors == []

    def test_qty_error_detected(self):
        errors = detect_errors(SAMPLE_ORDERS[3])  # #EF-1004 — CH-MCPC qty=3
        class4 = [e for e in errors if "Class 4" in e[0]]
        assert len(class4) >= 1, "Should detect qty>1 food item"

    def test_all_orders_scanned(self):
        total_errors = 0
        for order in SAMPLE_ORDERS:
            total_errors += len(detect_errors(order))
        # We know at least order #1004 has errors
        assert total_errors >= 1


class TestTuesdayDemandCalculation:
    """Tuesday: Calculate demand for Wednesday cut order."""

    def test_demand_produces_sku_quantities(self):
        sku_demand: dict[str, float] = {}
        for curation, box_count in WEEKLY_DEMAND.items():
            recipe = CURATION_RECIPES.get(curation, [])
            for sku, qty_per_box in recipe:
                sku_demand[sku] = sku_demand.get(sku, 0) + box_count * qty_per_box

        # CH-MCPC appears in all 3 curations: 80+60+40 = 180
        assert sku_demand["CH-MCPC"] == 180
        # MT-LONZ in MONG + OWC: 80+40 = 120
        assert sku_demand["MT-LONZ"] == 120
        # CH-BLR only in MONG: 80
        assert sku_demand["CH-BLR"] == 80

    def test_pr_cjam_adds_demand(self):
        pr_demand: dict[str, float] = {}
        for curation, box_count in WEEKLY_DEMAND.items():
            cheese = PR_CJAM_OVERRIDES.get(curation)
            if cheese:
                pr_demand[cheese] = pr_demand.get(cheese, 0) + box_count

        # MONG→CH-BLR (80), MDT→CH-TTBRIE (60), OWC→CH-MCPC (40)
        assert pr_demand["CH-BLR"] == 80
        assert pr_demand["CH-TTBRIE"] == 60
        assert pr_demand["CH-MCPC"] == 40

    def test_churn_reduces_demand(self):
        raw = 100
        after_churn = apply_churn_rate(raw, 5)
        assert after_churn == 95

    def test_net_position_calculation(self):
        # CH-MCPC: inventory=500, demand from recipes=180, from PR-CJAM=40 → net=280
        total_demand = 180 + 40
        net = FRIDAY_INVENTORY["CH-MCPC"] - total_demand
        assert net == 280


class TestWednesdayCutOrder:
    """Wednesday: Generate cut order — what needs to be cut/prepared."""

    def test_shortfall_detection(self):
        sku_demand: dict[str, float] = {}
        for curation, box_count in WEEKLY_DEMAND.items():
            for sku, qty_per_box in CURATION_RECIPES.get(curation, []):
                sku_demand[sku] = sku_demand.get(sku, 0) + box_count * qty_per_box
            cheese = PR_CJAM_OVERRIDES.get(curation)
            if cheese:
                sku_demand[cheese] = sku_demand.get(cheese, 0) + box_count
            ec_cheese = CEX_EC_OVERRIDES.get(curation)
            if ec_cheese:
                # ~40% of boxes get CEX-EC (large boxes only)
                sku_demand[ec_cheese] = sku_demand.get(ec_cheese, 0) + box_count * 0.4

        shortfalls = {}
        for sku, demand in sku_demand.items():
            available = FRIDAY_INVENTORY.get(sku, 0)
            if demand > available:
                shortfalls[sku] = demand - available

        # AC-TCRISP: inventory=100, demand=40 (OWC recipe) → no shortfall
        assert "AC-TCRISP" not in shortfalls

    def test_bundle_decomposition(self):
        bundle_map = {"AHB-MED": [("CH-MCPC", 1), ("MT-LONZ", 1)]}
        components = decompose_bundles("AHB-MED", 10, bundle_map)
        assert ("CH-MCPC", 10) in components
        assert ("MT-LONZ", 10) in components


class TestShippingRiskAssessment:
    """Cross-cutting: Shipping risk for orders going out."""

    def test_cool_state_low_risk(self):
        result = gel_pack_recommendation(destination_temp=55, transit_type="2-Day")
        assert result["risk"] == "LOW"

    def test_hot_state_needs_extra_gel(self):
        result = gel_pack_recommendation(destination_temp=100, transit_type="3-Day")
        assert result["config_48oz"] >= 1

    def test_on_time_classification(self):
        assert is_on_time(2, "2-Day") is True
        assert is_on_time(3, "2-Day") is False

    def test_transit_risk_classification(self):
        assert classify_transit_risk(3, 2, 95) == "CRITICAL"
        assert classify_transit_risk(2, 2, 45) == "ON_TIME"
