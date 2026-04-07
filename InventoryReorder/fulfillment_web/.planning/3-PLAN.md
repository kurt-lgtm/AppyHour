# Phase 3: Timer + Task Execution Flow — Plan

**Status:** COMPLETE (retroactive)
**Created:** 2026-04-06 (retroactive from commit aa28b4f)

## Goal
Build the active task execution panel with a live countdown timer, checklist progress, and action buttons so the operator has a clear focus surface while working a task.

## Deliverables
- Active task panel with 1-second interval timer and progress bar
- Checklist toggle with spring animation and pulse-on-completion
- Timer persistence via PATCH on stop; auto-pause on window unfocus
- Extend +10min, Skip, Blocked, Done action buttons

## Tasks
1. Implement `ccStartTask(taskId)` — builds and injects active task panel into focus pane
2. Timer: `setInterval` at 1s, elapsed display (MM:SS), bar fills proportional to `estimated_minutes`
3. Progress bar turns amber at 90%, red at 110% of estimated time
4. `ccStopTimer()` — clears interval, computes actual_minutes, PATCH /api/cc/tasks/:id
5. `ccToggleActiveCheck(itemId)` — POST toggle, spring CSS animation on checkbox, re-evaluate done state
6. Pulse animation on Done button when all checklist items complete
7. Auto-pause: `visibilitychange` + `blur` events; if unfocused >3 min, pause timer, show "Paused" badge
8. "Extend +10min" button — increments estimated_minutes, updates bar denominator
9. "Skip" button — marks task as skipped, advances to next task in queue
10. "Blocked" button — opens inline blocker form (wired to Phase 5 handler)
11. "Done" button — PATCH status=done, record actual_minutes, animate card out, show next task

## Files Modified
- `static/command-center/cc.js` — ccStartTask, timer logic, checklist toggles, action buttons

## Verification
- [x] Timer starts on task activation and counts up in real time
- [x] Progress bar fills relative to estimated_minutes
- [x] Checklist items toggle with animation; Done button pulses when all complete
- [x] actual_minutes saved via PATCH on Done
- [x] Timer auto-pauses after 3 min window unfocus
- [x] Extend, Skip, Blocked, Done buttons all functional
