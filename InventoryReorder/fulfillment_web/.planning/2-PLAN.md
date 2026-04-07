# Phase 2: UI Shell + Tab Integration — Plan

**Status:** COMPLETE (retroactive)
**Created:** 2026-04-06 (retroactive from commit aa28b4f)

## Goal
Add the Command Center tab to the fulfillment web app and establish the dark navy two-panel layout with keyboard navigation and energy controls.

## Deliverables
- Command Center tab wired into index.html tab system
- cc.css (828 lines) — dark navy theme, 2-panel layout (focus panel left, queue panel right)
- cc.js — full render pipeline: ccLoad(), ccFetchToday(), ccRender(), ccRenderCard(), ccRenderList()
- Keyboard nav (J/K/Enter/D/B/Escape) and energy buttons (High/Med/Low) in header

## Tasks
1. Add Command Center tab button and panel div to `templates/index.html` (lines 632–739)
2. Create `static/command-center/cc.css` — dark navy `#0d0f1a` base, 2-panel flex layout, card styles
3. Create `static/command-center/cc.js` with module init pattern matching existing tabs
4. Implement `ccLoad()` — entry point called on tab activation
5. Implement `ccFetchToday()` — GET /api/cc/today with energy param
6. Implement `ccRender(data)` — splits tasks into frog/quick_wins/today/blocked/personal sections
7. Implement `ccRenderCard(task)` — task card HTML with checklist preview, energy badge, time estimate
8. Implement `ccRenderList(tasks, section)` — renders a section group with header + cards
9. Keyboard nav: J/K move focus, Enter starts task, D marks done, B opens blocker, Escape cancels
10. Energy header buttons: High/Med/Low — POST energy to settings, re-render with new sort

## Files Modified
- `templates/index.html` — tab button + panel container
- `static/command-center/cc.css` — new file, 828 lines
- `static/command-center/cc.js` — new file, initial render pipeline

## Verification
- [x] Command Center tab renders without errors on activation
- [x] Tasks load and render in correct sections (frog, quick wins, today, blocked)
- [x] 2-panel layout: focus panel (left) + task queue (right)
- [x] J/K keyboard navigation moves focus between cards
- [x] Energy buttons update display and re-fetch sorted tasks
