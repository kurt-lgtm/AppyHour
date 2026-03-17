"""Tests for appyhour.thermal — gel pack sizing and heat analysis."""

import pytest

from appyhour.thermal import (
    analyze_order,
    calc_heat_gain,
    calc_r_total,
    calc_surface_area,
    recommend_config,
)


class TestCalcSurfaceArea:
    def test_standard_box(self):
        # 13x10x10 inches -> 5.0 ft2
        result = calc_surface_area(13, 10, 10)
        assert abs(result - 5.0) < 0.01

    def test_zero_dimension(self):
        assert calc_surface_area(0, 10, 10) == pytest.approx(2 * 10 * 10 / 144.0)

    def test_cube(self):
        # 12x12x12 = 864 in2 = 6.0 ft2
        assert calc_surface_area(12, 12, 12) == pytest.approx(6.0)


class TestCalcRTotal:
    def test_basic_calculation(self):
        # R_per_inch=3.5, thickness=1.5", R_air=0.365 -> 5.615
        result = calc_r_total(3.5, 1.5, 0.365)
        assert result == pytest.approx(5.615)

    def test_no_air_film(self):
        assert calc_r_total(4.0, 1.0, 0.0) == pytest.approx(4.0)


class TestCalcHeatGain:
    def test_no_heat_gain_below_target(self):
        # Outside temp 40F < target 50F -> no heat gain
        assert calc_heat_gain(40, 24, 5.0, 6.0) == 0.0

    def test_basic_heat_gain(self):
        # 80F outside, 50F target -> deltaT=30, 24hrs, 5ft2, R=6
        # Q = (30 * 5 * 24) / 6 = 600 BTU
        assert calc_heat_gain(80, 24, 5.0, 6.0) == pytest.approx(600.0)

    def test_custom_target_temp(self):
        # 80F outside, 40F target -> deltaT=40
        # Q = (40 * 5 * 24) / 6 = 800 BTU
        assert calc_heat_gain(80, 24, 5.0, 6.0, target_temp=40) == pytest.approx(800.0)

    def test_zero_hours(self):
        assert calc_heat_gain(100, 0, 5.0, 6.0) == 0.0


class TestRecommendConfig:
    def test_baseline_sufficient(self):
        # 489 * 0.9 = 440.1 BTU effective
        config = recommend_config(400)
        assert config["name"] == "1x 48oz (baseline)"

    def test_needs_extra_24oz(self):
        # Need > 440.1 BTU but <= 660.15
        config = recommend_config(500)
        assert config["name"] == "1x 48oz + 1x 24oz"

    def test_needs_double_48oz(self):
        # Need > 660.15 but <= 880.2
        config = recommend_config(700)
        assert config["name"] == "2x 48oz"

    def test_max_config(self):
        config = recommend_config(1000)
        assert config["name"] == "2x 48oz + 1x 24oz"

    def test_exceeded(self):
        config = recommend_config(2000)
        assert config["exceeded"] is True
        assert config["name"] == "2x 48oz + 1x 24oz"

    def test_zero_heat(self):
        config = recommend_config(0)
        assert config["name"] == "1x 48oz (baseline)"


class TestAnalyzeOrder:
    # Standard box: 13x10x10 -> 5.0 ft2, R=6.115
    SA = calc_surface_area(13, 10, 10)
    R = 6.115

    def test_low_risk_cool_weather(self):
        result = analyze_order(
            outside_temp=55, transit_type="2-Day",
            hub_hours_1day=4, hub_hours_2day=6, hub_hours_3day=8,
            hub_temp=70, surface_area=self.SA, r_total=self.R,
        )
        assert result["risk"] == "LOW"
        assert result["total_cycle"] == 48.0
        assert result["hub_hours"] == 6

    def test_3day_transit(self):
        result = analyze_order(
            outside_temp=90, transit_type="3-Day",
            hub_hours_1day=4, hub_hours_2day=6, hub_hours_3day=8,
            hub_temp=75, surface_area=self.SA, r_total=self.R,
        )
        assert result["total_cycle"] == 72.0
        assert result["actual_transit"] == 64.0

    def test_safety_factor_increases_heat(self):
        base = analyze_order(
            outside_temp=85, transit_type="2-Day",
            hub_hours_1day=4, hub_hours_2day=6, hub_hours_3day=8,
            hub_temp=70, surface_area=self.SA, r_total=self.R,
        )
        with_safety = analyze_order(
            outside_temp=85, transit_type="2-Day",
            hub_hours_1day=4, hub_hours_2day=6, hub_hours_3day=8,
            hub_temp=70, surface_area=self.SA, r_total=self.R,
            safety_factor_pct=20,
        )
        assert with_safety["total_q_safe"] > base["total_q_safe"]

    def test_split_transit_with_origin_temp(self):
        result = analyze_order(
            outside_temp=95, transit_type="2-Day",
            hub_hours_1day=4, hub_hours_2day=6, hub_hours_3day=8,
            hub_temp=70, surface_area=self.SA, r_total=self.R,
            origin_temp=75,
        )
        # Origin is cooler, so total heat should be less than if all at 95
        all_dest = analyze_order(
            outside_temp=95, transit_type="2-Day",
            hub_hours_1day=4, hub_hours_2day=6, hub_hours_3day=8,
            hub_temp=70, surface_area=self.SA, r_total=self.R,
        )
        assert result["q_transit"] < all_dest["q_transit"]

    def test_result_contains_all_keys(self):
        result = analyze_order(
            outside_temp=80, transit_type="1-Day",
            hub_hours_1day=4, hub_hours_2day=6, hub_hours_3day=8,
            hub_temp=70, surface_area=self.SA, r_total=self.R,
        )
        expected_keys = {
            "total_cycle", "hub_hours", "actual_transit",
            "q_transit", "q_hub", "total_q", "total_q_safe",
            "config_name", "config_btu", "config_48oz", "config_24oz",
            "config_tags", "cap_pct", "temp_rise", "final_temp",
            "risk", "exceeded",
        }
        assert set(result.keys()) == expected_keys
