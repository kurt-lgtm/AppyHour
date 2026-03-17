"""
Context & Memory MCP resources — exposes Claude Code memory files,
live inventory state, error scan results, and cut order configuration
as readable MCP resources and tools.
"""

import json
from pathlib import Path

MEMORY_DIR = Path.home() / ".claude" / "projects" / "C--Users-Work" / "memory"
SETTINGS_PATH = Path(__file__).parent.parent.parent / "InventoryReorder" / "dist" / "inventory_reorder_settings.json"
ERRORS_DIR = Path(__file__).parent.parent.parent / "InventoryReorder" / "Errors"


def _load_settings() -> dict:
    """Load the inventory reorder settings JSON (single source of truth)."""
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _make_reader(path: Path):
    """Create a no-arg closure that reads a specific file."""
    def reader() -> str:
        return path.read_text(encoding="utf-8")
    reader.__name__ = f"memory_{path.stem.replace('-', '_')}"
    reader.__doc__ = f"Memory file: {path.name}"
    return reader


def register(mcp):
    """Register context/memory resources and live data tools on the MCP server."""

    # ── Memory file resources ────────────────────────────────────────

    @mcp.resource("context://memory/index")
    def memory_index() -> str:
        """Master index of all memory files — read this first to discover available context."""
        index_path = MEMORY_DIR / "MEMORY.md"
        if not index_path.exists():
            return "# Memory Index\n\nNo memories found."
        return index_path.read_text(encoding="utf-8")

    for md_file in sorted(MEMORY_DIR.glob("*.md")):
        if md_file.name == "MEMORY.md":
            continue
        reader = _make_reader(md_file)
        mcp.resource(f"context://memory/{md_file.stem}")(reader)

    # ── Live inventory snapshot ──────────────────────────────────────

    @mcp.tool()
    def get_inventory_snapshot(
        category: str = "",
        low_stock_only: bool = False,
    ) -> str:
        """Get current inventory levels from settings.

        Args:
            category: Filter by SKU prefix (e.g. 'CH-', 'MT-', 'AC-'). Empty = all.
            low_stock_only: If true, only return SKUs with qty <= 50.

        Returns JSON array of {sku, qty, name, category, warehouse_qty}.
        """
        settings = _load_settings()
        inventory = settings.get("inventory", {})
        results = []
        for sku, data in sorted(inventory.items()):
            if category and not sku.startswith(category):
                continue
            qty = data.get("qty", 0)
            if low_stock_only and qty > 50:
                continue
            results.append({
                "sku": sku,
                "qty": qty,
                "name": data.get("name", ""),
                "category": data.get("category", ""),
                "warehouse_qty": data.get("warehouse_qty", {}),
            })
        return json.dumps(results, indent=2)

    # ── Curation & cut order config ──────────────────────────────────

    @mcp.tool()
    def get_cut_order_config() -> str:
        """Get current curation recipes, PR-CJAM assignments, CEX-EC assignments,
        and wheel inventory — everything needed to understand or generate a cut order.

        Returns JSON with keys: curation_recipes, pr_cjam, cex_ec, cexec_splits,
        wheel_inventory, monthly_box_counts.
        """
        settings = _load_settings()
        return json.dumps({
            "curation_recipes": settings.get("curation_recipes", {}),
            "pr_cjam": settings.get("pr_cjam", {}),
            "cex_ec": settings.get("cex_ec", {}),
            "cexec_splits": settings.get("cexec_splits", {}),
            "wheel_inventory": settings.get("wheel_inventory", {}),
            "monthly_box_counts": settings.get("monthly_box_counts", {}),
        }, indent=2)

    # ── Recent error scan results ────────────────────────────────────

    @mcp.tool()
    def get_recent_errors(limit: int = 50) -> str:
        """Get the most recent error scan CSV results from InventoryReorder/Errors/.

        Reads the newest CSV file matching 'error-classes-*' or 'new-error-classes-*'.
        Returns the first `limit` rows as JSON array of dicts.
        """
        import csv as csv_mod

        candidates = sorted(ERRORS_DIR.glob("*error-classes*.csv"), reverse=True)
        if not candidates:
            candidates = sorted(ERRORS_DIR.glob("*error*.csv"), reverse=True)
        if not candidates:
            return json.dumps({"error": "No error scan CSV found", "path": str(ERRORS_DIR)})

        newest = candidates[0]
        rows = []
        with open(newest, encoding="utf-8") as f:
            reader = csv_mod.DictReader(f)
            for i, row in enumerate(reader):
                if i >= limit:
                    break
                rows.append(dict(row))

        return json.dumps({
            "file": newest.name,
            "total_rows": len(rows),
            "rows": rows,
        }, indent=2)

    # ── Depletion history ────────────────────────────────────────────

    @mcp.tool()
    def get_depletion_history(limit: int = 20) -> str:
        """Get recent depletion history entries (fulfillment depletions applied).

        Returns JSON array of {date, file, day, total, total_orders, reship_count, reship_pct}.
        """
        settings = _load_settings()
        history = settings.get("depletion_history", [])
        recent = history[-limit:] if history else []
        return json.dumps(recent, indent=2)
