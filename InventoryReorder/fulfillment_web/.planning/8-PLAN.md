# Phase 8: End-of-Day + Weekly Review — Plan

**Status:** COMPLETE (retroactive)
**Created:** 2026-04-06 (retroactive from commit aa28b4f)

## Goal
Give the operator a structured close-of-day ritual and a weekly retrospective view so carry-forwards are visible, time is accounted for, and streaks reward consistency.

## Deliverables
- `get_eod_summary()` — completed, carrying forward, open blockers, time tracked, tomorrow preview
- `get_weekly_review()` — Mon–Fri completed counts, time analytics, blocker counts, waiting-on, streaks
- EOD card and Weekly Review card rendered inline in the focus panel
- Wrap Up and Review buttons in header
- `get_streaks()` — consecutive weekly completion count per recurring task

## Tasks
1. `get_eod_summary(date)` — query completed tasks for date, open tasks (carry forward), open blockers, sum actual_minutes
2. "Tomorrow preview" — run get_today_tasks() for tomorrow's date to show what's coming
3. `GET /api/cc/eod` — returns eod_summary for today
4. `ccShowEOD()` — button click handler; POST /api/cc/eod then ccRenderEOD(data)
5. `ccRenderEOD(data)` — inject card: completed list, carry-forward list, blocker count, time tracked, tomorrow preview
6. `get_weekly_review(week_start)` — aggregate Mon–Fri: completed per day, total actual_minutes per day, blocker counts, waiting-on list
7. `get_streaks()` — for each recurring_task: count consecutive weeks where at least one instance was completed
8. `get_daily_stats()` — counts: completed_today, active, blocked, minutes_tracked (used in header badges)
9. `GET /api/cc/weekly-review` — returns weekly_review + streaks
10. `ccShowWeeklyReview()` — fetch + ccRenderWeeklyReview(data)
11. `ccRenderWeeklyReview(data)` — 3 stat tiles (tasks done, hours tracked, streak) + daily breakdown + blockers section
12. "Wrap Up" button in header triggers ccShowEOD(); "Review" button triggers ccShowWeeklyReview()
13. Header badge: show daily stats counts next to Wrap Up button

## Files Modified
- `command_center.py` — get_eod_summary, get_weekly_review, get_streaks, get_daily_stats
- `app.py` — /api/cc/eod, /api/cc/weekly-review routes
- `static/command-center/cc.js` — ccShowEOD, ccRenderEOD, ccShowWeeklyReview, ccRenderWeeklyReview

## Verification
- [x] Wrap Up card shows completed tasks, carry-forwards, time tracked
- [x] Tomorrow preview lists next day's recurring tasks
- [x] Weekly Review shows per-day breakdown with time totals
- [x] Streak tiles show consecutive weeks for recurring tasks
- [x] Daily stats badges update in header
- [x] Both cards render without errors on days with no completed tasks
