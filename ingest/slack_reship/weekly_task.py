"""Scheduled weekly wrapper for the reship report -> Google Sheet (one tab/week).

CLI-only (no MCP — unreliable in scheduled runs). Computes the current ship-week
Monday, then runs sync in LIVE Slack + box-type + push mode. Requires:
  * AH_SLACK_BOT_TOKEN set (headless Slack fetch), else it exits LOUDLY (rc=2).
  * healthy shipping.db at the pinned MSIX path (auto-denom + carrier join).
  * reship sheet id already seeded in _outputs/cache/reship_sheet_id.txt
    (Kurt-owned sheet shared to the SA — see 'Weekly Shipping Issue Report.md').

Run manually:  python -m ingest.slack_reship.weekly_task
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_ID_CACHE = Path(r"C:\Users\Work\Claude Projects\_outputs\cache\reship_sheet_id.txt")


def current_week_monday(today: date | None = None) -> str:
    d = today or date.today()
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def main() -> int:
    if not os.environ.get("AH_SLACK_BOT_TOKEN", "").strip():
        print("FATAL: AH_SLACK_BOT_TOKEN not set — cannot fetch Slack headless.",
              file=sys.stderr)
        return 2
    if not _ID_CACHE.exists():
        print(f"FATAL: reship sheet id not seeded ({_ID_CACHE}). Create a Kurt-owned "
              "sheet, share to the SA, run once with --sheet-id <id>.", file=sys.stderr)
        return 2
    week = current_week_monday()
    # delegate to sync's main via argv so all report logic stays in one place
    from ingest.slack_reship import sync
    sys.argv = ["sync", "--week", week, "--report", "--push"]
    sync.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
