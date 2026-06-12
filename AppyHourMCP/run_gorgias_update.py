"""CLI runner for Gorgias → Sheet sync + enrich.

Standalone entry so Warp (or any terminal) can trigger the same
combo as the `update_operational_issues` MCP tool without needing
Claude Code running.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Add AppyHourMCP dir to path so `tools.*` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.gorgias_sheets_sync import (  # noqa: E402
    enrich_incomplete_rows,
    sync_gorgias_to_sheet,
)


def _run_sync(days: int, dry_run: bool, retries: int = 3) -> dict:
    """Sync with 429-aware retry/backoff."""
    delay = 30
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return sync_gorgias_to_sheet(days_back=days, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e)
            if "429" in msg and attempt < retries:
                print(f"[sync] 429 rate limit, sleeping {delay}s (attempt {attempt}/{retries})", flush=True)
                time.sleep(delay)
                delay *= 2
                continue
            raise
    assert last_err is not None
    raise last_err


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="gorgias-update",
        description="Sync new Gorgias tickets + enrich incomplete rows in UPDATE_Operational Issues.",
    )
    parser.add_argument("--days", type=int, default=7, help="Days back to pull (default 7)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    parser.add_argument("--sync-only", action="store_true", help="Skip enrich step")
    parser.add_argument("--enrich-only", action="store_true", help="Skip sync step")
    args = parser.parse_args()

    if args.sync_only and args.enrich_only:
        parser.error("--sync-only and --enrich-only are mutually exclusive")

    result: dict = {"dry_run": args.dry_run}

    if not args.enrich_only:
        print(f"[sync] pulling Gorgias tickets, last {args.days} days...", flush=True)
        sync = _run_sync(args.days, args.dry_run)
        result["sync"] = {
            "checked": sync["checked"],
            "new_rows": sync["new_rows"],
            "skipped_duplicate": sync["skipped_duplicate"],
            "skipped_invalid_tag": sync["skipped_invalid_tag"],
        }
        print(json.dumps(result["sync"], indent=2), flush=True)

    if not args.sync_only:
        # Multi-pass enrich — second pass may unlock rows that only got
        # partial data first time (e.g. order_num filled → now Shopify
        # lookup works → carrier/state fill).
        passes = []
        for pass_no in range(1, 4):
            print(f"[enrich] pass {pass_no} — filling incomplete rows...", flush=True)
            enr = enrich_incomplete_rows(dry_run=args.dry_run)
            passes.append({
                "pass": pass_no,
                "total_rows": enr["total_rows"],
                "rows_enriched": enr["rows_enriched"],
            })
            print(json.dumps(passes[-1], indent=2), flush=True)
            if enr["rows_enriched"] == 0:
                break
        result["enrich_passes"] = passes

    print("\n[done]", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
