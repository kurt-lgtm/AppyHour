# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""
Fetch all Shopify products and build a SKU -> product title JSON database.
Includes active, archived, and draft products.
Uses Link-header pagination to get all pages.
"""

import json
import os
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Load Shopify credentials from InventoryReorder settings
# ---------------------------------------------------------------------------

INVENTORY_DIR = Path(__file__).resolve().parent.parent.parent / "InventoryReorder"
SETTINGS_FILE = "inventory_reorder_settings.json"


def load_inventory_settings() -> dict:
    """Load settings from InventoryReorder directory."""
    # Check dist/ first, then root
    for subdir in ["dist", "."]:
        path = INVENTORY_DIR / subdir / SETTINGS_FILE
        if path.exists():
            with open(path) as f:
                return json.load(f)
    raise FileNotFoundError(
        f"Could not find {SETTINGS_FILE} in {INVENTORY_DIR} or {INVENTORY_DIR / 'dist'}"
    )


# Re-exported from appyhour.credentials (single source of truth).
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))
from appyhour.credentials import get_shopify_auth  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Fetch products with Link-header pagination
# ---------------------------------------------------------------------------

VALID_PREFIXES = ("CH-", "MT-", "AC-", "AHB-", "PR-", "CEX-", "PK-", "TR-", "EX-", "BL-", "SN-")


def fetch_all_products(base_url: str, headers: dict[str, str]) -> list[dict]:
    """Fetch all products across all statuses using Link-header pagination."""
    all_products: list[dict] = []
    for status in ["active", "archived", "draft"]:
        url: str | None = f"{base_url}/products.json?status={status}&limit=250"
        page = 0
        while url:
            page += 1
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            products = data.get("products", [])
            all_products.extend(products)
            print(f"  [{status}] page {page}: {len(products)} products", file=sys.stderr)

            # Link-header pagination
            link = resp.headers.get("Link", "")
            url = None
            if 'rel="next"' in link:
                for part in link.split(","):
                    if 'rel="next"' in part:
                        url = part.split("<")[1].split(">")[0]
                        break
    return all_products


def build_sku_database(products: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Extract SKU -> title map from products. Returns (sku_map, warnings)."""
    sku_map: dict[str, str] = {}
    warnings: list[str] = []

    for product in products:
        title = product.get("title", "").strip()
        for variant in product.get("variants", []):
            sku = (variant.get("sku") or "").strip()
            if not sku:
                continue
            if not sku.startswith(VALID_PREFIXES):
                continue
            if not title:
                warnings.append(f"SKU {sku} has empty product title")
                continue
            if sku in sku_map and sku_map[sku] != title:
                warnings.append(f"SKU {sku} duplicate: '{sku_map[sku]}' vs '{title}'")
            sku_map[sku] = title

    # Sort by SKU
    sorted_map = dict(sorted(sku_map.items()))
    return sorted_map, warnings


def main() -> None:
    print("Loading Shopify credentials...", file=sys.stderr)
    base_url, headers = get_shopify_auth()

    print("Fetching all products...", file=sys.stderr)
    products = fetch_all_products(base_url, headers)
    print(f"Total products fetched: {len(products)}", file=sys.stderr)

    print("Building SKU database...", file=sys.stderr)
    sku_map, warnings = build_sku_database(products)

    # Write JSON file
    output_path = Path(__file__).resolve().parent / "sku_database.json"
    with open(output_path, "w") as f:
        json.dump(sku_map, f, indent=2)
    print(f"\nWrote {len(sku_map)} SKUs to {output_path}", file=sys.stderr)

    # Summary by prefix
    prefix_counts: dict[str, int] = {}
    for sku in sku_map:
        prefix = sku.split("-")[0] + "-"
        prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

    print("\n=== SKU Count by Prefix ===", file=sys.stderr)
    for prefix in sorted(prefix_counts):
        print(f"  {prefix:6s} {prefix_counts[prefix]:4d}", file=sys.stderr)
    print(f"  {'TOTAL':6s} {len(sku_map):4d}", file=sys.stderr)

    if warnings:
        print(f"\n=== Warnings ({len(warnings)}) ===", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
    else:
        print("\nNo warnings.", file=sys.stderr)


if __name__ == "__main__":
    main()
