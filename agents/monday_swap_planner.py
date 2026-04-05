"""
Monday Swap Planner — Agent SDK Automation

Runs Monday evening to prepare for Tuesday cut order.
Hits the fulfillment web API (localhost:5187) for Tuesday projection,
identifies SKUs that will be short, applies substitution rules,
and generates swap recommendations.

Substitution families:
  - Brie:   CH-TTBRIE <-> CH-TIP <-> CH-EBRIE
  - Porter: CH-MCPC <-> CH-IPRW
  - Alpine: CH-BARI <-> CH-ALPHA

Usage:
  python monday_swap_planner.py              # dry-run (default)
  python monday_swap_planner.py --execute    # apply swaps via MCP tool
  python monday_swap_planner.py --threshold 5  # only flag shortages > 5 units
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
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

SUBSTITUTION_FAMILIES: dict[str, list[str]] = {
    "brie": ["CH-TTBRIE", "CH-TIP", "CH-EBRIE"],
    "porter": ["CH-MCPC", "CH-IPRW"],
    "alpine": ["CH-BARI", "CH-ALPHA"],
}

# Dietary-restriction box fragments — never auto-swap these orders
DIETARY_EXCLUSION_FRAGMENTS = ("NNRS", "CORS", "NCRS")

FULFILLMENT_API = "http://localhost:5187"


@dataclass(frozen=True, slots=True)
class Shortage:
    sku: str
    demand: int
    available: int
    deficit: int


@dataclass(frozen=True, slots=True)
class SwapRecommendation:
    short_sku: str
    sub_sku: str
    family: str
    units_to_swap: int
    sub_surplus: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_family(sku: str) -> tuple[str, list[str]] | None:
    """Return (family_name, members) if SKU belongs to a substitution family."""
    for name, members in SUBSTITUTION_FAMILIES.items():
        if sku in members:
            return name, members
    return None


def _build_swap_recommendations(
    shortages: list[Shortage],
    inventory: dict[str, int],
) -> list[SwapRecommendation]:
    """For each shortage, find the best substitute within the same family."""
    recommendations: list[SwapRecommendation] = []
    # Track surplus consumed by earlier recommendations
    consumed: dict[str, int] = {}

    for shortage in shortages:
        family_info = _find_family(shortage.sku)
        if family_info is None:
            continue

        family_name, members = family_info
        candidates = [m for m in members if m != shortage.sku]

        # Pick candidate with highest remaining surplus
        best_candidate = None
        best_surplus = 0
        for candidate in candidates:
            raw_surplus = inventory.get(candidate, 0)
            already_used = consumed.get(candidate, 0)
            remaining = raw_surplus - already_used
            if remaining > 0 and remaining > best_surplus:
                best_candidate = candidate
                best_surplus = remaining

        if best_candidate is not None:
            units = min(shortage.deficit, best_surplus)
            consumed[best_candidate] = consumed.get(best_candidate, 0) + units
            recommendations.append(
                SwapRecommendation(
                    short_sku=shortage.sku,
                    sub_sku=best_candidate,
                    family=family_name,
                    units_to_swap=units,
                    sub_surplus=best_surplus,
                )
            )

    return recommendations


def _format_slack_summary(
    shortages: list[Shortage],
    recommendations: list[SwapRecommendation],
    unresolved: list[Shortage],
    dry_run: bool,
) -> str:
    """Format a Slack-ready summary block."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "DRY RUN" if dry_run else "EXECUTED"
    lines = [
        f"*Monday Swap Planner* ({mode}) -- {now}",
        "",
    ]

    if not shortages:
        lines.append("No shortages detected for Tuesday. All clear.")
        return "\n".join(lines)

    lines.append(f"*{len(shortages)} SKU(s) short for Tuesday:*")
    for s in shortages:
        lines.append(f"  - `{s.sku}`: need {s.demand}, have {s.available} (short {s.deficit})")

    if recommendations:
        lines.append("")
        lines.append(f"*{len(recommendations)} swap(s) {'applied' if not dry_run else 'recommended'}:*")
        for r in recommendations:
            lines.append(
                f"  - `{r.short_sku}` -> `{r.sub_sku}` ({r.family}): "
                f"{r.units_to_swap} units (sub surplus: {r.sub_surplus})"
            )

    if unresolved:
        lines.append("")
        lines.append(f"*{len(unresolved)} shortage(s) with NO substitute available:*")
        for s in unresolved:
            lines.append(f"  - `{s.sku}`: short {s.deficit} -- manual intervention needed")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def fetch_tuesday_projection() -> dict[str, Any]:
    """Fetch Tuesday projection from the fulfillment web API."""
    prompt = (
        f"Make an HTTP GET request to {FULFILLMENT_API}/api/tuesday_projection "
        "and return the full JSON response body. If the server is not running, "
        "return an error message. Output ONLY the raw JSON, no commentary."
    )

    result_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=["Bash"],
            permission_mode="acceptEdits",
            max_turns=5,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    result_text += block.text

    # Extract JSON from the response
    try:
        # Try to find JSON in the output
        start = result_text.index("{")
        end = result_text.rindex("}") + 1
        return json.loads(result_text[start:end])
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: Could not parse projection response: {exc}")
        print(f"Raw response: {result_text[:500]}")
        sys.exit(1)


async def execute_swaps(recommendations: list[SwapRecommendation]) -> None:
    """Use the MCP swap tool to apply recommended swaps."""
    for rec in recommendations:
        print(f"  Executing swap: {rec.short_sku} -> {rec.sub_sku} ({rec.units_to_swap} units)...")

        prompt = (
            f"Use the appyhour_swap_order_skus tool to swap {rec.short_sku} "
            f"with {rec.sub_sku} on unfulfilled orders. "
            f"Only swap _rc_bundle line items (never paid add-ons or customer-chosen). "
            f"Skip orders with dietary restriction box SKUs containing NNRS, CORS, or NCRS. "
            f"Limit to {rec.units_to_swap} units maximum. "
            f"Report how many orders were affected."
        )

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[
                    "mcp__appyhour__appyhour_swap_order_skus",
                    "mcp__appyhour__appyhour_fetch_orders",
                ],
                permission_mode="acceptEdits",
                max_turns=10,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        print(f"    {block.text}")


async def main(args: argparse.Namespace) -> None:
    """Main entry point."""
    print("=" * 60)
    print("Monday Swap Planner")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    print(f"Shortage threshold: {args.threshold} units")
    print("=" * 60)

    # Step 1: Get Tuesday projection
    print("\n[1/3] Fetching Tuesday projection from fulfillment web...")
    projection = await fetch_tuesday_projection()

    # Extract inventory and demand data
    inventory: dict[str, int] = {}
    demand: dict[str, int] = {}
    shortages: list[Shortage] = []

    # Parse projection response -- structure depends on API
    # Expected: {"skus": {"CH-TTBRIE": {"demand": 120, "available": 80, ...}, ...}}
    skus_data = projection.get("skus", projection.get("data", projection))

    if isinstance(skus_data, dict):
        for sku, info in skus_data.items():
            if not sku.startswith(("CH-", "MT-", "AC-")):
                continue
            if isinstance(info, dict):
                sku_demand = info.get("demand", info.get("total_demand", 0))
                sku_available = info.get("available", info.get("on_hand", 0))
            else:
                continue

            demand[sku] = sku_demand
            inventory[sku] = sku_available

            deficit = sku_demand - sku_available
            if deficit > args.threshold:
                shortages.append(Shortage(
                    sku=sku,
                    demand=sku_demand,
                    available=sku_available,
                    deficit=deficit,
                ))

    # Sort by worst shortage first
    shortages = sorted(shortages, key=lambda s: s.deficit, reverse=True)

    print(f"  Found {len(shortages)} shortage(s) above threshold ({args.threshold})")

    # Step 2: Generate swap recommendations
    print("\n[2/3] Generating swap recommendations...")
    recommendations = _build_swap_recommendations(shortages, inventory)

    resolved_skus = {r.short_sku for r in recommendations}
    unresolved = [s for s in shortages if s.sku not in resolved_skus]

    print(f"  {len(recommendations)} swap(s) recommended")
    if unresolved:
        print(f"  {len(unresolved)} shortage(s) have no automatic substitute")

    # Step 3: Execute or report
    if args.execute and recommendations:
        print("\n[3/3] Executing swaps...")
        await execute_swaps(recommendations)
    else:
        print("\n[3/3] Generating summary (dry run)...")

    # Output Slack-ready summary
    summary = _format_slack_summary(
        shortages=shortages,
        recommendations=recommendations,
        unresolved=unresolved,
        dry_run=not args.execute,
    )

    print("\n" + "-" * 60)
    print("SLACK-READY SUMMARY:")
    print("-" * 60)
    print(summary)
    print("-" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Monday Swap Planner -- identify Tuesday shortages and recommend swaps",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually execute swaps via MCP tool (default: dry run)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=0,
        help="Only flag shortages greater than this number of units (default: 0)",
    )
    parsed = parser.parse_args()
    asyncio.run(main(parsed))
