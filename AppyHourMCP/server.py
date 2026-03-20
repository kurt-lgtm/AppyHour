#!/usr/bin/env python3
"""
AppyHour Unified MCP Server

Exposes GelPackCalculator, InventoryReorder, and ShippingReports
as MCP tools for Claude Desktop and other MCP clients.

Transport: stdio (local subprocess)
"""

import sys
from pathlib import Path

# Ensure our package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP("appyhour_mcp")

# Import tool modules — each module registers tools on the shared `mcp` instance
# We pass `mcp` via a module-level setter pattern
from tools import gelcalc, shopify, inventory, shipping, context, google_sheets, gorgias, gorgias_sheets_sync, ops_summary_builder

gelcalc.register(mcp)
shopify.register(mcp)
inventory.register(mcp)
shipping.register(mcp)
context.register(mcp)
google_sheets.register(mcp)
gorgias.register(mcp)
gorgias_sheets_sync.register(mcp)
ops_summary_builder.register(mcp)


if __name__ == "__main__":
    mcp.run()
