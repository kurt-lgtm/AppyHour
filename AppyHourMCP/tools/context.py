"""
Context & Memory MCP resources — exposes Claude Code memory files,
live inventory state, error scan results, and cut order configuration
as readable MCP resources and tools.
"""

import json
from collections.abc import Callable
from pathlib import Path


def _find_memory_dir() -> Path:
    """Derive Claude Code memory directory from home path."""
    home = Path.home()
    # Claude Code encodes the project path: colons become double-dash, separators become dash
    home_encoded = str(home).replace("\\", "-").replace("/", "-").replace(":", "-")
    return home / ".claude" / "projects" / home_encoded / "memory"


MEMORY_DIR = _find_memory_dir()
SETTINGS_PATH = Path(__file__).parent.parent.parent / "InventoryReorder" / "dist" / "inventory_reorder_settings.json"
ERRORS_DIR = Path(__file__).parent.parent.parent / "InventoryReorder" / "Errors"


def _load_settings() -> dict:
    """Load the inventory reorder settings JSON (single source of truth)."""
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _make_reader(path: Path) -> Callable[[], str]:
    """Create a no-arg closure that reads a specific file."""

    def reader() -> str:
        return path.read_text(encoding="utf-8")

    reader.__name__ = f"memory_{path.stem.replace('-', '_')}"
    reader.__doc__ = f"Memory file: {path.name}"
    return reader


def register(mcp: object) -> None:
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
            results.append(
                {
                    "sku": sku,
                    "qty": qty,
                    "name": data.get("name", ""),
                    "category": data.get("category", ""),
                    "warehouse_qty": data.get("warehouse_qty", {}),
                }
            )
        return json.dumps(results, indent=2)

    # ── Calculated inventory (journal-replayed) ───────────────────────

    @mcp.tool()
    def get_calculated_inventory(
        category: str = "",
        include_potential: bool = False,
    ) -> str:
        """Get calculated available inventory by replaying the inventory journal.

        This is the source of truth — accounts for Dropbox snapshots,
        depletions (Sat/Tue), adjustments, and production. More accurate
        than get_inventory_snapshot which reads static settings values.

        Use this for Matrix Commander inventory CSV input and pre-fulfillment
        inventory checks.

        Args:
            category: Filter by SKU prefix (e.g. 'CH-', 'MT-', 'AC-'). Empty = all.
            include_potential: If true, include wheel potential and open PO qty.

        Returns JSON with {sku: {on_hand, wheel_potential?, incoming?, total?}}.
        """
        settings = _load_settings()
        journal = settings.get("inventory_journal", [])
        snapshots = settings.get("inventory_snapshots", [])

        # Replay journal on last snapshot
        snap_by_id = {sn["id"]: sn for sn in snapshots}

        def _load_snap(snap_id: str) -> tuple[dict[str, int], dict[str, int]]:
            sn = snap_by_id.get(snap_id)
            if not sn:
                return {}, {}
            sl = dict(sn.get("inventory", {}))
            wh = {s: p.get("wheels", 0) for s, p in sn.get("potential_yield", {}).items()}
            return sl, wh

        last_snap_idx = -1
        for i, entry in enumerate(journal):
            if entry.get("type") == "snapshot":
                last_snap_idx = i

        sliced, wheels = {}, {}
        if last_snap_idx >= 0:
            sliced, wheels = _load_snap(journal[last_snap_idx].get("snapshot_id", ""))

        for entry in journal[last_snap_idx + 1 :]:
            etype = entry.get("type", "")
            if etype == "snapshot":
                sliced, wheels = _load_snap(entry.get("snapshot_id", ""))
            elif etype in ("depletion", "adjustment"):
                for sku, delta in entry.get("sku_deltas", {}).items():
                    sliced[sku] = sliced.get(sku, 0) + int(delta)
            elif etype == "production":
                sku = entry.get("sku", "")
                if sku:
                    wheels[sku] = wheels.get(sku, 0) - entry.get("wheels_cut", 0)
                    sliced[sku] = sliced.get(sku, 0) + entry.get("actual_sliced", 0)

        # Build results
        results = {}
        for sku in sorted(sliced.keys()):
            if category and not sku.startswith(category):
                continue
            qty = max(0, int(sliced.get(sku, 0)))
            entry = {"on_hand": qty}

            if include_potential:
                wheel_inv = settings.get("wheel_inventory", {})
                pot = 0
                for wsku, wdata in wheel_inv.items():
                    if wdata.get("target_sku") == sku:
                        w = wdata.get("weight_lbs", 0)
                        c = wheels.get(wsku, wdata.get("count", 0))
                        if c > 0 and w > 0:
                            pot += int(c * w * 2.67)
                entry["wheel_potential"] = pot

                incoming = 0
                for po in settings.get("open_pos", []):
                    if po.get("sku") == sku and po.get("status") in ("ordered", "confirmed", "in_transit"):
                        incoming += po.get("qty", 0)
                entry["incoming"] = incoming
                entry["total"] = qty + pot + incoming

            results[sku] = entry

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
        return json.dumps(
            {
                "curation_recipes": settings.get("curation_recipes", {}),
                "pr_cjam": settings.get("pr_cjam", {}),
                "cex_ec": settings.get("cex_ec", {}),
                "cexec_splits": settings.get("cexec_splits", {}),
                "wheel_inventory": settings.get("wheel_inventory", {}),
                "monthly_box_counts": settings.get("monthly_box_counts", {}),
            },
            indent=2,
        )

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

        return json.dumps(
            {
                "file": newest.name,
                "total_rows": len(rows),
                "rows": rows,
            },
            indent=2,
        )

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
