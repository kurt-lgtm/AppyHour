"""
Shipping Reports MCP tools — cost analysis, transit performance, routing recommendations.
Wraps the same logic as GelPackCalculator/app/routers/shipping.py.
"""

import json
from typing import Optional
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum

from utils import format_error, to_json, SHIPPING_DIR


# Lazy-loaded modules
_analyze = None
_recommend = None


def _get_analyze():
    """Lazy-import reports.analyze module."""
    global _analyze
    if _analyze is None:
        from reports import analyze
        _analyze = analyze
    return _analyze


def _get_recommend():
    """Lazy-import reports.recommend module."""
    global _recommend
    if _recommend is None:
        from reports import recommend
        _recommend = recommend
    return _recommend


def _load_shipments():
    """Load shipments from the most recent output file."""
    output_file = SHIPPING_DIR / "output" / "shipments.json"
    if not output_file.exists():
        # Try data directory
        data_dir = SHIPPING_DIR / "data"
        if data_dir.exists():
            files = sorted(data_dir.glob("*.json"), reverse=True)
            if files:
                analyze = _get_analyze()
                return analyze.load_shipments(str(files[0]))
        raise FileNotFoundError(
            "No shipment data found. Run 'python ingest.py' in ShippingReports/ first."
        )
    analyze = _get_analyze()
    return analyze.load_shipments(str(output_file))


def register(mcp):
    """Register shipping analysis tools on the MCP server."""

    # -----------------------------------------------------------------------
    # Input models
    # -----------------------------------------------------------------------

    class GroupByChoice(str, Enum):
        STATE = "state"
        CARRIER = "carrier"
        HUB = "hub"
        ZONE = "zone"

    class CostAnalysisInput(BaseModel):
        """Input for shipping cost analysis."""
        model_config = ConfigDict(str_strip_whitespace=True)

        group_by: GroupByChoice = Field(
            GroupByChoice.STATE,
            description="Group results by: state, carrier, hub, or zone"
        )
        carrier: Optional[str] = Field(None, description="Filter to a specific carrier (e.g. 'OnTrac', 'UPS', 'FedEx')")

    class TransitAnalysisInput(BaseModel):
        """Input for transit time analysis."""
        model_config = ConfigDict(str_strip_whitespace=True)

        group_by: str = Field("state", description="Group results by: state, carrier, or hub")
        carrier: Optional[str] = Field(None, description="Filter to a specific carrier")

        @field_validator("group_by")
        @classmethod
        def validate_group_by(cls, v: str) -> str:
            allowed = {"state", "carrier", "hub"}
            if v not in allowed:
                raise ValueError(f"group_by must be one of: {', '.join(allowed)}")
            return v

    class Chronic3DayInput(BaseModel):
        """Input for chronic 3-day zip identification."""
        model_config = ConfigDict(str_strip_whitespace=True)

        min_volume: int = Field(5, description="Minimum shipment volume to consider a zip", ge=1)
        pct_threshold: float = Field(25.0, description="Minimum % of shipments taking 3+ days to flag", ge=0, le=100)

    # -----------------------------------------------------------------------
    # Tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="appyhour_analyze_shipping_costs",
        annotations={
            "title": "Analyze Shipping Costs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def analyze_shipping_costs(params: CostAnalysisInput) -> str:
        """Analyze shipping costs grouped by state, carrier, hub, or zone.

        Uses historical invoice data (OnTrac, UPS, FedEx) to break down
        average cost per shipment across the grouping dimension. Useful for
        identifying expensive lanes and carrier cost differences.

        Args:
            params: group_by dimension and optional carrier filter.

        Returns:
            JSON with cost breakdown: group key, shipment count, total cost,
            average cost per shipment.
        """
        try:
            analyze = _get_analyze()
            shipments = _load_shipments()
            filters = {}
            if params.carrier:
                filters["carrier"] = params.carrier
            result = analyze.cost_analysis(
                shipments, group_by=params.group_by.value, filters=filters or None
            )
            return to_json({"group_by": params.group_by.value, "data": result})
        except Exception as e:
            return format_error(e, "analyze_shipping_costs")

    @mcp.tool(
        name="appyhour_analyze_transit",
        annotations={
            "title": "Analyze Transit Performance",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def analyze_transit(params: TransitAnalysisInput) -> str:
        """Analyze transit time performance grouped by state, carrier, or hub.

        Shows average transit days, percentage of shipments taking 3+ days
        (thermal risk zone), and distribution of transit times. Helps identify
        lanes where shipments are consistently slow.

        Args:
            params: group_by dimension and optional carrier filter.

        Returns:
            JSON with transit metrics per group: avg_days, pct_3plus_days,
            shipment_count, transit distribution.
        """
        try:
            analyze = _get_analyze()
            shipments = _load_shipments()
            filters = {}
            if params.carrier:
                filters["carrier"] = params.carrier
            result = analyze.transit_analysis(
                shipments, group_by=params.group_by, filters=filters or None
            )
            return to_json({"group_by": params.group_by, "data": result})
        except Exception as e:
            return format_error(e, "analyze_transit")

    @mcp.tool(
        name="appyhour_detect_misroutes",
        annotations={
            "title": "Detect Shipping Misroutes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def detect_misroutes() -> str:
        """Detect shipments that were routed to the wrong fulfillment hub.

        Compares actual shipping hub against the expected hub based on territory
        assignments in config.yaml. Misrouted shipments typically have higher
        costs and longer transit times.

        Returns:
            JSON with list of misrouted shipments including tracking number,
            expected vs actual hub, state, and cost penalty.
        """
        try:
            import yaml
            analyze = _get_analyze()
            shipments = _load_shipments()

            config_path = SHIPPING_DIR / "config.yaml"
            territories = {}
            acceptable_hubs = None
            dallas_2day_states = None
            if config_path.exists():
                with open(config_path) as f:
                    cfg = yaml.safe_load(f)
                    territories = cfg.get("territories", {})
                    raw_acceptable = cfg.get("acceptable_hubs", {})
                    if raw_acceptable:
                        acceptable_hubs = {k: set(v) for k, v in raw_acceptable.items()}
                    raw_2day = cfg.get("dallas_2day_states", [])
                    if raw_2day:
                        dallas_2day_states = set(raw_2day)

            result = analyze.misroute_analysis(
                shipments, territories,
                acceptable_hubs=acceptable_hubs,
                dallas_2day_states=dallas_2day_states,
            )
            return to_json({"misroutes": result})
        except Exception as e:
            return format_error(e, "detect_misroutes")

    @mcp.tool(
        name="appyhour_get_chronic_3day_zips",
        annotations={
            "title": "Find Chronic 3-Day Delivery Zones",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_chronic_3day_zips(params: Chronic3DayInput) -> str:
        """Identify zip codes with chronically slow (3+ day) transit times.

        These zips represent thermal risk zones where ground shipping frequently
        takes 3 or more days, putting perishable items at risk. Consider forcing
        2-Day shipping or adding extra gel packs for these destinations.

        Args:
            params: min_volume (min shipments to consider) and pct_threshold
                    (min % of slow shipments to flag).

        Returns:
            JSON with list of problematic zip codes, their 3-day percentage,
            volume, and carrier breakdown.
        """
        try:
            recommend = _get_recommend()
            shipments = _load_shipments()
            result = recommend.find_chronic_3day_zips(
                shipments, min_volume=params.min_volume, pct_threshold=params.pct_threshold
            )
            return to_json({"chronic_3day_zips": result, "count": len(result)})
        except Exception as e:
            return format_error(e, "get_chronic_3day_zips")

    @mcp.tool(
        name="appyhour_get_zip_overrides",
        annotations={
            "title": "Generate Zip Routing Overrides",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_zip_overrides() -> str:
        """Generate complete zip-code routing override recommendations.

        Combines all shipping analyses (force-2day, misrouted, chronic 3-day)
        into a unified set of routing rules. These can be imported into the
        GelPackCalculator as a routing profile.

        Returns:
            JSON with zip override rules: zip code, recommended action
            (force_2day, reroute, add_gel), reason, and supporting data.
        """
        try:
            recommend = _get_recommend()
            shipments = _load_shipments()

            force_2day = recommend.find_force_2day_zips(shipments)
            api_forced = recommend.find_api_forced_2day_zips(shipments)
            misrouted = recommend.find_misrouted_zips(shipments)
            chronic = recommend.find_chronic_3day_zips(shipments)

            overrides = recommend.build_zip_overrides(force_2day, api_forced, misrouted, chronic)
            return to_json({"overrides": overrides, "count": len(overrides)})
        except Exception as e:
            return format_error(e, "get_zip_overrides")
