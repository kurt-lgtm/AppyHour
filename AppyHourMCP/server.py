#!/usr/bin/env python3
"""
AppyHour Unified MCP Server

Exposes GelPackCalculator, InventoryReorder, and ShippingReports
as MCP tools for Claude Desktop and other MCP clients.

Transport: stdio (local subprocess)
"""

import logging
import sys
import traceback
from pathlib import Path

# Configure stderr logging so crashes are visible
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("appyhour_mcp")

# Ensure our package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP("appyhour_mcp")

# Import tool modules — each module registers tools on the shared `mcp` instance
# We pass `mcp` via a module-level setter pattern
from tools import gelcalc, shopify, inventory, shipping, context, google_sheets, gorgias, gorgias_sheets_sync, ops_summary_builder, order_edit, matrix_qc

gelcalc.register(mcp)
shopify.register(mcp)
inventory.register(mcp)
shipping.register(mcp)
context.register(mcp)
google_sheets.register(mcp)
gorgias.register(mcp)
gorgias_sheets_sync.register(mcp)
ops_summary_builder.register(mcp)
order_edit.register(mcp)
matrix_qc.register(mcp)


if __name__ == "__main__":
    try:
        logger.info("Starting AppyHour MCP server")
        mcp.run()
    except Exception:
        logger.critical("MCP server crashed:\n%s", traceback.format_exc())
        sys.exit(1)
