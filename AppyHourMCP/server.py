#!/usr/bin/env python3

# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp"]
# ///

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
mcp: FastMCP = FastMCP("appyhour_mcp")

# Import tool modules — each module registers tools on the shared `mcp` instance
# We pass `mcp` via a module-level setter pattern
# NOTE: `shipping` (appyhour_shipping_analysis, appyhour_apply_zip_routing_tags)
# intentionally NOT registered here as of 2026-06-01 — those tools are duplicated
# in AppyHourShippingMCP (the load-on-demand shipping server) and were removed from
# this always-on server to keep its tool surface lean. The tools/shipping.py module
# is retained but unwired; re-add it to the import + register() list to restore.
from tools import gelcalc, shopify, inventory, context, google_sheets, gorgias, gorgias_sheets_sync, ops_summary_builder, order_edit, matrix_qc, product_catalog, shopify_bulk

gelcalc.register(mcp)
shopify.register(mcp)
inventory.register(mcp)
context.register(mcp)
google_sheets.register(mcp)
gorgias.register(mcp)
gorgias_sheets_sync.register(mcp)
ops_summary_builder.register(mcp)
order_edit.register(mcp)
matrix_qc.register(mcp)
product_catalog.register(mcp)
shopify_bulk.register(mcp)

if __name__ == "__main__":
    try:
        # Bind this stdio server's lifetime to its client. On Windows an unclean
        # client shutdown often fails to deliver stdin-EOF, leaving an orphaned
        # server holding a shipping.db connection (6 such orphans accumulated and
        # contributed to the 2026-06-27 DB corruption). The watchdog hard-exits
        # when the parent dies. See appyhour_lib/proc.py.
        try:
            from appyhour_lib.proc import install_parent_death_watchdog

            if install_parent_death_watchdog(logger=logger):
                logger.info("parent-death watchdog active (ppid reaping)")
        except Exception:
            logger.warning("parent-death watchdog unavailable", exc_info=True)

        logger.info("Starting AppyHour MCP server")
        mcp.run()
    except Exception:
        logger.critical("MCP server crashed:\n%s", traceback.format_exc())
        sys.exit(1)
