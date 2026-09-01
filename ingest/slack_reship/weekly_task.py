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


def target_week_monday(today: date | None = None) -> str:
    """The Monday of the last COMPLETE Mon–Sun week: this week's Monday, minus 7 days.

    🔴 THIS REPORTS THE PRIOR WEEK, NEVER THE WEEK IN PROGRESS. Until 2026-09-01 this was
    `current_week_monday` — the Monday of the CURRENT week — and the routine fires Tuesday
    around noon. So it asked "how did the week that began yesterday go", against a ticket
    window ~1.5 of 7 days old and a `_SHIP_<Monday>` cohort that had barely begun to fulfil.
    Two tabs went out that way and are still on the sheet:
      * `2026-07-20`, written Tue 07-21 12:11 → **denom 0**, 5 tickets (true cohort 2082);
      * `2026-08-10`, written Tue 08-11 12:10 → **denom 2**, 1 ticket (true cohort 2365).
    The weeks that look right (`2026-06-29`, `2026-07-13`, `2026-08-24`) are exactly the ones
    whose run landed LATE — Wed/Fri catch-ups — so lateness was accidentally the only thing
    producing a correct number.

    Day-agnostic on purpose: `today − 8 days` assumes a Tuesday fire and silently reports the
    wrong window on a catch-up run, which is common here (the machine sleeps through noon).
    Identical formula and reason as the sibling `weekly-shipping-vendor-matrix` routine, which
    has covered the prior complete window since 2026-08-28 — the two read the same Slack
    channel and the same denominator and must not disagree about which week they mean.
    """
    d = today or date.today()
    return (d - timedelta(days=d.weekday() + 7)).strftime("%Y-%m-%d")


def main() -> int:
    require_env("AH_SLACK_BOT_TOKEN")  # headless Slack fetch — loud SystemExit if truly absent
    if not _ID_CACHE.exists():
        print(f"FATAL: reship sheet id not seeded ({_ID_CACHE}). Create a Kurt-owned "
              "sheet, share to the SA, run once with --sheet-id <id>.", file=sys.stderr)
        return 2
    week = target_week_monday()
    # delegate to sync's main via argv so all report logic stays in one place
    from ingest.slack_reship import sync
    sys.argv = ["sync", "--week", week, "--report", "--push"]
    url = sync.main()
    # 🔴 THE BEAT IS GATED ON THE PUBLISHED TAB, NOT ON REACHING THIS LINE. `slack-reship` is the
    # only evidence this exception-only routine still runs (automation_health.EXPECTED, 10d), so a
    # beat on a run that wrote no tab would forge exactly the signal the dead-man switch exists to
    # withhold — worse than no beat at all. `sync.main()` returns the sheet URL only after
    # `push()` returned one; anything earlier raises and never reaches here.
    if not url:
        print("FATAL: sync returned no sheet URL — nothing was published, so no beat.",
              file=sys.stderr)
        return 3
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
