"""Tests for appyhour.shipping — unified shipping analytics."""

from appyhour.shipping import classify_transit_risk, gel_pack_recommendation, is_on_time


class TestGelPackRecommendation:
    def test_cool_weather_low_risk(self):
        result = gel_pack_recommendation(destination_temp=55, transit_type="2-Day")
        assert result["risk"] == "LOW"

    def test_hot_weather_higher_risk(self):
        result = gel_pack_recommendation(destination_temp=100, transit_type="3-Day")
        assert result["risk"] in ("HIGH", "CRITICAL")

    def test_origin_temp_reduces_heat(self):
        no_origin = gel_pack_recommendation(destination_temp=95)
        with_origin = gel_pack_recommendation(destination_temp=95, origin_temp=70)
        assert with_origin["q_transit"] < no_origin["q_transit"]

    def test_safety_factor_increases_safe_q(self):
        base = gel_pack_recommendation(destination_temp=85, safety_factor_pct=0)
        safe = gel_pack_recommendation(destination_temp=85, safety_factor_pct=20)
        assert safe["total_q_safe"] > base["total_q_safe"]

    def test_returns_config_tags(self):
        result = gel_pack_recommendation(destination_temp=55)
        assert "config_tags" in result
        assert isinstance(result["config_tags"], list)


class TestClassifyTransitRisk:
    def test_on_time_cool(self):
        assert classify_transit_risk(2, 2, 45) == "ON_TIME"

    def test_delayed_cool(self):
        assert classify_transit_risk(3, 2, 45) == "DELAYED"

    def test_on_time_hot(self):
        assert classify_transit_risk(2, 2, 85) == "TEMP_RISK"

    def test_delayed_hot_is_critical(self):
        assert classify_transit_risk(4, 2, 90) == "CRITICAL"

    def test_custom_threshold(self):
        # 45F is fine at default 50F threshold but not at 40F
        assert classify_transit_risk(2, 2, 45, threshold_temp=40) == "TEMP_RISK"


class TestIsOnTime:
    def test_within_service_level(self):
        assert is_on_time(2, "2-Day") is True

    def test_exceeds_service_level(self):
        assert is_on_time(3, "2-Day") is False

    def test_exact_match(self):
        assert is_on_time(1, "1-Day") is True

    def test_temp_override_marks_late(self):
        # Arrived on time but too hot
        assert is_on_time(2, "2-Day", temp=85) is False

    def test_temp_within_threshold(self):
        assert is_on_time(2, "2-Day", temp=45) is True

    def test_unknown_service_defaults_3day(self):
        assert is_on_time(3, "Unknown") is True
        assert is_on_time(4, "Unknown") is False
