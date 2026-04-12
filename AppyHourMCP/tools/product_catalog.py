"""
Shopify Product Catalog MCP tools — browse products, variants, and collections.

Uses InventoryReorder's static Admin API token for Shopify API access.
"""

import json
import logging

from pydantic import BaseModel, ConfigDict, Field

from utils import get_shopify_auth, format_error, to_json, shopify_paginate

logger = logging.getLogger("appyhour_mcp.product_catalog")


def _paginated_get(url: str, headers: dict[str, str], params: dict | None = None, limit: int = 250) -> list[dict]:
    """Fetch all pages from a Shopify REST endpoint using Link-header pagination."""
    return shopify_paginate(url, headers, params=params, key="")


def register(mcp: object) -> None:
    """Register product catalog tools on the MCP server."""

    # -----------------------------------------------------------------------
    # Input models
    # -----------------------------------------------------------------------

    class ListProductsInput(BaseModel):
        """Input for listing Shopify products."""
        model_config = ConfigDict(str_strip_whitespace=True)

        product_type: str = Field("", description="Filter by product type (e.g. 'Gift Card', 'Cheese', 'Bundle')")
        status: str = Field("active", description="Product status: active, archived, draft, or empty string for all")
        title_contains: str = Field("", description="Filter products whose title contains this text (case-insensitive)")
        limit: int = Field(50, description="Max number of products to return", ge=1, le=250)

    class GetProductInput(BaseModel):
        """Input for fetching a single product with full details."""
        model_config = ConfigDict(str_strip_whitespace=True)

        product_id: int = Field(..., description="Shopify product ID (numeric)")

    class SearchProductsInput(BaseModel):
        """Input for searching products by title, SKU, or vendor."""
        model_config = ConfigDict(str_strip_whitespace=True)

        query: str = Field(..., description="Search term — matches against title, SKU, vendor, and product type")
        limit: int = Field(25, description="Max number of results to return", ge=1, le=100)

    class ListCollectionsInput(BaseModel):
        """Input for listing Shopify collections."""
        model_config = ConfigDict(str_strip_whitespace=True)

        title_contains: str = Field("", description="Filter collections whose title contains this text")
        limit: int = Field(50, description="Max number of collections to return", ge=1, le=250)

    # -----------------------------------------------------------------------
    # Tools
    # -----------------------------------------------------------------------

    @mcp.tool(
        name="appyhour_list_products",
        annotations={
            "title": "List Shopify Products",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def list_products(params: ListProductsInput) -> str:
        """List Shopify products with optional filters.

        Browse the product catalog by type, status, or title keyword.
        Returns product summary including title, type, status, vendor,
        variant count, and price range.

        Args:
            params: Filters for product_type, status, title_contains, and limit.

        Returns:
            JSON with list of products and total count.
        """
        try:
            base, headers = get_shopify_auth()
            api_params: dict = {"limit": 250}
            if params.status:
                api_params["status"] = params.status
            if params.product_type:
                api_params["product_type"] = params.product_type

            products = _paginated_get(f"{base}/products.json", headers, api_params)

            if params.title_contains:
                needle = params.title_contains.lower()
                products = [p for p in products if needle in p.get("title", "").lower()]

            results = []
            for p in products[:params.limit]:
                variants = p.get("variants", [])
                prices = [float(v.get("price", 0)) for v in variants]
                skus = [v.get("sku", "") for v in variants if v.get("sku")]
                results.append({
                    "id": p["id"],
                    "title": p.get("title", ""),
                    "product_type": p.get("product_type", ""),
                    "vendor": p.get("vendor", ""),
                    "status": p.get("status", ""),
                    "tags": p.get("tags", ""),
                    "variant_count": len(variants),
                    "price_min": min(prices) if prices else 0,
                    "price_max": max(prices) if prices else 0,
                    "skus": skus,
                    "created_at": p.get("created_at", ""),
                    "updated_at": p.get("updated_at", ""),
                })

            return to_json({"products": results, "count": len(results), "total_in_catalog": len(products)})
        except Exception as e:
            logger.exception("list_products failed")
            return format_error(str(e))

    @mcp.tool(
        name="appyhour_get_product",
        annotations={
            "title": "Get Shopify Product Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def get_product(params: GetProductInput) -> str:
        """Get full details for a single Shopify product.

        Returns complete product info including all variants with prices,
        SKUs, inventory quantities, and options.

        Args:
            params: Product ID to look up.

        Returns:
            JSON with full product details and all variants.
        """
        try:
            base, headers = get_shopify_auth()
            resp = requests.get(f"{base}/products/{params.product_id}.json", headers=headers, timeout=30)
            resp.raise_for_status()
            p = resp.json()["product"]

            variants = []
            for v in p.get("variants", []):
                variants.append({
                    "id": v["id"],
                    "title": v.get("title", ""),
                    "price": v.get("price", "0.00"),
                    "sku": v.get("sku", ""),
                    "inventory_quantity": v.get("inventory_quantity", 0),
                    "inventory_policy": v.get("inventory_policy", ""),
                    "requires_shipping": v.get("requires_shipping", True),
                    "taxable": v.get("taxable", True),
                    "weight": v.get("weight", 0),
                    "weight_unit": v.get("weight_unit", ""),
                    "option1": v.get("option1"),
                    "option2": v.get("option2"),
                    "option3": v.get("option3"),
                })

            result = {
                "id": p["id"],
                "title": p.get("title", ""),
                "body_html": p.get("body_html", ""),
                "product_type": p.get("product_type", ""),
                "vendor": p.get("vendor", ""),
                "status": p.get("status", ""),
                "tags": p.get("tags", ""),
                "options": p.get("options", []),
                "variants": variants,
                "created_at": p.get("created_at", ""),
                "updated_at": p.get("updated_at", ""),
                "published_at": p.get("published_at"),
                "image_count": len(p.get("images", [])),
            }

            return to_json(result)
        except Exception as e:
            logger.exception("get_product failed")
            return format_error(str(e))

    @mcp.tool(
        name="appyhour_search_products",
        annotations={
            "title": "Search Shopify Products",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def search_products(params: SearchProductsInput) -> str:
        """Search products by title, SKU, vendor, or product type.

        Performs a text search across all active products. Matches against
        title, variant SKUs, vendor name, and product type.

        Args:
            params: Search query string and result limit.

        Returns:
            JSON with matching products and match details.
        """
        try:
            base, headers = get_shopify_auth()
            all_products = _paginated_get(f"{base}/products.json", headers, {"limit": 250, "status": "active"})

            needle = params.query.lower()
            matches = []
            for p in all_products:
                title = p.get("title", "").lower()
                vendor = p.get("vendor", "").lower()
                ptype = p.get("product_type", "").lower()
                tags = p.get("tags", "").lower()
                variant_skus = [v.get("sku", "").lower() for v in p.get("variants", [])]
                variant_titles = [v.get("title", "").lower() for v in p.get("variants", [])]

                matched_fields = []
                if needle in title:
                    matched_fields.append("title")
                if needle in vendor:
                    matched_fields.append("vendor")
                if needle in ptype:
                    matched_fields.append("product_type")
                if needle in tags:
                    matched_fields.append("tags")
                if any(needle in sku for sku in variant_skus):
                    matched_fields.append("sku")
                if any(needle in vt for vt in variant_titles):
                    matched_fields.append("variant_title")

                if matched_fields:
                    variants = p.get("variants", [])
                    prices = [float(v.get("price", 0)) for v in variants]
                    matches.append({
                        "id": p["id"],
                        "title": p.get("title", ""),
                        "product_type": p.get("product_type", ""),
                        "vendor": p.get("vendor", ""),
                        "tags": p.get("tags", ""),
                        "variant_count": len(variants),
                        "price_min": min(prices) if prices else 0,
                        "price_max": max(prices) if prices else 0,
                        "skus": [v.get("sku", "") for v in variants if v.get("sku")],
                        "matched_on": matched_fields,
                    })

                if len(matches) >= params.limit:
                    break

            return to_json({"results": matches, "count": len(matches), "query": params.query})
        except Exception as e:
            logger.exception("search_products failed")
            return format_error(str(e))

    @mcp.tool(
        name="appyhour_list_collections",
        annotations={
            "title": "List Shopify Collections",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def list_collections(params: ListCollectionsInput) -> str:
        """List Shopify custom and smart collections.

        Returns collection titles, product counts, and publication status.
        Useful for understanding how products are organized in the store.

        Args:
            params: Optional title filter and result limit.

        Returns:
            JSON with list of collections.
        """
        try:
            base, headers = get_shopify_auth()

            custom = _paginated_get(f"{base}/custom_collections.json", headers, {"limit": 250})
            smart = _paginated_get(f"{base}/smart_collections.json", headers, {"limit": 250})

            all_collections = []
            for c in custom + smart:
                all_collections.append({
                    "id": c["id"],
                    "title": c.get("title", ""),
                    "body_html": c.get("body_html", "")[:200] if c.get("body_html") else "",
                    "sort_order": c.get("sort_order", ""),
                    "published_at": c.get("published_at"),
                    "updated_at": c.get("updated_at", ""),
                    "type": "smart" if c.get("rules") else "custom",
                })

            if params.title_contains:
                needle = params.title_contains.lower()
                all_collections = [c for c in all_collections if needle in c["title"].lower()]

            return to_json({
                "collections": all_collections[:params.limit],
                "count": len(all_collections[:params.limit]),
            })
        except Exception as e:
            logger.exception("list_collections failed")
            return format_error(str(e))
