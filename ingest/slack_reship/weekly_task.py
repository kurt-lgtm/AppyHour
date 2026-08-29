"""Scheduled weekly wrapper for the reship report -> Google Sheet (one tab/week).

CLI-only (no MCP — unreliable in scheduled runs). Computes the current ship-week
Monday, then runs sync in LIVE Slack + box-type + push mode. Requires:
  * AH_SLACK_BOT_TOKEN in the environment OR AppyHour/.env (bootstrap.init loads
    it), else it exits LOUDLY. 🔴 The pre-bootstrap version checked os.environ
    BEFORE anything loaded .env — broke scheduled runs 2026-08-11/18/22/28.
  * healthy shipping.db at the pinned MSIX path (auto-denom + carrier join).
  * reship sheet id already seeded in _outputs/cache/reship_sheet_id.txt
    (Kurt-owned sheet shared to the SA — see 'Weekly Shipping Issue Report.md').

Run manually:  python -m ingest.slack_reship.weekly_task
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from appyhour_lib.bootstrap import init, require_env  # noqa: E402

init()  # UTF-8 stdio + canonical .env BEFORE any env check

_ID_CACHE = Path(r"C:\Users\Work\Claude Projects\_outputs\cache\reship_sheet_id.txt")


def current_week_monday(today: date | None = None) -> str:
    d = today or date.today()
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def main() -> int:
    require_env("AH_SLACK_BOT_TOKEN")  # headless Slack fetch — loud SystemExit if truly absent
    if not _ID_CACHE.exists():
        print(f"FATAL: reship sheet id not seeded ({_ID_CACHE}). Create a Kurt-owned "
              "sheet, share to the SA, run once with --sheet-id <id>.", file=sys.stderr)
        return 2
    week = current_week_monday()
    # delegate to sync's main via argv so all report logic stays in one place
    from ingest.slack_reship import sync
    sys.argv = ["sync", "--week", week, "--report", "--push"]
    sync.main()
    # Writer-ownership gate (D3, 2026-08-08): a successful run must MOVE a freshness signal the
    # sweep can read — reship metrics were blind to this ingest dying. Fire-and-forget
    # (HEARTBEAT_RULES rule 2: never fail the host task).
    try:
        from appyhour_lib.heartbeat import beat
        beat("slack-reship")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
