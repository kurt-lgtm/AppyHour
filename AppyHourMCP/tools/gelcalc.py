"""
Gel Pack Calculator MCP tools — single-shipment thermal analysis.

Weather + NWS-alert tools (appyhour_get_weather, appyhour_get_weather_alerts)
were moved out 2026-06-01 — they now live ONLY in AppyHourShippingMCP, the
load-on-demand shipping server, to keep this always-on server lean.
Wraps the same logic as GelPackCalculator/app/routers/gelcalc.py.
"""

from pydantic import BaseModel, Field, ConfigDict

from utils import get_gelcalc_settings, format_error, to_json


def register(mcp: object) -> None:
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
        avg_temp_f: float | None = Field(None, description="Average transit temperature in F (defaults to peak if omitted)")
        transit_days: int | None = Field(None, description="Override transit days (1, 2, or 3). Auto-detected from state config if omitted.")
        box_l: float | None = Field(None, description="Box length in inches (uses default if omitted)")
        box_w: float | None = Field(None, description="Box width in inches")
        box_h: float | None = Field(None, description="Box height in inches")

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
