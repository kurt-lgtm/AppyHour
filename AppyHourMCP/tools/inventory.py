"""
Inventory & Forecasting MCP tools — subscription demand, forecasts, reorder alerts.
Wraps the same logic as GelPackCalculator/app/routers/inventory.py.
"""

import asyncio
import json
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from utils import get_inventory_settings, format_error, to_json


# Lazy-loaded client singletons
_module = None
_recharge = None


def _get_module():
    """Lazy-import inventory_reorder module."""
    global _module
    if _module is None:
        import inventory_reorder as mod
        _module = mod
    return _module


def _get_recharge():
    """Get or create the RechargeClient singleton."""
    global _recharge
    if _recharge is None:
        mod = _get_module()
        settings = mod.load_settings()
        token = settings.get("recharge_api_token", "")
        if not token:
            raise RuntimeError("ReCharge API token not configured in InventoryReorder settings.")
        _recharge = mod.RechargeClient(token)
    return _recharge


def register(mcp):
    """Register inventory & forecasting tools on the MCP server."""

    # -----------------------------------------------------------------------
    # Input models
    # -----------------------------------------------------------------------

    class ForecastInput(BaseModel):
        """Input for demand forecasting."""
        model_config = ConfigDict(str_strip_whitespace=True)

        months: int = Field(3, description="Number of months to forecast ahead (1-12)", ge=1, le=12)

    # -----------------------------------------------------------------------
    # Tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="appyhour_get_subscription_demand",
        annotations={
            "title": "Get Subscription SKU Demand",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def get_subscription_demand() -> str:
        """Get current weekly SKU demand from active ReCharge subscriptions.

        Fetches all active subscriptions from the ReCharge API and aggregates
        them into total weekly quantities per SKU. This is the baseline demand
        signal for inventory planning.

        Returns:
            JSON with SKU quantities (sku -> weekly_qty) from active subscriptions.
        """
        try:
            rc = _get_recharge()
            subs = await asyncio.to_thread(rc.get_active_subscriptions)
            skus = rc.aggregate_sku_quantities(subs)
            return to_json({"subscription_count": len(subs), "skus": skus})
        except Exception as e:
            return format_error(e, "get_subscription_demand")

    @mcp.tool(
        name="appyhour_get_upcoming_charges",
        annotations={
            "title": "Get Upcoming Charges by Month",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def get_upcoming_charges() -> str:
        """Fetch queued/scheduled charges from ReCharge, grouped by month.

        Shows the upcoming charges already scheduled in ReCharge, resolved into
        per-month SKU demand. This provides a more accurate near-term forecast
        than cohort projections since these are actual queued orders.

        Returns:
            JSON with charges_count and by_month breakdown of SKU quantities.
        """
        try:
            rc = _get_recharge()
            mod = _get_module()
            charges = await asyncio.to_thread(rc.get_queued_charges)
            resolved = mod.resolve_queued_charges(charges)
            return to_json({"charges_count": len(charges), "by_month": resolved})
        except Exception as e:
            return format_error(e, "get_upcoming_charges")

    @mcp.tool(
        name="appyhour_forecast_demand",
        annotations={
            "title": "Forecast SKU Demand",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def forecast_demand(params: ForecastInput) -> str:
        """Generate multi-month SKU demand forecast from cohort data.

        Uses the cohort-based forecasting model: takes subscriber cohorts,
        applies retention curves, curation recipes, and PR-CJAM/CEX-EC
        assignments to project total SKU demand per month. Overlays queued
        charge data for near-term accuracy.

        Args:
            params: Number of months to forecast (1-12, default 3).

        Returns:
            JSON with per-month, per-SKU demand projections.
        """
        try:
            mod = _get_module()
            rc = _get_recharge()
            settings = mod.load_settings()

            subs = await asyncio.to_thread(rc.get_active_subscriptions)
            cohorts = rc.build_cohorts_from_subscriptions(subs)

            retention = settings.get("retention_matrix", {})
            recipes = settings.get("curation_recipes", {})
            charges = await asyncio.to_thread(rc.get_queued_charges)
            resolved = mod.resolve_queued_charges(charges)

            first_month = next(iter(resolved.values()), {})
            pr_cjam = first_month.get("pr_cjam", 0)
            cex_ec = first_month.get("cex_ec", 0)

            forecast = mod.forecast_cohort_demand(
                cohorts, retention, recipes, pr_cjam, cex_ec,
                forecast_months=params.months,
            )
            return to_json({"months": params.months, "forecast": forecast})
        except Exception as e:
            return format_error(e, "forecast_demand")

    @mcp.tool(
        name="appyhour_get_reorder_alerts",
        annotations={
            "title": "Get Inventory Reorder Alerts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def get_reorder_alerts() -> str:
        """Generate reorder alerts by comparing forecast demand to current inventory.

        Runs the full forecasting pipeline (3-month horizon), then compares projected
        demand against on-hand inventory, open POs, and wheel supply. Returns alerts
        for SKUs that are CRITICAL, WARNING, or need a PLAN-level action (PO, MFG,
        or Transfer).

        Returns:
            JSON with reorder alerts per SKU including status level (CRITICAL/WARNING/
            OK/PLAN), action type (PO/MFG/Transfer), current stock, and projected shortfall.
        """
        try:
            mod = _get_module()
            rc = _get_recharge()
            settings = mod.load_settings()

            subs = await asyncio.to_thread(rc.get_active_subscriptions)
            cohorts = rc.build_cohorts_from_subscriptions(subs)

            retention = settings.get("retention_matrix", {})
            recipes = settings.get("curation_recipes", {})
            charges = await asyncio.to_thread(rc.get_queued_charges)
            resolved = mod.resolve_queued_charges(charges)
            first_month = next(iter(resolved.values()), {})

            forecast = mod.forecast_cohort_demand(
                cohorts, retention, recipes,
                first_month.get("pr_cjam", 0),
                first_month.get("cex_ec", 0),
                forecast_months=3,
            )

            inventory = settings.get("current_inventory", settings.get("inventory", {}))
            open_pos = settings.get("open_purchase_orders", settings.get("open_pos", []))
            wheel_supply = {}
            if hasattr(mod, "compute_wheel_supply"):
                wheel_inv = settings.get("wheel_inventory", {})
                adjusted = settings.get("adjusted_conversion_factors", {})
                wheel_supply = mod.compute_wheel_supply(wheel_inv, adjusted)

            alerts = mod.compute_reorder_alerts(forecast, inventory, open_pos, wheel_supply)
            return to_json({"alerts": alerts})
        except Exception as e:
            return format_error(e, "get_reorder_alerts")
