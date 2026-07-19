"""Classify each reshipped order's BOX TYPE from its Shopify line-item SKUs.

Box taxonomy (per appyhour-business-rules / elevate-ops):
  * AHB-MCUST-*  (SKU contains 'MCUST')  -> "Medium Tray"
  * AHB-LCUST-*  (SKU contains 'LCUST')  -> "Large Tray"
  * anything else (AHB-MED / AHB-LGE / monthly / one-time) -> "Regular Box"

WHY live Shopify: shipping.db carries no per-line SKUs (fulfillments/shopify_orders
have counts only), and a reship ticket can reference ANY past cohort — so we can't
pull a single _SHIP_ tag. We look the exact order numbers up by name, in batches.

CLI-only (no MCP) so the scheduled weekly task can call it — MCP is unreliable in
scheduled runs. Auth via the canonical get_shopify_auth().
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

APPYHOUR_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APPYHOUR_REPO))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(APPYHOUR_REPO / ".env")
import requests  # noqa: E402

from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

BASE_URL, HEADERS = get_shopify_auth()
BASE_URL = BASE_URL.replace(".myshopify.com.myshopify.com", ".myshopify.com")
GRAPHQL_URL = f"{BASE_URL}/graphql.json"

# canonical box-type buckets (order = report/display order)
BOX_ORDER = ["Regular Box", "Medium Tray", "Large Tray"]

_QUERY = """
query Q($cursor: String, $q: String!) {
  orders(first: 100, after: $cursor, query: $q) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      name
      lineItems(first: 50) { edges { node { sku } } }
    } }
  }
}
"""


def classify_skus(skus: list[str]) -> str:
    """LCUST -> Large Tray, MCUST -> Medium Tray, else Regular Box.
    Large checked first so a box with both custom markers reads as the larger."""
    up = [(s or "").upper() for s in skus]
    if any("LCUST" in s for s in up):
        return "Large Tray"
    if any("MCUST" in s for s in up):
        return "Medium Tray"
    return "Regular Box"


def _post(variables: dict) -> dict:
    for _ in range(4):
        r = requests.post(GRAPHQL_URL, headers=HEADERS,
                          json={"query": _QUERY, "variables": variables}, timeout=60)
        if r.status_code == 429:
            time.sleep(2)
            continue
        r.raise_for_status()
        body = r.json()
        if "errors" in body:
            raise RuntimeError(body["errors"])
        avail = (body.get("extensions", {}).get("cost", {})
                 .get("throttleStatus", {}).get("currentlyAvailable", 1000))
        if avail < 200:
            time.sleep(1.0)
        return body
    raise RuntimeError("Shopify rate-limited (boxtype)")


def box_types_for(order_numbers: list[int], chunk: int = 30) -> dict[int, str]:
    """Return {order_number: box_type} for the given Shopify order numbers.
    Unfound orders are omitted (caller buckets them as 'unknown')."""
    nums = sorted({n for n in order_numbers if n is not None})
    out: dict[int, str] = {}
    for i in range(0, len(nums), chunk):
        batch = nums[i:i + chunk]
        q = " OR ".join(f"name:#{n}" for n in batch)
        cursor = None
        while True:
            body = _post({"cursor": cursor, "q": q})
            conn = body["data"]["orders"]
            for edge in conn["edges"]:
                o = edge["node"]
                try:
                    onum = int((o["name"] or "").lstrip("#"))
                except ValueError:
                    continue
                skus = [li["node"].get("sku") for li in o["lineItems"]["edges"]]
                out[onum] = classify_skus(skus)
            if not conn["pageInfo"]["hasNextPage"]:
                break
            cursor = conn["pageInfo"]["endCursor"]
            time.sleep(0.1)
        time.sleep(0.1)
    return out
