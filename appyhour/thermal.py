"""Thermal analysis and gel pack sizing calculations.

Pure functions extracted from GelPackCalculator/gel_pack_shopify.py.
No GUI or API dependencies — import freely from any module.
"""

from __future__ import annotations

# ── Physical constants ───────────────────────────────────────────────────

HEAT_CAPACITY = 35.7  # BTU/F product heat capacity
TARGET_TEMP_DEFAULT = 50.0  # F default max product temp
MELT_EFFICIENCY = 0.90  # gel packs lose ~10% effectiveness near full melt

GEL_CONFIGS = [
    {"name": "1x 48oz (baseline)", "48oz": 1, "24oz": 0, "btu": 489.0, "tags": []},
    {"name": "1x 48oz + 1x 24oz", "48oz": 1, "24oz": 1, "btu": 733.5, "tags": ["!ExtraGel24oz!"]},
    {"name": "2x 48oz", "48oz": 2, "24oz": 0, "btu": 978.0, "tags": ["!ExtraGel48oz!"]},
    {"name": "2x 48oz + 1x 24oz", "48oz": 2, "24oz": 1, "btu": 1222.5, "tags": ["!ExtraGel24oz!", "!ExtraGel48oz!"]},
]


# ── Core calculations ────────────────────────────────────────────────────

def calc_surface_area(l_in: float, w_in: float, h_in: float) -> float:
    """Calculate box surface area in square feet from dimensions in inches."""
    sa_in2 = 2 * (l_in * w_in + l_in * h_in + w_in * h_in)
    return sa_in2 / 144.0


def calc_r_total(r_per_inch: float, thickness: float, r_air_film: float) -> float:
    """Calculate total thermal resistance (R-value)."""
    return (r_per_inch * thickness) + r_air_film


def calc_heat_gain(
    outside_temp: float,
    hours: float,
    surface_area: float,
    r_total: float,
    target_temp: float | None = None,
) -> float:
    """Calculate heat gain in BTU over a time period.

    Formula: Q = (deltaT * surface_area * hours) / R_total
    """
    if target_temp is None:
        target_temp = TARGET_TEMP_DEFAULT
    delta_t = max(0, outside_temp - target_temp)
    return (delta_t * surface_area * hours) / r_total


def recommend_config(total_heat_btu: float) -> dict:
    """Return the smallest gel pack configuration that covers the heat load.

    Returns the config dict with an 'exceeded' key if no config is sufficient.
    """
    for config in GEL_CONFIGS:
        effective_btu = config["btu"] * MELT_EFFICIENCY
        if effective_btu >= total_heat_btu:
            return config
    return {**GEL_CONFIGS[-1], "exceeded": True}


def analyze_order(
    outside_temp: float,
    transit_type: str,
    hub_hours_1day: float,
    hub_hours_2day: float,
    hub_hours_3day: float,
    hub_temp: float,
    surface_area: float,
    r_total: float,
    target_temp: float | None = None,
    safety_factor_pct: float = 0,
    origin_temp: float | None = None,
) -> dict:
    """Analyze gel pack needs for an order.

    If origin_temp is provided, first half of transit uses origin_temp,
    second half uses outside_temp (destination).
    """
    if target_temp is None:
        target_temp = TARGET_TEMP_DEFAULT

    cycle_map = {"1-Day": (24.0, hub_hours_1day), "2-Day": (48.0, hub_hours_2day)}
    total_cycle, hub_hours = cycle_map.get(transit_type, (72.0, hub_hours_3day))
    actual_transit = max(0, total_cycle - hub_hours)

    if origin_temp is not None:
        origin_hours = actual_transit / 2.0
        dest_hours = actual_transit - origin_hours
        q_transit = (
            calc_heat_gain(origin_temp, origin_hours, surface_area, r_total, target_temp)
            + calc_heat_gain(outside_temp, dest_hours, surface_area, r_total, target_temp)
        )
    else:
        q_transit = calc_heat_gain(outside_temp, actual_transit, surface_area, r_total, target_temp)

    q_hub = calc_heat_gain(hub_temp, hub_hours, surface_area, r_total, target_temp)
    total_q = q_transit + q_hub
    total_q_safe = total_q * (1 + safety_factor_pct / 100.0)

    config = recommend_config(total_q_safe)
    exceeded = config.get("exceeded", False)
    effective_btu = config["btu"] * MELT_EFFICIENCY
    cap_pct = (total_q_safe / effective_btu * 100) if effective_btu > 0 else 0

    if total_q_safe <= effective_btu:
        temp_rise = 0.0
        final_temp = target_temp
    else:
        excess = total_q_safe - effective_btu
        temp_rise = excess / HEAT_CAPACITY
        final_temp = target_temp + temp_rise

    if exceeded and temp_rise >= 10:
        risk = "CRITICAL"
    elif exceeded or temp_rise >= 5:
        risk = "HIGH"
    elif cap_pct >= 75:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    return {
        "total_cycle": total_cycle,
        "hub_hours": hub_hours,
        "actual_transit": actual_transit,
        "q_transit": q_transit,
        "q_hub": q_hub,
        "total_q": total_q,
        "total_q_safe": total_q_safe,
        "config_name": config["name"],
        "config_btu": config["btu"],
        "config_48oz": config.get("48oz", 0),
        "config_24oz": config.get("24oz", 0),
        "config_tags": list(config.get("tags", [])),
        "cap_pct": cap_pct,
        "temp_rise": temp_rise,
        "final_temp": final_temp,
        "risk": risk,
        "exceeded": exceeded,
    }
