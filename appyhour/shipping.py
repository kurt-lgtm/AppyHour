"""Unified shipping analytics — consolidates thermal risk assessment
with operational transit metrics.

Bridges GelPackCalculator thermal logic with ShippingReports transit data.
Import this module from any context (MCP server, web app, scripts).
"""

from __future__ import annotations

from appyhour.thermal import analyze_order as thermal_analyze
from appyhour.thermal import calc_r_total, calc_surface_area

# Default box dimensions (13x10x10" Elevate Foods box)
DEFAULT_BOX = {"l": 13, "w": 10, "h": 10}
# Default insulation (1.5" EPS foam)
DEFAULT_INSULATION = {"r_per_inch": 3.5, "thickness": 1.5, "r_air_film": 0.365}
# Default hub hours by transit type
DEFAULT_HUB_HOURS = {"1-Day": 4, "2-Day": 6, "3-Day": 8}
DEFAULT_HUB_TEMP = 70  # F, warehouse ambient


def gel_pack_recommendation(
    destination_temp: float,
    transit_type: str = "2-Day",
    origin_temp: float | None = None,
    safety_factor_pct: float = 10,
    box: dict | None = None,
    insulation: dict | None = None,
    hub_hours: dict | None = None,
    hub_temp: float = DEFAULT_HUB_TEMP,
) -> dict:
    """High-level gel pack recommendation for a shipment.

    Wraps thermal.analyze_order with sensible defaults for Elevate Foods.
    Returns the full analysis dict including risk level, config, and temps.
    """
    box = box or DEFAULT_BOX
    insulation = insulation or DEFAULT_INSULATION
    hub_hours = hub_hours or DEFAULT_HUB_HOURS

    sa = calc_surface_area(box["l"], box["w"], box["h"])
    r = calc_r_total(insulation["r_per_inch"], insulation["thickness"], insulation["r_air_film"])

    return thermal_analyze(
        outside_temp=destination_temp,
        transit_type=transit_type,
        hub_hours_1day=hub_hours.get("1-Day", 4),
        hub_hours_2day=hub_hours.get("2-Day", 6),
        hub_hours_3day=hub_hours.get("3-Day", 8),
        hub_temp=hub_temp,
        surface_area=sa,
        r_total=r,
        safety_factor_pct=safety_factor_pct,
        origin_temp=origin_temp,
    )


def classify_transit_risk(
    actual_days: int, expected_days: int, destination_temp: float, threshold_temp: float = 50.0
) -> str:
    """Classify shipping risk based on transit time and temperature.

    Returns: ON_TIME, DELAYED, TEMP_RISK, or CRITICAL (delayed + hot).
    """
    delayed = actual_days > expected_days
    hot = destination_temp > threshold_temp

    if delayed and hot:
        return "CRITICAL"
    if hot:
        return "TEMP_RISK"
    if delayed:
        return "DELAYED"
    return "ON_TIME"


def is_on_time(
    actual_days: int,
    service_level: str,
    temp: float | None = None,
    threshold_temp: float = 50.0,
) -> bool:
    """Determine if a shipment is on-time considering service level and temperature.

    Service levels: '1-Day' (1d), '2-Day' (2d), '3-Day' (3d).
    A shipment is on-time if it arrives within service level AND
    (if temp provided) destination temp doesn't exceed threshold.
    """
    expected = {"1-Day": 1, "2-Day": 2, "3-Day": 3}.get(service_level, 3)
    within_time = actual_days <= expected

    if temp is not None and temp > threshold_temp:
        return False

    return within_time
