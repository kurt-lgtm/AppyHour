"""
Tuesday Cut Order Generator — Agent SDK Automation

Generates the weekly production cut order for RMFG.
Pulls demand from Recharge queued charges + Shopify unfulfilled orders,
resolves PR-CJAM and CEX-EC per curation, includes MONG first-order
projection, cross-checks demand, generates XLSX, and uploads to Drive.

Due: TUESDAY (not Wednesday — updated schedule).

Key logic:
  - PR-CJAM: one cheese per curation, unique across curations
  - CEX-EC: ~40% of boxes, can split across 2 cheeses by percentage
  - MONG projection: 3-day rolling average of first orders
  - Cross-check: PR-CJAM count vs recipe cheese demand (flag >10% discrepancy)

Usage:
  python tuesday_cut_order.py                    # generate and upload
  python tuesday_cut_order.py --no-upload        # generate only, skip Drive upload
  python tuesday_cut_order.py --no-projection    # skip MONG first-order projection
  python tuesday_cut_order.py --output cut.xlsx  # custom output filename
"""

import argparse
import asyncio
import io
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

FULFILLMENT_API = "http://localhost:5187"

# Google Drive upload
TOKEN_PATH = Path("C:/Users/Work/Claude Projects/AppyHour/InventoryReorder/dist/drive_oauth_token.json")
DRIVE_FOLDER_ID = "1TgvxK10tFAPJqhkYw-6u1Umnvp9wMJ3I"

# SKU prefixes
FOOD_PREFIXES = ("CH-", "MT-", "AC-")
SKIP_PREFIXES = ("AHB-", "BL-", "PK-", "TR-", "EX-", "PR-CJAM", "CEX-E", "CEX-EM")

# Known curations
KNOWN_CURATIONS = frozenset({
    "MONG", "MDT", "OWC", "SPN", "ALPN", "ALPT",
    "ISUN", "HHIGH", "NMS", "BYO", "SS", "GEN", "MS",
})

# SKU normalization
SKU_NORMALIZE = {"CH-BRIE": "CH-EBRIE"}

# Cross-check tolerance
DISCREPANCY_THRESHOLD = 0.10  # 10%


@dataclass(frozen=True, slots=True)
class DemandLine:
    sku: str
    quantity: int
    source: str  # "recharge", "shopify", "projection"


@dataclass(frozen=True, slots=True)
class CurationDemand:
    curation: str
    box_count: int
    pr_cjam_cheese: str
    pr_cjam_qty: int
    cex_ec_assignments: tuple[tuple[str, int], ...]  # (sku, qty) pairs


@dataclass(frozen=True, slots=True)
class Discrepancy:
    curation: str
    pr_cjam_count: int
    recipe_cheese_demand: int
    delta_pct: float
    detail: str


@dataclass(frozen=True, slots=True)
class CutOrderSummary:
    total_boxes: int
    curations: tuple[CurationDemand, ...]
    sku_totals: dict[str, int]
    shortages: tuple[tuple[str, int, int], ...]  # (sku, demand, available)
    discrepancies: tuple[Discrepancy, ...]
    mong_projection: int
    output_path: str


# ---------------------------------------------------------------------------
# Data fetching via Agent SDK
# ---------------------------------------------------------------------------

async def _agent_query(prompt: str, tools: list[str], max_turns: int = 15) -> str:
    """Run an agent query and return the concatenated text output."""
    result_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=tools,
            permission_mode="acceptEdits",
            max_turns=max_turns,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    result_text += block.text
    return result_text


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON object from agent response text."""
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        # Try to find a JSON array
        try:
            start = text.index("[")
            end = text.rindex("]") + 1
            return {"data": json.loads(text[start:end])}
        except (ValueError, json.JSONDecodeError):
            return {}


async def fetch_recharge_demand() -> dict[str, Any]:
    """Get Recharge queued charges demand via MCP tools."""
    print("  Fetching Recharge queued charges...")
    result = await _agent_query(
        prompt=(
            "Use appyhour_get_upcoming_charges to get queued Recharge charges. "
            "Then use appyhour_get_subscription_demand to get demand by curation. "
            "Return JSON with:\n"
            '{"charges_count": N, "curations": {"MONG": {"box_count": N, '
            '"skus": {"CH-BRZ": N, ...}}, ...}, "pr_cjam": {"MONG": {"sku": "CH-BLR", "qty": N}, ...}, '
            '"cex_ec": {"MONG": {"assignments": [["CH-XXX", N]], "total": N}, ...}}'
        ),
        tools=[
            "mcp__appyhour__appyhour_get_upcoming_charges",
            "mcp__appyhour__appyhour_get_subscription_demand",
            "mcp__appyhour__get_cut_order_config",
        ],
        max_turns=15,
    )
    return _extract_json(result)


async def fetch_shopify_demand(ship_tag: str) -> dict[str, Any]:
    """Get Shopify unfulfilled order demand."""
    print("  Fetching Shopify unfulfilled orders...")
    result = await _agent_query(
        prompt=(
            f"Use appyhour_fetch_orders to get unfulfilled orders tagged '{ship_tag}'. "
            "Count food SKUs (CH-/MT-/AC- only) across all orders. "
            "Group by curation (resolve from AHB-* box SKU). "
            "Return JSON with:\n"
            '{"order_count": N, "curations": {"MONG": {"box_count": N, '
            '"skus": {"CH-BRZ": N, ...}}, ...}}'
        ),
        tools=[
            "mcp__appyhour__appyhour_fetch_orders",
            "mcp__appyhour__appyhour_analyze_orders",
        ],
        max_turns=15,
    )
    return _extract_json(result)


async def fetch_mong_projection() -> dict[str, Any]:
    """Get MONG first-order projection from fulfillment web API."""
    print("  Fetching MONG first-order projection...")
    result = await _agent_query(
        prompt=(
            f"Make an HTTP GET to {FULFILLMENT_API}/api/tuesday_projection "
            "and extract the MONG first-order projection data. "
            "Return JSON with:\n"
            '{"projected_additional": N, "daily_rate": N, '
            '"pr_cjam_mong_additional": N, "cex_ec_mong_additional": N}'
        ),
        tools=["Bash"],
        max_turns=5,
    )
    return _extract_json(result)


async def fetch_inventory() -> dict[str, int]:
    """Get current inventory from fulfillment web API."""
    print("  Fetching current inventory...")
    result = await _agent_query(
        prompt=(
            f"Make an HTTP GET to {FULFILLMENT_API}/api/calculated_inventory "
            "and return the inventory data as JSON: "
            '{"CH-BRZ": 150, "CH-MCPC": 200, ...} (only CH-/MT-/AC- SKUs)'
        ),
        tools=["Bash", "mcp__appyhour__get_inventory_snapshot"],
        max_turns=5,
    )
    return _extract_json(result)


# ---------------------------------------------------------------------------
# Demand aggregation and cross-checks
# ---------------------------------------------------------------------------

def _normalize_sku(sku: str) -> str:
    """Apply SKU normalization rules."""
    return SKU_NORMALIZE.get(sku, sku)


def _merge_demand(
    recharge: dict[str, Any],
    shopify: dict[str, Any],
    projection: dict[str, Any],
    include_projection: bool,
) -> dict[str, int]:
    """Merge demand from all sources into total SKU quantities."""
    totals: dict[str, float] = defaultdict(float)

    # Recharge demand (primary source)
    for curation, data in recharge.get("curations", {}).items():
        if isinstance(data, dict):
            for sku, qty in data.get("skus", {}).items():
                totals[_normalize_sku(sku)] += qty

    # PR-CJAM demand from Recharge
    for curation, data in recharge.get("pr_cjam", {}).items():
        if isinstance(data, dict):
            sku = data.get("sku", "")
            qty = data.get("qty", 0)
            if sku:
                totals[_normalize_sku(sku)] += qty

    # CEX-EC demand from Recharge
    for curation, data in recharge.get("cex_ec", {}).items():
        if isinstance(data, dict):
            for assignment in data.get("assignments", []):
                if len(assignment) >= 2:
                    totals[_normalize_sku(assignment[0])] += assignment[1]

    # Shopify demand (only MONG gets Shopify contribution to avoid double-counting)
    mong_shopify = shopify.get("curations", {}).get("MONG", {})
    if isinstance(mong_shopify, dict):
        for sku, qty in mong_shopify.get("skus", {}).items():
            totals[_normalize_sku(sku)] += qty

    # MONG first-order projection
    if include_projection:
        proj_additional = projection.get("projected_additional", 0)
        pr_cjam_add = projection.get("pr_cjam_mong_additional", 0)
        cex_ec_add = projection.get("cex_ec_mong_additional", 0)

        # Add projected MONG recipe items
        # (projection count added proportionally to existing MONG recipe)
        if proj_additional > 0:
            mong_skus = recharge.get("curations", {}).get("MONG", {}).get("skus", {})
            if mong_skus:
                for sku in mong_skus:
                    totals[_normalize_sku(sku)] += proj_additional

        # PR-CJAM-MONG additional
        pr_cjam_mong_sku = recharge.get("pr_cjam", {}).get("MONG", {}).get("sku", "")
        if pr_cjam_mong_sku and pr_cjam_add:
            totals[_normalize_sku(pr_cjam_mong_sku)] += pr_cjam_add

        # CEX-EC-MONG additional
        cex_ec_mong = recharge.get("cex_ec", {}).get("MONG", {}).get("assignments", [])
        if cex_ec_mong and cex_ec_add:
            # Distribute proportionally across CEX-EC assignments
            total_cex = sum(a[1] for a in cex_ec_mong if len(a) >= 2)
            if total_cex > 0:
                for assignment in cex_ec_mong:
                    if len(assignment) >= 2:
                        ratio = assignment[1] / total_cex
                        totals[_normalize_sku(assignment[0])] += round(cex_ec_add * ratio)

    # Round all to int at the end (CEX-EC splits use float)
    return {sku: round(qty) for sku, qty in totals.items() if qty > 0}


def _cross_check_pr_cjam(
    recharge: dict[str, Any],
    sku_totals: dict[str, int],
) -> list[Discrepancy]:
    """Verify PR-CJAM counts vs recipe cheese demand."""
    discrepancies: list[Discrepancy] = []

    for curation, data in recharge.get("pr_cjam", {}).items():
        if not isinstance(data, dict):
            continue
        pr_cjam_sku = data.get("sku", "")
        pr_cjam_qty = data.get("qty", 0)
        if not pr_cjam_sku or pr_cjam_qty == 0:
            continue

        # Get recipe cheese demand for this curation
        curation_data = recharge.get("curations", {}).get(curation, {})
        if not isinstance(curation_data, dict):
            continue
        box_count = curation_data.get("box_count", 0)

        # PR-CJAM count should approximately equal box count
        if box_count > 0:
            delta = abs(pr_cjam_qty - box_count)
            delta_pct = delta / box_count
            if delta_pct > DISCREPANCY_THRESHOLD:
                discrepancies.append(Discrepancy(
                    curation=curation,
                    pr_cjam_count=pr_cjam_qty,
                    recipe_cheese_demand=box_count,
                    delta_pct=delta_pct,
                    detail=(
                        f"PR-CJAM-{curation} ({pr_cjam_sku}): {pr_cjam_qty} vs "
                        f"{box_count} boxes ({delta_pct:.0%} off)"
                    ),
                ))

    return discrepancies


def _find_shortages(
    sku_totals: dict[str, int],
    inventory: dict[str, int],
) -> list[tuple[str, int, int]]:
    """Find SKUs where demand exceeds available inventory."""
    shortages: list[tuple[str, int, int]] = []
    for sku, demand in sorted(sku_totals.items()):
        available = inventory.get(sku, 0)
        if demand > available:
            shortages.append((sku, demand, available))
    return shortages


# ---------------------------------------------------------------------------
# XLSX generation
# ---------------------------------------------------------------------------

async def generate_xlsx(
    sku_totals: dict[str, int],
    inventory: dict[str, int],
    recharge: dict[str, Any],
    shortages: list[tuple[str, int, int]],
    discrepancies: list[Discrepancy],
    output_path: str,
) -> str:
    """Generate the cut order XLSX via agent (uses openpyxl)."""
    print("  Generating XLSX...")

    # Prepare data for the agent
    data_payload = json.dumps({
        "sku_totals": sku_totals,
        "inventory": inventory,
        "shortages": [(s[0], s[1], s[2]) for s in shortages],
        "curations": {
            cur: {
                "box_count": d.get("box_count", 0) if isinstance(d, dict) else 0,
            }
            for cur, d in recharge.get("curations", {}).items()
        },
        "discrepancies": [
            {"curation": d.curation, "detail": d.detail}
            for d in discrepancies
        ],
    }, indent=2)

    result = await _agent_query(
        prompt=(
            f"Generate a cut order XLSX file at '{output_path}' using openpyxl.\n\n"
            "Create these sheets:\n\n"
            "1. 'Cut Order' sheet:\n"
            "   - Columns: SKU, Demand, On Hand, Shortage, Action Needed\n"
            "   - Sort by SKU\n"
            "   - Highlight shortages in red\n"
            "   - Bold headers, auto-width columns\n\n"
            "2. 'By Curation' sheet:\n"
            "   - Columns: Curation, Box Count, PR-CJAM SKU, PR-CJAM Qty, CEX-EC Total\n"
            "   - One row per curation\n\n"
            "3. 'Discrepancies' sheet (if any):\n"
            "   - Columns: Curation, PR-CJAM Count, Box Count, Delta %, Detail\n\n"
            f"Data:\n{data_payload}\n\n"
            f"Save to: {output_path}"
        ),
        tools=["Bash", "Write"],
        max_turns=10,
    )

    return output_path


# ---------------------------------------------------------------------------
# Google Drive upload
# ---------------------------------------------------------------------------

async def upload_to_drive(file_path: str) -> str | None:
    """Upload XLSX to Google Drive using OAuth token."""
    if not TOKEN_PATH.exists():
        print(f"  WARNING: OAuth token not found at {TOKEN_PATH}")
        print("  Skipping Drive upload.")
        return None

    print("  Uploading to Google Drive...")
    result = await _agent_query(
        prompt=(
            f"Upload the file '{file_path}' to Google Drive.\n\n"
            "Use this exact pattern:\n"
            "```python\n"
            "import json, io\n"
            "from google.auth.transport.requests import Request\n"
            "from google.oauth2.credentials import Credentials\n"
            "from googleapiclient.discovery import build\n"
            "from googleapiclient.http import MediaFileUpload\n\n"
            f"TOKEN_PATH = r'{TOKEN_PATH}'\n"
            f"FOLDER_ID = '{DRIVE_FOLDER_ID}'\n\n"
            "with open(TOKEN_PATH) as f:\n"
            "    td = json.load(f)\n"
            "creds = Credentials(token=td['token'], refresh_token=td['refresh_token'],\n"
            "    token_uri=td['token_uri'], client_id=td['client_id'],\n"
            "    client_secret=td['client_secret'], scopes=td['scopes'])\n"
            "if creds.expired:\n"
            "    creds.refresh(Request())\n"
            "    td['token'] = creds.token\n"
            "    with open(TOKEN_PATH, 'w') as f:\n"
            "        json.dump(td, f, indent=2)\n\n"
            "drive = build('drive', 'v3', credentials=creds)\n"
            f"media = MediaFileUpload(r'{file_path}', "
            "mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')\n"
            "file_metadata = {'name': '" + Path(file_path).name + "', "
            "'parents': [FOLDER_ID]}\n"
            "result = drive.files().create(body=file_metadata, media_body=media, "
            "fields='id,webViewLink', supportsAllDrives=True).execute()\n"
            "print(f'Uploaded: {result.get(\"webViewLink\", result.get(\"id\"))}')\n"
            "```\n"
            "Run this Python code and report the result."
        ),
        tools=["Bash"],
        max_turns=5,
    )

    # Try to extract the Drive link from the result
    for line in result.split("\n"):
        if "drive.google.com" in line or "Uploaded:" in line:
            return line.strip()

    return "Upload attempted (check Drive folder)"


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    """Main entry point."""
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")

    # Compute ship tag for this week's Tuesday
    ship_tag = args.ship_tag or f"_SHIP_{date_str}"

    output_path = args.output or str(
        Path("C:/Users/Work/Claude Projects/AppyHour")
        / f"AHB_WeeklyProductionQuery_{today.strftime('%m-%d-%y')}_vF.xlsx"
    )

    print("=" * 60)
    print("Tuesday Cut Order Generator")
    print(f"Date: {date_str}")
    print(f"Ship tag: {ship_tag}")
    print(f"Output: {output_path}")
    print(f"MONG projection: {'enabled' if not args.no_projection else 'disabled'}")
    print(f"Drive upload: {'enabled' if not args.no_upload else 'disabled'}")
    print("=" * 60)

    # Step 1: Fetch demand from all sources
    print("\n[1/5] Fetching demand data...")
    recharge_data, shopify_data, inventory_data = await asyncio.gather(
        fetch_recharge_demand(),
        fetch_shopify_demand(ship_tag),
        fetch_inventory(),
    )

    projection_data: dict[str, Any] = {}
    if not args.no_projection:
        projection_data = await fetch_mong_projection()
        mong_proj = projection_data.get("projected_additional", 0)
        print(f"  MONG projection: +{mong_proj} additional first orders")

    # Step 2: Merge demand
    print("\n[2/5] Aggregating demand...")
    sku_totals = _merge_demand(
        recharge_data, shopify_data, projection_data,
        include_projection=not args.no_projection,
    )

    recharge_charges = recharge_data.get("charges_count", "?")
    shopify_orders = shopify_data.get("order_count", "?")
    print(f"  Recharge charges: {recharge_charges}")
    print(f"  Shopify orders: {shopify_orders}")
    print(f"  Total SKUs tracked: {len(sku_totals)}")

    # Step 3: Cross-checks
    print("\n[3/5] Running cross-checks...")
    discrepancies = _cross_check_pr_cjam(recharge_data, sku_totals)
    shortages = _find_shortages(sku_totals, inventory_data)

    if discrepancies:
        print(f"  WARNING: {len(discrepancies)} PR-CJAM discrepancy(ies):")
        for d in discrepancies:
            print(f"    {d.detail}")
    else:
        print("  PR-CJAM cross-check passed")

    if shortages:
        print(f"  {len(shortages)} shortage(s) detected:")
        for sku, demand, avail in shortages:
            print(f"    {sku}: need {demand}, have {avail} (short {demand - avail})")
    else:
        print("  No shortages detected")

    # Step 4: Generate XLSX
    print("\n[4/5] Generating cut order XLSX...")
    await generate_xlsx(
        sku_totals=sku_totals,
        inventory=inventory_data,
        recharge=recharge_data,
        shortages=shortages,
        discrepancies=discrepancies,
        output_path=output_path,
    )
    print(f"  Saved to: {output_path}")

    # Step 5: Upload to Drive
    drive_link = None
    if not args.no_upload:
        print("\n[5/5] Uploading to Google Drive...")
        drive_link = await upload_to_drive(output_path)
        if drive_link:
            print(f"  {drive_link}")
    else:
        print("\n[5/5] Skipping Drive upload (--no-upload)")

    # Final summary
    print("\n" + "=" * 60)
    print("CUT ORDER SUMMARY")
    print("=" * 60)

    # Top-level counts
    total_boxes = sum(
        d.get("box_count", 0) if isinstance(d, dict) else 0
        for d in recharge_data.get("curations", {}).values()
    )
    print(f"Total boxes: {total_boxes}")
    print(f"MONG projection: +{projection_data.get('projected_additional', 0)}")

    # Curation breakdown
    print("\nBy curation:")
    for cur in sorted(recharge_data.get("curations", {}).keys()):
        data = recharge_data["curations"][cur]
        if isinstance(data, dict):
            print(f"  {cur}: {data.get('box_count', '?')} boxes")

    # Shortages
    if shortages:
        print(f"\n{len(shortages)} SHORTAGE(S):")
        for sku, demand, avail in shortages:
            print(f"  {sku}: short {demand - avail} (need {demand}, have {avail})")

    # Discrepancies
    if discrepancies:
        print(f"\n{len(discrepancies)} DISCREPANCY(IES):")
        for d in discrepancies:
            print(f"  {d.detail}")

    # Output locations
    print(f"\nXLSX: {output_path}")
    if drive_link:
        print(f"Drive: {drive_link}")

    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tuesday Cut Order Generator -- weekly production cut order for RMFG",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        default=False,
        help="Skip Google Drive upload",
    )
    parser.add_argument(
        "--no-projection",
        action="store_true",
        default=False,
        help="Skip MONG first-order projection",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Custom output XLSX path",
    )
    parser.add_argument(
        "--ship-tag",
        type=str,
        default=None,
        help="Override ship tag (default: _SHIP_<today>)",
    )
    parsed = parser.parse_args()
    asyncio.run(main(parsed))
