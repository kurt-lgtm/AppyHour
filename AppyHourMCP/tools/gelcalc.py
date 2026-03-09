"""
Gel Pack Calculator MCP tools — thermal analysis, weather, and alerts.
Wraps the same logic as GelPackCalculator/app/routers/gelcalc.py.
"""

import json
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from utils import get_gelcalc_settings, format_error, to_json


def register(mcp):
    """Register gel pack calculator tools on the MCP server."""

    # -----------------------------------------------------------------------
    # Input models
    # -----------------------------------------------------------------------

    class AnalyzeShipmentInput(BaseModel):
        """Input for single-shipment thermal analysis."""
        model_config = ConfigDict(str_strip_whitespace=True)

        origin: str = Field("TX", description="Origin hub state code (e.g. 'TX', 'TN', 'CA')")
        dest_state: str = Field(..., description="Destination state code (e.g. 'CA', 'FL', 'NY')")
        peak_temp_f: float = Field(..., description="Peak forecast temperature in Fahrenheit for destination")
        avg_temp_f: Optional[float] = Field(None, description="Average transit temperature in F (defaults to peak if omitted)")
        transit_days: Optional[int] = Field(None, description="Override transit days (1, 2, or 3). Auto-detected from state config if omitted.")
        box_l: Optional[float] = Field(None, description="Box length in inches (uses default if omitted)")
        box_w: Optional[float] = Field(None, description="Box width in inches")
        box_h: Optional[float] = Field(None, description="Box height in inches")

    class WeatherInput(BaseModel):
        """Input for weather forecast lookup."""
        model_config = ConfigDict(str_strip_whitespace=True)

        zip_code: str = Field(..., description="5-digit US zip code (e.g. '75042', '90210')", min_length=5, max_length=5)

    class AlertsInput(BaseModel):
        """Input for NWS weather alerts lookup."""
        model_config = ConfigDict(str_strip_whitespace=True)

        lat: float = Field(..., description="Latitude of the location")
        lon: float = Field(..., description="Longitude of the location")
        days_ahead: int = Field(4, description="Number of days ahead to check for alerts", ge=1, le=7)

    # -----------------------------------------------------------------------
    # Tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="appyhour_analyze_shipment",
        annotations={
            "title": "Analyze Shipment Thermal Needs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def analyze_shipment(params: AnalyzeShipmentInput) -> str:
        """Run thermal analysis for a single shipment to determine gel pack requirements.

        Calculates heat gain during transit based on temperature, box dimensions,
        insulation R-value, and transit duration. Returns the recommended gel pack
        configuration (number and size of packs), BTU capacity, cost, and risk level.

        Args:
            params: Shipment details including destination state and temperature.

        Returns:
            JSON with thermal analysis results: config_name, packs needed, BTU values,
            cost, risk level (OK/WARNING/EXCEEDED), and recommended Shopify gel tags.
        """
        try:
            from gel_pack_shopify import (
                analyze_order, calc_surface_area, calc_r_total,
                get_transit_type, state_from_code, MELT_EFFICIENCY,
                DEFAULT_R_PER_INCH, DEFAULT_THICKNESS, DEFAULT_R_AIR_FILM,
                DEFAULT_BOX_L, DEFAULT_BOX_W, DEFAULT_BOX_H,
                TARGET_TEMP_DEFAULT, SAFETY_FACTOR_DEFAULT,
            )

            s = get_gelcalc_settings()

            box_l = params.box_l or float(s.get("box_length", DEFAULT_BOX_L))
            box_w = params.box_w or float(s.get("box_width", DEFAULT_BOX_W))
            box_h = params.box_h or float(s.get("box_height", DEFAULT_BOX_H))

            surface_area = calc_surface_area(box_l, box_w, box_h)
            r_total = calc_r_total(
                float(s.get("r_per_inch", DEFAULT_R_PER_INCH)),
                float(s.get("insulation_thickness", DEFAULT_THICKNESS)),
                float(s.get("r_air_film", DEFAULT_R_AIR_FILM)),
            )

            dest_name = state_from_code(params.dest_state)
            if params.transit_days is not None:
                transit_type = f"{params.transit_days}-Day"
            else:
                transit_type = get_transit_type(dest_name) if dest_name else "3-Day"

            outside_temp = params.avg_temp_f if params.avg_temp_f is not None else params.peak_temp_f

            result = analyze_order(
                outside_temp=outside_temp,
                transit_type=transit_type,
                hub_hours_1day=float(s.get("hub_hours_1day", 8)),
                hub_hours_2day=float(s.get("hub_hours_2day", 8)),
                hub_hours_3day=float(s.get("hub_hours_3day", 8)),
                hub_temp=float(s.get("hub_temp", 75)),
                surface_area=surface_area,
                r_total=r_total,
                target_temp=float(s.get("threshold_temp", TARGET_TEMP_DEFAULT)),
                safety_factor_pct=float(s.get("safety_factor", SAFETY_FACTOR_DEFAULT)),
            )

            effective_btu = result["config_btu"] * MELT_EFFICIENCY
            cost = round(
                result["config_48oz"] * float(s.get("gel_48oz_cost", 1.50))
                + result["config_24oz"] * float(s.get("gel_24oz_cost", 0.85)),
                2,
            )

            return to_json({
                "origin": params.origin,
                "dest_state": params.dest_state,
                "transit_type": transit_type,
                "outside_temp_f": outside_temp,
                "peak_temp_f": params.peak_temp_f,
                "total_heat_btu": round(result["total_q_safe"], 1),
                "config_name": result["config_name"],
                "packs_48oz": result["config_48oz"],
                "packs_24oz": result["config_24oz"],
                "config_btu": result["config_btu"],
                "effective_btu": round(effective_btu, 1),
                "margin_btu": round(effective_btu - result["total_q_safe"], 1),
                "cap_pct": round(result["cap_pct"], 1),
                "risk": result["risk"],
                "exceeded": result["exceeded"],
                "gel_tags": result["config_tags"],
                "cost": cost,
            })
        except Exception as e:
            return format_error(e, "analyze_shipment")

    @mcp.tool(
        name="appyhour_get_weather",
        annotations={
            "title": "Get Weather Forecast for Zip Code",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def get_weather(params: WeatherInput) -> str:
        """Fetch weather forecast for a US zip code using OpenWeatherMap.

        Returns average, peak, and minimum temperatures over the forecast window,
        plus lat/lon coordinates (useful for subsequent NWS alert lookups).

        Args:
            params: Contains the 5-digit zip code to look up.

        Returns:
            JSON with avg_temp_f, peak_temp_f, min_temp_f, lat, lon, and
            number of forecast data points.
        """
        try:
            from gel_pack_shopify import fetch_weather_by_zip

            s = get_gelcalc_settings()
            api_key = s.get("owm_api_key", "")
            if not api_key:
                return "Error: OpenWeatherMap API key not configured in GelPackCalculator settings."

            forecasts, lat, lon = fetch_weather_by_zip(api_key, params.zip_code)
            if forecasts is None:
                return f"Error: Could not fetch weather data for zip {params.zip_code}."

            temps = [t for _, t in forecasts]
            return to_json({
                "zip": params.zip_code,
                "lat": lat,
                "lon": lon,
                "avg_temp_f": round(sum(temps) / len(temps), 1) if temps else None,
                "peak_temp_f": round(max(temps), 1) if temps else None,
                "min_temp_f": round(min(temps), 1) if temps else None,
                "forecast_points": len(temps),
            })
        except Exception as e:
            return format_error(e, "get_weather")

    @mcp.tool(
        name="appyhour_get_weather_alerts",
        annotations={
            "title": "Get NWS Weather Alerts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def get_weather_alerts(params: AlertsInput) -> str:
        """Fetch active NWS weather alerts for a location.

        Checks the National Weather Service API for severe weather alerts
        (heat advisories, winter storms, etc.) that may affect shipments.
        Use the lat/lon from appyhour_get_weather to look up alerts for a zip code.

        Args:
            params: Latitude, longitude, and optional days_ahead (default 4).

        Returns:
            JSON with list of active alerts (event type, severity, headline, description)
            and total alert count.
        """
        try:
            from gel_pack_shopify import fetch_nws_alerts

            alerts = fetch_nws_alerts(params.lat, params.lon, days_ahead=params.days_ahead)
            return to_json({"alerts": alerts, "count": len(alerts)})
        except Exception as e:
            return format_error(e, "get_weather_alerts")
