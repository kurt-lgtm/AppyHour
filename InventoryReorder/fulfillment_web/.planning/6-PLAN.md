# Phase 6: Priority Engine + Energy Scheduling — Plan

**Status:** COMPLETE (retroactive)
**Created:** 2026-04-06 (retroactive from commit aa28b4f)

## Goal
Replace manual priority with a computed urgency score that weighs energy fit, time pressure, impact, and blocker risk — so the top of the queue always reflects what to work on next.

## Deliverables
- `compute_urgency()` — 4-factor weighted score (energy_fit×0.35, time_pressure×0.35, impact×0.20, blocker_risk×0.10)
- `_time_pressure()` — 6-bucket decay from overdue (1.0) to >2 weeks (0.0)
- `get_today_tasks()` — sorts by score, categorizes into frog/quick_wins/today/blocked/personal/completed
- Overwhelm detection at 12+ tasks with "Lighten my day" button (top 3 only)
- WIP limit constant (CC_WIP_LIMIT=3) declared; enforced as 1-active-timer-at-a-time

## Tasks
1. `compute_urgency(task, energy_level)` — returns float 0.0–1.0
2. `_energy_fit(task, energy_level)` — high task needs high energy; mismatch penalty
3. `_time_pressure(task)` — buckets: overdue=1.0, today=0.85, tomorrow=0.65, this_week=0.40, next_week=0.20, >2wk=0.0
4. `_impact_score(task)` — derived from priority field: high=1.0, medium=0.6, low=0.2
5. `_blocker_risk(task)` — 0.8 if has open dependencies, 0.0 otherwise
6. Update `get_today_tasks(energy_level)` — score each task, sort descending, categorize
7. Category rules: frog = highest-scoring work task, quick_wins = score>0.3 + estimated<5min, blocked = status=blocked, personal = type=personal
8. `CC_WIP_LIMIT = 3` constant; only 1 timer runs at a time (enforce in ccStartTask)
9. Overwhelm detection: if len(today_tasks) >= 12, add `overwhelmed: true` flag to response
10. `GET /api/cc/today?lighten=true` — returns top 3 tasks only when overwhelmed
11. `ccAutoEnergy()` updated to write back degraded level via PATCH settings
12. Frontend: show "Lighten my day" button in header when overwhelmed flag set; re-fetch with lighten=true

## Files Modified
- `command_center.py` — compute_urgency, _time_pressure, _energy_fit, _impact_score, _blocker_risk, get_today_tasks update
- `static/command-center/cc.js` — overwhelm UI, lighten button, WIP enforcement in ccStartTask

## Verification
- [x] Tasks sorted by urgency score, not insertion order
- [x] Frog task (highest score) pinned at top of queue
- [x] Quick wins section shows tasks under 5 min
- [x] Overwhelm banner appears at 12+ tasks
- [x] "Lighten my day" reduces queue to top 3
- [x] Only one timer can run at a time
