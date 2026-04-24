"""CLI runner for the Shipments tab build.

Mirrors `run_gorgias_update.py` — standalone entry so Warp, Task
Scheduler, or the Command Center Jobs panel can trigger the same
work that the `build_shipments_tab` MCP tool does, without needing
Claude Code running.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.ops_summary_builder import build_shipments_from_shopify  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="shipping-report",
        description="Rebuild the Shipments tab from Shopify fulfilled orders.",
    )
    parser.add_argument("--weeks-back", type=int, default=12, help="History window (default 12)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no sheet writes")
    args = parser.parse_args()

    print(f"[shipping-report] weeks_back={args.weeks_back} dry_run={args.dry_run}", flush=True)
    try:
        result = build_shipments_from_shopify(
            weeks_back=args.weeks_back,
            dry_run=args.dry_run,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[shipping-report] FAILED: {e}", flush=True)
        return 1

    print(json.dumps(result, indent=2, default=str), flush=True)
    print("[shipping-report] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
