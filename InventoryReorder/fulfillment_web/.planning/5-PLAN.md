# Phase 5: Blocker Handling + Monitoring — Plan

**Status:** COMPLETE (retroactive)
**Created:** 2026-04-06 (retroactive from commit aa28b4f)

## Goal
Give blocked tasks a first-class UI with typed blocker forms, a "WAITING ON" section in the queue, and the groundwork for MCP-based resolution monitoring.

## Deliverables
- `create_blocker()` / `resolve_blocker()` with task status transitions
- 4 blocker types: person (who field), data (research note), unknown, toobig (GTD next-action prompt)
- Inline blocker form UI in cc.js; blocked tasks move to collapsed "WAITING ON" section
- `check_back_at` timestamp field; `monitor_source` stored but polling not yet implemented
- `ccResolveBlocker()` restores task to active queue

## Tasks
1. `create_blocker(task_id, type, **kwargs)` — insert blocker row, set task status=blocked
2. `resolve_blocker(blocker_id)` — mark resolved_at, set task status=active
3. Blocker schema: id, task_id, type, who, note, monitor_source, monitor_query, check_back_at, created_at, resolved_at
4. `GET /api/cc/blockers` — list active (resolved_at IS NULL)
5. `POST /api/cc/blockers` — create blocker, returns updated task
6. `POST /api/cc/blockers/:id/resolve` — resolve, returns task
7. `ccShowBlockerForm(taskId)` — inject inline form below active task panel
8. `ccBlockerFormHtml(taskId)` — 4 type buttons; person shows "who" input, toobig shows GTD prompt, data shows note textarea
9. On form submit: POST /api/cc/blockers, hide form, move card to WAITING ON section with animation
10. WAITING ON section — collapsed by default, shows count badge, expands on click
11. `ccResolveBlocker(blockerId)` — POST resolve, animate card back into active queue
12. `monitor_source` field accepted in create payload but no polling wired (placeholder for Phase 7+ MCP)

## Files Modified
- `command_center.py` — create_blocker, resolve_blocker, get_active_blockers
- `app.py` — /api/cc/blockers routes
- `static/command-center/cc.js` — ccShowBlockerForm, ccBlockerFormHtml, ccResolveBlocker, WAITING ON section

## Verification
- [x] Blocked button opens inline form with 4 type options
- [x] Person type shows "who" field; toobig shows next-action prompt
- [x] Blocking a task moves it to WAITING ON section
- [x] WAITING ON section collapsed by default with count badge
- [x] Resolve button restores task to active queue
- [x] check_back_at stored and visible in blocker card
