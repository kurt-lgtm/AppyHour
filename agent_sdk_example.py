"""
Claude Agent SDK — Fulfillment Automation Examples

Use these as templates for building standalone automation scripts
that run outside of Claude Code. Lighter weight, no UI needed.

Install: pip install claude-agent-sdk
Docs: https://platform.claude.com/docs/en/agent-sdk/quickstart
"""

import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage


async def weekly_error_scan():
    """Scan unfulfilled orders for errors and generate report."""
    async for message in query(
        prompt=(
            "Run the error scanner on unfulfilled Shopify orders. "
            "Check for Classes 2/3, 4B, 6, ROT. "
            "Output a summary CSV to InventoryReorder/Errors/scan_results.csv"
        ),
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Bash", "Grep", "Write"],
            permission_mode="acceptEdits",
            max_turns=20,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            print(f"Done: {message.subtype}")


async def generate_cut_order():
    """Generate Wednesday cut order from Recharge + Shopify demand."""
    async for message in query(
        prompt=(
            "Generate the weekly cut order. "
            "Pull Recharge queued charges and Shopify unfulfilled orders. "
            "Resolve PR-CJAM and CEX-EC per curation. "
            "Output to production_orders/cut_order_<today>.csv"
        ),
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Bash", "Grep", "Write"],
            permission_mode="acceptEdits",
            max_turns=30,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)


async def depletion_check():
    """After Saturday depletion, project Tuesday shortages."""
    async for message in query(
        prompt=(
            "Check the latest depletion files in Shipments/. "
            "Calculate current inventory minus Tuesday demand. "
            "Flag any SKUs that will be short for Tuesday fulfillment. "
            "Output shortage list with suggested swaps."
        ),
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Bash", "Grep"],
            permission_mode="default",
            max_turns=15,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)


if __name__ == "__main__":
    import sys

    commands = {
        "scan": weekly_error_scan,
        "cut-order": generate_cut_order,
        "depletion": depletion_check,
    }

    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(f"Usage: python agent_sdk_example.py <{'|'.join(commands)}>")
        sys.exit(1)

    asyncio.run(commands[sys.argv[1]]())
