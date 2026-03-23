"""
Shopify Order MCP tools — fetch orders, batch thermal analysis, tag updates.

Uses InventoryReorder's static Admin API token for all Shopify API access
(broader scopes than GelPackCalculator's OAuth client-credentials flow).
"""

import json
import logging
import re
import time
from typing import Optional, List

logger = logging.getLogger("appyhour_mcp.shopify")
from pydantic import BaseModel, Field, ConfigDict

import requests

from utils import get_gelcalc_settings, get_shopify_auth, format_error, to_json


def _fetch_unfulfilled_orders(base, headers, tag=None, fields="id,name,tags,shipping_address,line_items,customer,email"):
    """Fetch all unfulfilled orders with pagination. Optional tag filter."""
    all_orders = []
    url = f"{base}/orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": fields,
    }
    if tag:
        params["tag"] = tag
    page = 0
    while url:
        page += 1
        resp = requests.get(url, headers=headers,
                            params=params if page == 1 else None, timeout=30)
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
        all_orders.extend(orders)
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
        time.sleep(0.1)
    return all_orders


def _graphql(base, headers, query, variables=None):
    """Execute a Shopify GraphQL query."""
    url = f"{base}/graphql.json"
    body = {"query": query}
    if variables:
        body["variables"] = variables
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def register(mcp):
    """Register Shopify tools on the MCP server."""

    # -----------------------------------------------------------------------
    # Input models
    # -----------------------------------------------------------------------

    class FetchOrdersInput(BaseModel):
        """Input for fetching Shopify orders by tag filters."""
        model_config = ConfigDict(str_strip_whitespace=True)

        and_tags: List[str] = Field(default_factory=list, description="Tags that ALL must be present on the order")
        or_tags: List[str] = Field(default_factory=list, description="At least ONE of these tags must be present")
        exclude_tags: List[str] = Field(default_factory=list, description="Orders with any of these tags are excluded")
        limit: int = Field(50, description="Max number of orders to return", ge=1, le=250)

    class AnalyzeOrdersInput(BaseModel):
        """Input for fetching and thermally analyzing Shopify orders."""
        model_config = ConfigDict(str_strip_whitespace=True)

        and_tags: List[str] = Field(default_factory=list, description="Tags that ALL must be present on the order")
        or_tags: List[str] = Field(default_factory=list, description="At least ONE of these tags must be present")
        exclude_tags: List[str] = Field(default_factory=list, description="Orders with any of these tags are excluded")
        limit: int = Field(50, description="Max number of orders to analyze", ge=1, le=250)

    class UpdateTagsInput(BaseModel):
        """Input for adding/removing tags on a Shopify order."""
        model_config = ConfigDict(str_strip_whitespace=True)

        order_id: int = Field(..., description="Shopify order ID (numeric)")
        add_tags: List[str] = Field(default_factory=list, description="Tags to add to the order")
        remove_tags: List[str] = Field(default_factory=list, description="Tags to remove from the order")

    # -----------------------------------------------------------------------
    # Tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="appyhour_fetch_orders",
        annotations={
            "title": "Fetch Shopify Orders",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def fetch_orders(params: FetchOrdersInput) -> str:
        """Fetch unfulfilled Shopify orders filtered by tags.

        Returns order details including customer info, shipping address,
        current gel pack tags, and routing tags. Use this to see which
        orders need thermal analysis or tag updates.

        Args:
            params: Tag filters (and_tags, or_tags, exclude_tags) and result limit.

        Returns:
            JSON with list of orders (id, name, customer, city, state, zip, tags)
            and total count.
        """
        try:
            from gel_pack_shopify import GEL_TAG_SET, is_routing_tag

            base, headers = get_shopify_auth()
            all_orders = _fetch_unfulfilled_orders(
                base, headers,
                fields="id,name,tags,shipping_address,customer,email",
            )

            # Apply tag filters
            results = []
            for o in all_orders:
                tags = [t.strip() for t in o.get("tags", "").split(",") if t.strip()]
                if params.and_tags and not all(t in tags for t in params.and_tags):
                    continue
                if params.or_tags and not any(t in tags for t in params.or_tags):
                    continue
                if params.exclude_tags and any(t in tags for t in params.exclude_tags):
                    continue

                addr = o.get("shipping_address", {})
                results.append({
                    "id": o.get("id"),
                    "name": o.get("name", ""),
                    "customer": f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip(),
                    "city": addr.get("city", ""),
                    "state": addr.get("province_code", ""),
                    "zip": addr.get("zip", ""),
                    "tags": tags,
                    "gel_tags": [t for t in tags if t in GEL_TAG_SET],
                    "routing_tags": [t for t in tags if is_routing_tag(t)],
                    "created_at": o.get("created_at", ""),
                })
                if len(results) >= params.limit:
                    break

            return to_json({"orders": results, "count": len(results)})
        except Exception as e:
            return format_error(e, "fetch_orders")

    @mcp.tool(
        name="appyhour_analyze_orders",
        annotations={
            "title": "Analyze Orders Thermal Needs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def analyze_orders(params: AnalyzeOrdersInput) -> str:
        """Fetch Shopify orders and run thermal analysis on each.

        For every matching order, fetches the destination weather forecast,
        calculates heat gain during transit, and recommends the gel pack
        configuration. Returns a table of results including risk levels.

        Note: This makes external API calls (Shopify + OpenWeatherMap) for each
        order, so larger batches will take longer.

        Args:
            params: Tag filters and limit for order selection.

        Returns:
            JSON with analyzed orders including: order name, customer, destination,
            temperature, transit type, recommended gel config, margin, cost, risk.
        """
        try:
            from gel_pack_shopify import (
                analyze_order, calc_surface_area, calc_r_total,
                fetch_weather_by_zip, get_transit_type, state_from_code,
                is_routing_tag, GEL_TAG_SET, MELT_EFFICIENCY,
                DEFAULT_R_PER_INCH, DEFAULT_THICKNESS, DEFAULT_R_AIR_FILM,
                DEFAULT_BOX_L, DEFAULT_BOX_W, DEFAULT_BOX_H,
                TARGET_TEMP_DEFAULT, SAFETY_FACTOR_DEFAULT,
            )

            base, headers = get_shopify_auth()
            s = get_gelcalc_settings()

            all_orders = _fetch_unfulfilled_orders(
                base, headers,
                fields="id,name,tags,shipping_address,customer,email",
            )

            # Apply tag filters
            filtered = []
            for o in all_orders:
                tags = [t.strip() for t in o.get("tags", "").split(",") if t.strip()]
                if params.and_tags and not all(t in tags for t in params.and_tags):
                    continue
                if params.or_tags and not any(t in tags for t in params.or_tags):
                    continue
                if params.exclude_tags and any(t in tags for t in params.exclude_tags):
                    continue
                filtered.append(o)
                if len(filtered) >= params.limit:
                    break

            surface_area = calc_surface_area(
                float(s.get("box_length", DEFAULT_BOX_L)),
                float(s.get("box_width", DEFAULT_BOX_W)),
                float(s.get("box_height", DEFAULT_BOX_H)),
            )
            r_total = calc_r_total(
                float(s.get("r_per_inch", DEFAULT_R_PER_INCH)),
                float(s.get("insulation_thickness", DEFAULT_THICKNESS)),
                float(s.get("r_air_film", DEFAULT_R_AIR_FILM)),
            )

            api_key = s.get("owm_api_key", "")
            results = []

            for order in filtered:
                try:
                    addr = order.get("shipping_address", {})
                    zip_code = addr.get("zip", "")
                    state_code = addr.get("province_code", "")
                    state_name = state_from_code(state_code)
                    transit_type = get_transit_type(state_name) if state_name else "3-Day"

                    avg_temp, peak_temp = 75.0, 80.0
                    weather_error = False
                    if api_key and zip_code:
                        try:
                            forecasts, _, _ = fetch_weather_by_zip(api_key, zip_code)
                            if forecasts:
                                temps = [t for _, t in forecasts]
                                avg_temp = sum(temps) / len(temps)
                                peak_temp = max(temps)
                        except Exception as wx:
                            logger.warning("Weather fetch failed for %s: %s", zip_code, wx)
                            weather_error = True

                    result = analyze_order(
                        outside_temp=avg_temp,
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

                    tags = [t.strip() for t in order.get("tags", "").split(",") if t.strip()]
                    effective_btu = result["config_btu"] * MELT_EFFICIENCY
                    cost = round(
                        result["config_48oz"] * float(s.get("gel_48oz_cost", 1.50))
                        + result["config_24oz"] * float(s.get("gel_24oz_cost", 0.85)),
                        2,
                    )

                    results.append({
                        "order": order.get("name", ""),
                        "order_id": order.get("id"),
                        "customer": f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip(),
                        "city": addr.get("city", ""),
                        "state": state_code,
                        "zip": zip_code,
                        "avg_temp_f": round(avg_temp, 1),
                        "peak_temp_f": round(peak_temp, 1),
                        "transit_type": transit_type,
                        "config": result["config_name"],
                        "packs_48oz": result["config_48oz"],
                        "packs_24oz": result["config_24oz"],
                        "margin_btu": round(effective_btu - result["total_q_safe"], 0),
                        "cost": cost,
                        "risk": result["risk"],
                        "gel_tags": result["config_tags"],
                        "current_gel_tags": [t for t in tags if t in GEL_TAG_SET],
                        "weather_estimated": weather_error,
                    })
                except Exception as e:
                    results.append({
                        "order": order.get("name", "?"),
                        "error": str(e),
                    })

            return to_json({"orders": results, "count": len(results)})
        except Exception as e:
            return format_error(e, "analyze_orders")

    @mcp.tool(
        name="appyhour_update_order_tags",
        annotations={
            "title": "Update Shopify Order Tags",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def update_order_tags(params: UpdateTagsInput) -> str:
        """Add or remove tags on a Shopify order.

        Modifies the tags on a single order. Use this to apply gel pack tags
        (e.g. '!ExtraGel48oz!'), routing tags, or weather hold tags after
        running analysis.

        WARNING: This writes to your Shopify store. Double-check the order_id
        and tags before calling.

        Args:
            params: Order ID, tags to add, and tags to remove.

        Returns:
            JSON with the order_id and the final tag list after modification.
        """
        try:
            base, headers = get_shopify_auth()
            gid = f"gid://shopify/Order/{params.order_id}"

            # Get current tags
            resp = requests.get(
                f"{base}/orders/{params.order_id}.json",
                headers=headers,
                params={"fields": "id,tags"},
                timeout=30,
            )
            resp.raise_for_status()
            order = resp.json().get("order", {})
            current = [t.strip() for t in order.get("tags", "").split(",") if t.strip()]

            new_tags = [t for t in current if t not in params.remove_tags]
            for t in params.add_tags:
                if t not in new_tags:
                    new_tags.append(t)

            # Update via REST PUT
            resp = requests.put(
                f"{base}/orders/{params.order_id}.json",
                headers=headers,
                json={"order": {"id": params.order_id, "tags": ", ".join(new_tags)}},
                timeout=30,
            )
            resp.raise_for_status()

            return to_json({"order_id": params.order_id, "tags": new_tags})
        except Exception as e:
            return format_error(e, "update_order_tags")
