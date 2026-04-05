"""
Tuesday Error Scanner — Agent SDK Automation

Scans unfulfilled Shopify orders for error classes before Tuesday cut order.
Auto-filters known false positives and categorizes results into
AUTO-FIX, REVIEW, and IGNORE buckets.

Error Classes:
  AUTO-FIX: Class 2/3 (blank SKU), 4B (duplicate food)
  REVIEW:   Class 6 (curation mismatch), ROT (rotation bug)
  IGNORE:   Class 7 (gift redemption), 11 (structural), 12 (info only)

False Positive Exclusions:
  - bundle_selections present (customer customized)
  - Double subs (different subscription_ids)
  - Reships (reship tag)
  - AHB-X* specialty boxes
  - Tray orders
  - fulfillable_quantity <= 0

Usage:
  python tuesday_error_scanner.py              # scan and report only
  python tuesday_error_scanner.py --fix        # auto-fix Class 2/3 and 4B
  python tuesday_error_scanner.py --verbose    # include IGNORE category in output
  python tuesday_error_scanner.py --ship-tag _SHIP_2026-04-08  # specific ship tag
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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

# Error class categorization
AUTO_FIX_CLASSES = frozenset({"2", "3", "2/3", "4B"})
REVIEW_CLASSES = frozenset({"6", "ROT", "13", "14"})
IGNORE_CLASSES = frozenset({"7", "11", "12"})

# False positive markers
FALSE_POSITIVE_TAGS = frozenset({"reship", "replacement", "comp"})
SPECIALTY_BOX_PREFIX = "AHB-X"
TRAY_PREFIXES = ("TR-", "PK-TRAY")

# Item count thresholds
FOOD_PREFIXES = ("CH-", "MT-", "AC-")
MEDIUM_MAX_ITEMS = 7
LARGE_MAX_ITEMS = 9

# Items that are never auto-assigned (not errors if missing)
ASSIGNMENT_EXCLUDE = frozenset({"CH-MAFT"})


@dataclass(frozen=True, slots=True)
class ErrorOrder:
    order_name: str
    order_id: str
    error_class: str
    description: str
    sku_details: str
    category: str  # AUTO-FIX, REVIEW, IGNORE


@dataclass(frozen=True, slots=True)
class ScanResults:
    auto_fix: tuple[ErrorOrder, ...]
    review: tuple[ErrorOrder, ...]
    ignore: tuple[ErrorOrder, ...]
    total_scanned: int
    false_positives_filtered: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _categorize_class(error_class: str) -> str:
    """Map error class to action category."""
    if error_class in AUTO_FIX_CLASSES:
        return "AUTO-FIX"
    if error_class in REVIEW_CLASSES:
        return "REVIEW"
    return "IGNORE"


def _compute_ship_tag() -> str:
    """Compute the next Tuesday ship tag from today's date."""
    today = datetime.now()
    # Find next Tuesday (weekday 1)
    days_ahead = (1 - today.weekday()) % 7
    if days_ahead == 0 and today.hour >= 12:
        days_ahead = 7  # Past noon Tuesday, use next week
    next_tuesday = today + timedelta(days=days_ahead)
    return f"_SHIP_{next_tuesday.strftime('%Y-%m-%d')}"


def _format_action_list(results: ScanResults, verbose: bool) -> str:
    """Format a clean, actionable summary."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"*Tuesday Error Scanner* -- {now}",
        f"Scanned {results.total_scanned} orders, "
        f"filtered {results.false_positives_filtered} false positives",
        "",
    ]

    if not results.auto_fix and not results.review:
        lines.append("All clear -- no actionable errors found.")
        return "\n".join(lines)

    # AUTO-FIX section
    if results.auto_fix:
        lines.append(f"*AUTO-FIX ({len(results.auto_fix)}):*")
        for err in results.auto_fix:
            lines.append(f"  [{err.error_class}] {err.order_name}: {err.description}")
            if err.sku_details:
                lines.append(f"        SKUs: {err.sku_details}")
        lines.append("")

    # REVIEW section
    if results.review:
        lines.append(f"*REVIEW ({len(results.review)}):*")
        for err in results.review:
            lines.append(f"  [{err.error_class}] {err.order_name}: {err.description}")
            if err.sku_details:
                lines.append(f"        SKUs: {err.sku_details}")
        lines.append("")

    # IGNORE section (only if verbose)
    if verbose and results.ignore:
        lines.append(f"*IGNORE ({len(results.ignore)}):*")
        for err in results.ignore:
            lines.append(f"  [{err.error_class}] {err.order_name}: {err.description}")
        lines.append("")

    # Summary counts
    lines.append("---")
    lines.append(
        f"Totals: {len(results.auto_fix)} auto-fix, "
        f"{len(results.review)} review, "
        f"{len(results.ignore)} ignore"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent-powered scanning
# ---------------------------------------------------------------------------

async def run_error_scan(ship_tag: str) -> dict[str, Any]:
    """Use the agent to scan orders and return structured error data."""
    prompt = (
        f"Scan unfulfilled Shopify orders tagged '{ship_tag}' for errors. "
        "For each order, check:\n"
        "1. Class 2/3: 'appyhour box' title with no SKU, price > $30, no AHB- on order\n"
        "2. Class 4B: Duplicate CH-/MT-/AC- SKUs on _rc_bundle lines "
        "(exclude BL- bundles, one-time add-ons, customer-chosen items, "
        "and orders where item count matches expected box size)\n"
        "3. Class 6: Curation mismatch (>20% better overlap with different curation)\n"
        "4. Class ROT: Wrong month curation (_SHIP != ship tag, <60% recipe overlap, "
        "no bundle_selections, not BYO)\n"
        "5. Class 13: Stale Matrixify/CEX-EC from prior months\n"
        "6. Class 14: Duplicate PR-CJAM (NMS orders: PR-CJAM-GEN + PR-CJAM-NMS)\n\n"
        "EXCLUDE from results:\n"
        "- Orders with bundle_selections property (customer customized)\n"
        "- Orders tagged 'reship', 'replacement', or 'comp'\n"
        "- AHB-X* specialty boxes\n"
        "- Tray orders (TR-/PK-TRAY SKUs)\n"
        "- Line items with fulfillable_quantity <= 0\n"
        "- Double subs (items from different subscription_ids)\n"
        "- BYO curations for Class 6/ROT checks\n\n"
        "Return results as JSON with this structure:\n"
        '{"total_scanned": N, "false_positives_filtered": N, "errors": ['
        '{"order_name": "#1234", "order_id": "gid://...", '
        '"error_class": "4B", "description": "...", "sku_details": "CH-MCPC x2"}]}'
    )

    result_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=[
                "mcp__appyhour__appyhour_fetch_orders",
                "mcp__appyhour__appyhour_analyze_orders",
                "mcp__appyhour__get_recent_errors",
                "Bash",
                "Read",
                "Grep",
            ],
            permission_mode="acceptEdits",
            max_turns=25,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    result_text += block.text
                    # Print progress dots
                    if "scanning" in block.text.lower() or "checking" in block.text.lower():
                        print(".", end="", flush=True)

    print()  # newline after progress dots

    # Parse JSON from agent response
    try:
        start = result_text.index("{")
        end = result_text.rindex("}") + 1
        return json.loads(result_text[start:end])
    except (ValueError, json.JSONDecodeError):
        print("WARNING: Could not parse structured results from agent.")
        print("Raw output (first 1000 chars):")
        print(result_text[:1000])
        return {"total_scanned": 0, "false_positives_filtered": 0, "errors": []}


def _parse_scan_results(raw: dict[str, Any]) -> ScanResults:
    """Convert raw scan JSON into categorized ScanResults."""
    auto_fix: list[ErrorOrder] = []
    review: list[ErrorOrder] = []
    ignore: list[ErrorOrder] = []

    for err in raw.get("errors", []):
        error_class = str(err.get("error_class", ""))
        category = _categorize_class(error_class)

        order = ErrorOrder(
            order_name=err.get("order_name", "unknown"),
            order_id=err.get("order_id", ""),
            error_class=error_class,
            description=err.get("description", ""),
            sku_details=err.get("sku_details", ""),
            category=category,
        )

        if category == "AUTO-FIX":
            auto_fix.append(order)
        elif category == "REVIEW":
            review.append(order)
        else:
            ignore.append(order)

    return ScanResults(
        auto_fix=tuple(auto_fix),
        review=tuple(review),
        ignore=tuple(ignore),
        total_scanned=raw.get("total_scanned", 0),
        false_positives_filtered=raw.get("false_positives_filtered", 0),
    )


async def auto_fix_errors(errors: tuple[ErrorOrder, ...]) -> int:
    """Auto-fix Class 2/3 and 4B errors using existing fix scripts."""
    if not errors:
        return 0

    class23 = [e for e in errors if e.error_class in ("2", "3", "2/3")]
    class4b = [e for e in errors if e.error_class == "4B"]
    fixed = 0

    if class23:
        order_ids = [e.order_id for e in class23 if e.order_id]
        print(f"  Fixing {len(class23)} Class 2/3 errors (blank SKU -> AHB-MED + PR-CJAM-GEN)...")

        prompt = (
            "Fix these Class 2/3 error orders by running the fix_class23.py logic:\n"
            "For each order, use GraphQL orderEdit to:\n"
            "1. Add AHB-MED variant ($0)\n"
            "2. Add PR-CJAM-GEN variant ($0)\n"
            "3. Add appropriate discount\n\n"
            f"Order IDs: {json.dumps(order_ids)}\n"
            "Report each fix result."
        )

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[
                    "mcp__appyhour__appyhour_swap_order_skus",
                    "mcp__appyhour__appyhour_fetch_orders",
                    "Bash",
                    "Read",
                ],
                permission_mode="acceptEdits",
                max_turns=15,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text") and "fixed" in block.text.lower():
                        fixed += 1
                        print(f"    {block.text}")

    if class4b:
        order_ids = [e.order_id for e in class4b if e.order_id]
        print(f"  Fixing {len(class4b)} Class 4B errors (remove duplicate _rc_bundle items)...")

        prompt = (
            "Fix these Class 4B duplicate food errors:\n"
            "For each order, use GraphQL orderEdit to set quantity=0 on the "
            "duplicate _rc_bundle line item (keep the first occurrence).\n"
            "Only touch _rc_bundle items, never paid add-ons.\n\n"
            f"Order IDs: {json.dumps(order_ids)}\n"
            "Report each fix result."
        )

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[
                    "mcp__appyhour__appyhour_swap_order_skus",
                    "mcp__appyhour__appyhour_fetch_orders",
                    "Bash",
                    "Read",
                ],
                permission_mode="acceptEdits",
                max_turns=15,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text") and "fixed" in block.text.lower():
                        fixed += 1
                        print(f"    {block.text}")

    return fixed


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    """Main entry point."""
    ship_tag = args.ship_tag or _compute_ship_tag()

    print("=" * 60)
    print("Tuesday Error Scanner")
    print(f"Ship tag: {ship_tag}")
    print(f"Mode: {'AUTO-FIX' if args.fix else 'SCAN ONLY'}")
    print("=" * 60)

    # Step 1: Scan
    print("\n[1/3] Scanning orders for errors...")
    raw_results = await run_error_scan(ship_tag)
    results = _parse_scan_results(raw_results)

    print(f"  Scanned {results.total_scanned} orders")
    print(f"  Filtered {results.false_positives_filtered} false positives")
    print(f"  Found: {len(results.auto_fix)} auto-fix, "
          f"{len(results.review)} review, {len(results.ignore)} ignore")

    # Step 2: Auto-fix if requested
    fixed_count = 0
    if args.fix and results.auto_fix:
        print("\n[2/3] Auto-fixing errors...")
        fixed_count = await auto_fix_errors(results.auto_fix)
        print(f"  Fixed {fixed_count} orders")
    else:
        print("\n[2/3] Skipping auto-fix (use --fix to enable)")

    # Step 3: Output action list
    print("\n[3/3] Generating action list...")
    action_list = _format_action_list(results, verbose=args.verbose)

    print("\n" + "-" * 60)
    print("ACTION LIST:")
    print("-" * 60)
    print(action_list)

    if args.fix and fixed_count > 0:
        print(f"\n** {fixed_count} order(s) were auto-fixed **")

    print("-" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tuesday Error Scanner -- scan and optionally fix order errors",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Auto-fix Class 2/3 and 4B errors (default: scan only)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Include IGNORE category in output",
    )
    parser.add_argument(
        "--ship-tag",
        type=str,
        default=None,
        help="Specific ship tag to scan (default: auto-compute next Tuesday)",
    )
    parsed = parser.parse_args()
    asyncio.run(main(parsed))
