"""
Shipping Reports MCP tools — cost analysis, transit performance, routing recommendations.
Wraps the same logic as GelPackCalculator/app/routers/shipping.py.

Phase 3 consolidation: 6 tools → 2 (1 parameterized read, 1 write).
"""

import json
import time
import re
import types
from pathlib import Path
from datetime import datetime, timedelta
from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum

import requests

from utils import format_error, to_json, SHIPPING_DIR, GELCALC_DIR, get_inventory_settings


# Lazy-loaded modules
_analyze: types.ModuleType | None = None
_recommend: types.ModuleType | None = None


def _get_analyze() -> types.ModuleType:
    """Lazy-import reports.analyze module."""
    global _analyze
    if _analyze is None:
        from reports import analyze
        _analyze = analyze
    return _analyze


def _get_recommend() -> types.ModuleType:
    """Lazy-import reports.recommend module."""
    global _recommend
    if _recommend is None:
        from reports import recommend
        _recommend = recommend
    return _recommend


def _load_shipments() -> list[dict]:
    """Load shipments from the most recent output file."""
    output_file = SHIPPING_DIR / "output" / "shipments.json"
    if not output_file.exists():
        data_dir = SHIPPING_DIR / "data"
        if data_dir.exists():
            files = sorted(data_dir.glob("*.json"), reverse=True)
            if files:
                analyze = _get_analyze()
                return analyze.load_shipments(str(files[0]))
        return []
    analyze = _get_analyze()
    return analyze.load_shipments(str(output_file))


def _load_misroute_config() -> tuple[dict, dict | None, set | None]:
    """Load territory/hub config for misroute analysis."""
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


def register(mcp: object) -> None:
    """Register shipping analysis tools on the MCP server."""

    # ------------------------------------------------------------------
    # Input models
    # ------------------------------------------------------------------

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
        """Input for unified shipping analysis tool."""
        model_config = ConfigDict(str_strip_whitespace=True)

        report_type: ReportType = Field(
            description="Analysis type: costs, transit, misroutes, chronic_zips, or overrides"
        )
        group_by: GroupByChoice = Field(
            GroupByChoice.STATE,
            description="Group results by: state, carrier, hub, or zone (costs/transit only)"
        )
        carrier: str | None = Field(
            None,
            description="Filter to a specific carrier, e.g. 'OnTrac', 'UPS', 'FedEx' (costs/transit only)"
        )
        min_volume: int = Field(
            5,
            description="Minimum shipment volume to consider a zip (chronic_zips only)",
            ge=1,
        )
        pct_threshold: float = Field(
            25.0,
            description="Minimum % of shipments taking 3+ days to flag (chronic_zips only)",
            ge=0,
            le=100,
        )

    # ------------------------------------------------------------------
    # Consolidated read-only analysis tool
    # ------------------------------------------------------------------

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
        """Unified shipping analysis — costs, transit, misroutes, chronic zips, or routing overrides.

        report_type controls which analysis runs:
        - costs: Shipping cost breakdown by state/carrier/hub/zone. Uses group_by + carrier params.
        - transit: Transit time performance (avg days, % 3+ day). Uses group_by + carrier params.
        - misroutes: Shipments routed to wrong hub vs territory config.
        - chronic_zips: Zip codes with chronically slow (3+ day) transit. Uses min_volume + pct_threshold.
        - overrides: Complete zip-code routing override recommendations (force_2day, reroute, add_gel).

        Returns JSON with analysis results.
        """
        try:
            shipments = _load_shipments()
            report = params.report_type

            if report == ReportType.COSTS:
                analyze = _get_analyze()
                filters = {"carrier": params.carrier} if params.carrier else None
                result = analyze.cost_analysis(
                    shipments, group_by=params.group_by.value, filters=filters
                )
                return to_json({"report_type": "costs", "group_by": params.group_by.value, "data": result})

            if report == ReportType.TRANSIT:
                analyze = _get_analyze()
                filters = {"carrier": params.carrier} if params.carrier else None
                # transit_analysis accepts state/carrier/hub (not zone)
                group_by = params.group_by.value
                if group_by == "zone":
                    group_by = "state"  # fallback — transit doesn't support zone
                result = analyze.transit_analysis(
                    shipments, group_by=group_by, filters=filters
                )
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
                    shipments, min_volume=params.min_volume, pct_threshold=params.pct_threshold
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

            return format_error(f"Unknown report_type: {report}", "shipping_analysis")
        except Exception as e:
            return format_error(e, "shipping_analysis")

    # ------------------------------------------------------------------
    # Write tool — apply routing tags (separate due to write risk)
    # ------------------------------------------------------------------

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
    async def apply_zip_routing_tags(
        dry_run: bool = True,
    ) -> str:
        """Find unfulfilled Shopify orders in zip-override zones and apply routing tags.

        Reads zip_routing_overrides from GelPackCalculator settings. For each
        unfulfilled order shipping to an override zip:
        1. Checks for conflicting routing tags and removes them
        2. Applies the appropriate routing tag (e.g. !FedEx 2Day - Dallas_AHB!)

        Args:
            dry_run: If True (default), report what would change without modifying orders.
                     Set to False to actually apply tags.

        Returns:
            JSON with tagged orders, conflicts resolved, and any errors.
        """
        try:
            settings = get_inventory_settings()
            store = settings.get("shopify_store_url", "").strip()
            token = settings.get("shopify_access_token", "").strip()
            if not store or not token:
                return to_json({"error": "Shopify credentials not configured"})

            gql_url = f"https://{store}.myshopify.com/admin/api/2024-01/graphql.json"
            rest_base = f"https://{store}.myshopify.com/admin/api/2024-01"
            headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

            # Load zip overrides from GelPack settings
            gc_path = GELCALC_DIR / "gel_calc_shopify_settings.json"
            if not gc_path.exists():
                return to_json({"error": "GelPack settings not found"})
            with open(gc_path) as f:
                gc = json.load(f)
            overrides = gc.get("zip_routing_overrides", {})
            force_2day_zips = {
                z for z, v in overrides.items() if v.get("action") == "force_2day"
            }
            if not force_2day_zips:
                return to_json({"message": "No force_2day zip overrides configured", "tagged": 0})

            TAG = gc.get("default_routing_tag", "!FedEx 2Day - Dallas_AHB!")
            ROUTING_PREFIXES = ("!ANY", "!NO ", "!FedEx", "!UPS", "!OnTrac")

            # Fetch unfulfilled orders
            cutoff = (datetime.now() - timedelta(days=21)).isoformat()
            url = f"{rest_base}/orders.json"
            params = {
                "status": "open", "fulfillment_status": "unfulfilled",
                "limit": 250, "created_at_min": cutoff,
                "fields": "id,name,tags,shipping_address",
            }
            all_orders: list[dict] = []
            while url:
                resp = requests.get(url, headers=headers, params=params, timeout=60)
                if resp.status_code != 200:
                    return to_json({"error": f"Shopify API returned {resp.status_code}: {resp.text[:200]}"})
                data = resp.json()
                all_orders.extend(data.get("orders", []))
                url = None
                params = None
                link = resp.headers.get("Link", "")
                if 'rel="next"' in link:
                    m = re.search(r'<([^>]+)>;\s*rel="next"', link)
                    if m:
                        url = m.group(1)
                time.sleep(0.3)

            # Find orders needing tags
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

            # Apply tags via GraphQL
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

                # Remove conflicting routing tags first
                if t["conflicts"]:
                    resp = requests.post(gql_url, headers=headers, json={
                        "query": tags_remove_q,
                        "variables": {"id": gid, "tags": t["conflicts"]},
                    }, timeout=30)
                    data = resp.json()
                    errors = (data.get("data", {}).get("tagsRemove", {})
                              .get("userErrors", []))
                    if not errors:
                        results["conflicts_resolved"] += 1
                    time.sleep(0.3)

                # Add FedEx 2Day tag
                if not t["already_tagged"]:
                    resp = requests.post(gql_url, headers=headers, json={
                        "query": tags_add_q,
                        "variables": {"id": gid, "tags": [TAG]},
                    }, timeout=30)
                    data = resp.json()
                    if "errors" in data:
                        results["failed"] += 1
                        results["details"].append({
                            "name": t["name"], "error": str(data["errors"])[:100]})
                    else:
                        ue = (data.get("data", {}).get("tagsAdd", {})
                              .get("userErrors", []))
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
