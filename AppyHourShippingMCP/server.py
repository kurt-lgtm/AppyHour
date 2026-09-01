#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp", "pydantic", "requests", "pyyaml"]
# ///
"""AppyHour Shipping MCP — standalone server for shipping + weather tools.

4 tools:
  appyhour_shipping_analysis
  appyhour_apply_zip_routing_tags
  appyhour_get_weather
  appyhour_get_weather_alerts
"""

import json
import logging
import sys
import time
import traceback
import types
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import requests
from pydantic import BaseModel, ConfigDict, Field

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("appyhour_shipping_mcp")

# sys.path so we can import appyhour_lib package + ShippingReports.reports.*
_THIS_DIR = Path(__file__).resolve().parent
_APPYHOUR_ROOT = _THIS_DIR.parent
sys.path.insert(0, str(_THIS_DIR))
sys.path.insert(0, str(_APPYHOUR_ROOT))
sys.path.insert(0, str(_APPYHOUR_ROOT / "ShippingReports"))

from mcp.server.fastmcp import FastMCP

from utils import (
    SHIPPING_DIR,
    format_error,
    shopify_paginate,
    to_json,
)
from appyhour_lib.credentials import get_openweather_key, get_shopify_auth
from appyhour_lib.paths import gel_calc_settings_path

mcp: FastMCP = FastMCP("appyhour_shipping_mcp")

# ── Weather cache ────────────────────────────────────────────────────
_weather_cache: dict[str, tuple[float, dict]] = {}
_WEATHER_TTL = 3600

# ── Lazy module loaders for shipping reports ─────────────────────────
_analyze: types.ModuleType | None = None
_recommend: types.ModuleType | None = None


def _get_analyze() -> types.ModuleType:
    global _analyze
    if _analyze is None:
        from reports import analyze
        _analyze = analyze
    return _analyze


def _get_recommend() -> types.ModuleType:
    global _recommend
    if _recommend is None:
        from reports import recommend
        _recommend = recommend
    return _recommend


def _load_shipments() -> list[dict]:
    output_file = SHIPPING_DIR / "output" / "shipments.json"
    if output_file.exists():
        return _get_analyze().load_shipments(str(output_file))
    data_dir = SHIPPING_DIR / "data"
    if data_dir.exists():
        files = sorted(data_dir.glob("*.json"), reverse=True)
        if files:
            return _get_analyze().load_shipments(str(files[0]))
    return []


def _load_misroute_config() -> tuple[dict, dict | None, set | None]:
    import yaml
    config_path = SHIPPING_DIR / "config.yaml"
    territories: dict = {}
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
    return territories, acceptable_hubs, dallas_2day_states


# ── Input models ─────────────────────────────────────────────────────


class ReportType(str, Enum):
    COSTS = "costs"
    TRANSIT = "transit"
    MISROUTES = "misroutes"
    CHRONIC_ZIPS = "chronic_zips"
    OVERRIDES = "overrides"


class GroupByChoice(str, Enum):
    STATE = "state"
    CARRIER = "carrier"
    HUB = "hub"
    ZONE = "zone"


class ShippingAnalysisInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    report_type: ReportType = Field(description="costs, transit, misroutes, chronic_zips, overrides")
    group_by: GroupByChoice = Field(GroupByChoice.STATE, description="state, carrier, hub, zone")
    carrier: str | None = Field(None, description="Filter to a specific carrier (costs/transit)")
    min_volume: int = Field(5, description="Min shipments per zip (chronic_zips)", ge=1)
    pct_threshold: float = Field(25.0, description="% 3+ day threshold (chronic_zips)", ge=0, le=100)


class WeatherInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    zip_code: str = Field(..., min_length=5, max_length=5, description="5-digit US zip code")


class AlertsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    lat: float = Field(..., description="Latitude")
    lon: float = Field(..., description="Longitude")
    days_ahead: int = Field(4, description="Days ahead to check", ge=1, le=7)


# ── Tools ────────────────────────────────────────────────────────────


@mcp.tool(
    name="appyhour_shipping_analysis",
    annotations={
        "title": "Shipping Analysis",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def shipping_analysis(params: ShippingAnalysisInput) -> str:
    """Shipping analysis — costs, transit, misroutes, chronic zips, or routing overrides."""
    try:
        shipments = _load_shipments()
        report = params.report_type

        if report == ReportType.COSTS:
            analyze = _get_analyze()
            filters = {"carrier": params.carrier} if params.carrier else None
            result = analyze.cost_analysis(shipments, group_by=params.group_by.value, filters=filters)
            return to_json({"report_type": "costs", "group_by": params.group_by.value, "data": result})

        if report == ReportType.TRANSIT:
            analyze = _get_analyze()
            filters = {"carrier": params.carrier} if params.carrier else None
            group_by = params.group_by.value
            if group_by == "zone":
                group_by = "state"
            result = analyze.transit_analysis(shipments, group_by=group_by, filters=filters)
            return to_json({"report_type": "transit", "group_by": group_by, "data": result})

        if report == ReportType.MISROUTES:
            analyze = _get_analyze()
            territories, acceptable_hubs, dallas_2day_states = _load_misroute_config()
            result = analyze.misroute_analysis(
                shipments, territories,
                acceptable_hubs=acceptable_hubs,
                dallas_2day_states=dallas_2day_states,
            )
            return to_json({"report_type": "misroutes", "misroutes": result})

        if report == ReportType.CHRONIC_ZIPS:
            recommend = _get_recommend()
            result = recommend.find_chronic_3day_zips(
                shipments, min_volume=params.min_volume, pct_threshold=params.pct_threshold,
            )
            return to_json({"report_type": "chronic_zips", "chronic_3day_zips": result, "count": len(result)})

        if report == ReportType.OVERRIDES:
            recommend = _get_recommend()
            force_2day = recommend.find_force_2day_zips(shipments)
            api_forced = recommend.find_api_forced_2day_zips(shipments)
            misrouted = recommend.find_misrouted_zips(shipments)
            chronic = recommend.find_chronic_3day_zips(shipments)
            overrides = recommend.build_zip_overrides(force_2day, api_forced, misrouted, chronic)
            return to_json({"report_type": "overrides", "overrides": overrides, "count": len(overrides)})

        return to_json({"error": f"Unknown report_type: {report}"})
    except Exception as e:
        return format_error(e, "shipping_analysis")


@mcp.tool(
    name="appyhour_apply_zip_routing_tags",
    annotations={
        "title": "Apply Routing Tags for Zip Overrides",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def apply_zip_routing_tags(dry_run: bool = True) -> str:
    """Apply routing tags to unfulfilled Shopify orders in zip-override zones."""
    try:
        rest_base, headers = get_shopify_auth()
        # Build GraphQL URL by replacing the REST suffix.
        gql_url = rest_base.rsplit("/", 1)[0] + "/graphql.json"

        # 🔴 NOT the repo-local GelPackCalculator copy, and NOT %APPDATA% (through 2026-08-31).
        # This reads `zip_routing_overrides`, which decides which LIVE orders get retagged — so a
        # stale third copy silently tags the wrong zips. %APPDATA%\AppyHour is MSIX-virtualized
        # (packaged vs unpackaged readers saw two divergent files for three weeks); the canonical
        # merged copy is C:\AppyHourData. Resolve through appyhour_lib.paths, never by hand.
        try:
            gc_path = gel_calc_settings_path()
        except FileNotFoundError:
            return to_json({"error": "GelPack settings not found"})
        with open(gc_path, encoding="utf-8") as f:
            gc = json.load(f)

        overrides = gc.get("zip_routing_overrides", {})
        force_2day_zips = {z for z, v in overrides.items() if v.get("action") == "force_2day"}
        if not force_2day_zips:
            return to_json({"message": "No force_2day zip overrides configured", "tagged": 0})

        TAG = gc.get("default_routing_tag", "!FedEx 2Day - Dallas_AHB!")
        ROUTING_PREFIXES = ("!ANY", "!NO ", "!FedEx", "!UPS", "!OnTrac")

        cutoff = (datetime.now() - timedelta(days=21)).isoformat()
        all_orders = shopify_paginate(
            f"{rest_base}/orders.json", headers,
            params={
                "status": "open", "fulfillment_status": "unfulfilled",
                "limit": 250, "created_at_min": cutoff,
                "fields": "id,name,tags,shipping_address",
            },
            timeout=60, sleep=0.3,
        )

        targets: list[dict] = []
        for o in all_orders:
            addr = o.get("shipping_address") or {}
            zipcode = (addr.get("zip") or "").strip()
            prefix = zipcode[:3]
            if prefix not in force_2day_zips:
                continue
            tags = o.get("tags", "") or ""
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            routing_tags = [t for t in tag_list if any(t.startswith(p) for p in ROUTING_PREFIXES)]
            already_tagged = TAG in tag_list
            conflicts = [t for t in routing_tags if t != TAG]
            if already_tagged and not conflicts:
                continue
            targets.append({
                "order_id": o["id"],
                "name": o.get("name", ""),
                "city": addr.get("city", ""),
                "state": addr.get("province_code", ""),
                "zip": zipcode,
                "conflicts": conflicts,
                "already_tagged": already_tagged,
            })

        if dry_run:
            return to_json({
                "dry_run": True,
                "orders_to_tag": len(targets),
                "force_2day_zips": sorted(force_2day_zips),
                "tag": TAG,
                "details": targets,
            })

        tags_add_q = """
        mutation tagsAdd($id: ID!, $tags: [String!]!) {
          tagsAdd(id: $id, tags: $tags) {
            node { ... on Order { id name } }
            userErrors { field message }
          }
        }
        """
        tags_remove_q = """
        mutation tagsRemove($id: ID!, $tags: [String!]!) {
          tagsRemove(id: $id, tags: $tags) {
            node { ... on Order { id name } }
            userErrors { field message }
          }
        }
        """

        results: dict = {"tagged": 0, "conflicts_resolved": 0, "failed": 0, "details": []}
        for t in targets:
            gid = f"gid://shopify/Order/{t['order_id']}"
            if t["conflicts"]:
                resp = requests.post(gql_url, headers=headers, json={
                    "query": tags_remove_q,
                    "variables": {"id": gid, "tags": t["conflicts"]},
                }, timeout=30)
                data = resp.json()
                errors = data.get("data", {}).get("tagsRemove", {}).get("userErrors", [])
                if not errors:
                    results["conflicts_resolved"] += 1
                time.sleep(0.3)

            if not t["already_tagged"]:
                resp = requests.post(gql_url, headers=headers, json={
                    "query": tags_add_q,
                    "variables": {"id": gid, "tags": [TAG]},
                }, timeout=30)
                data = resp.json()
                if "errors" in data:
                    results["failed"] += 1
                    results["details"].append({"name": t["name"], "error": str(data["errors"])[:100]})
                else:
                    ue = data.get("data", {}).get("tagsAdd", {}).get("userErrors", [])
                    if ue:
                        results["failed"] += 1
                        results["details"].append({"name": t["name"], "error": str(ue)})
                    else:
                        results["tagged"] += 1
                        results["details"].append({
                            "name": t["name"],
                            "city": t["city"],
                            "zip": t["zip"],
                            "conflicts_removed": t["conflicts"],
                        })
                time.sleep(0.3)
        return to_json(results)
    except Exception as e:
        return format_error(e, "apply_zip_routing_tags")


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
    """Fetch OpenWeatherMap forecast for a 5-digit US zip. Returns avg/peak/min temps + lat/lon."""
    try:
        cached = _weather_cache.get(params.zip_code)
        if cached and (time.time() - cached[0]) < _WEATHER_TTL:
            return to_json(cached[1])

        from appyhour_lib.weather import fetch_weather_by_zip

        api_key = get_openweather_key()
        if not api_key:
            return "Error: OPENWEATHER_API_KEY env var not set."

        forecasts, lat, lon = fetch_weather_by_zip(api_key, params.zip_code)
        if forecasts is None:
            return f"Error: Could not fetch weather data for zip {params.zip_code}."

        temps = [t for _, t in forecasts]
        result = {
            "zip": params.zip_code,
            "lat": lat,
            "lon": lon,
            "avg_temp_f": round(sum(temps) / len(temps), 1) if temps else None,
            "peak_temp_f": round(max(temps), 1) if temps else None,
            "min_temp_f": round(min(temps), 1) if temps else None,
            "forecast_points": len(temps),
        }
        _weather_cache[params.zip_code] = (time.time(), result)
        return to_json(result)
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
    """Fetch active NWS alerts for a lat/lon location."""
    try:
        from appyhour_lib.weather import fetch_nws_alerts
        alerts, err = fetch_nws_alerts(params.lat, params.lon, days_ahead=params.days_ahead)
        if err:
            return to_json({"alerts": [], "count": 0, "error": err})
        return to_json({"alerts": alerts, "count": len(alerts)})
    except Exception as e:
        return format_error(e, "get_weather_alerts")


if __name__ == "__main__":
    try:
        # Reap this stdio server if its client dies uncleanly (Windows often
        # fails to deliver stdin-EOF on force-kill/crash). See appyhour_lib/proc.py
        # and the 2026-06-27 orphaned-server incident.
        try:
            from appyhour_lib.proc import install_parent_death_watchdog

            if install_parent_death_watchdog(logger=logger):
                logger.info("parent-death watchdog active (ppid reaping)")
        except Exception:
            logger.warning("parent-death watchdog unavailable", exc_info=True)

        logger.info("Starting AppyHour Shipping MCP server")
        mcp.run()
    except Exception:
        logger.critical("MCP server crashed:\n%s", traceback.format_exc())
        sys.exit(1)
