# Phase 9: Polish + Compassionate Design Pass — Plan

**Status:** COMPLETE (retroactive)
**Created:** 2026-04-06 (retroactive from commit aa28b4f)

## Goal
Harden the daily experience with triage flows for overwhelm, break reminders, daily snapshots, and accessible markup — making the tool feel supportive rather than demanding.

## Deliverables
- Bad Day Protocol: card-by-card triage UI (keep/archive/done) via `ccBadDayTriage()`
- Daily snapshots to `~/.cc/snapshots/` with 30-day retention on startup
- Break reminders after every 3 completed tasks
- ARIA roles on task cards; "carried forward" language replacing "overdue" everywhere
- Snapshot on app startup via POST /api/cc/backup

## Tasks
1. `get_carryover_tasks()` — tasks from previous days with status=active (not done/archived)
2. `triage_task(task_id, action)` — actions: keep (carry forward), archive, done
3. `POST /api/cc/triage` — accepts {task_id, action}, returns updated task
4. `ccBadDayTriage()` — button in header; fetches carryover tasks, injects triage flow
5. `ccShowTriageFlow(tasks)` — card-by-card UI: task title + Keep / Archive / Done buttons, progress indicator
6. `create_daily_snapshot()` — dump today's tasks JSON to `~/.cc/snapshots/YYYY-MM-DD.json`
7. Snapshot pruning: delete snapshots older than 30 days on each save
8. `POST /api/cc/backup` — triggers create_daily_snapshot(), called on app startup from cc.js
9. `ccCheckBreakReminder()` — count completed tasks since last break reminder; fire after every 3
10. Break reminder: subtle banner "You've done 3 tasks — take a 5-min break?" with Dismiss + Snooze 30min
11. ARIA: add `role="article"` and `aria-label="{task title}"` to all task card divs
12. Language audit: replace all "overdue" strings with "carried forward" or "rolled over" in templates + backend
13. NOT implemented: idle detection, auto load shedding, onboarding flow, mobile companion, Drive upload

## Files Modified
- `command_center.py` — get_carryover_tasks, triage_task, create_daily_snapshot (with 30-day pruning)
- `app.py` — /api/cc/triage, /api/cc/backup routes
- `static/command-center/cc.js` — ccBadDayTriage, ccShowTriageFlow, ccCheckBreakReminder, ARIA attrs, language updates

## Verification
- [x] Triage flow loads carryover tasks and presents keep/archive/done choice
- [x] Snapshot file created at ~/.cc/snapshots/YYYY-MM-DD.json on startup
- [x] Snapshots older than 30 days pruned automatically
- [x] Break reminder appears after every 3 completed tasks
- [x] Task cards have role="article" and aria-label
- [x] No "overdue" language anywhere in rendered UI
