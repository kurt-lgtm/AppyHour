# Phase 4: Day-of-Week Rules + Morning Brief + Slack Trawl — Plan

**Status:** COMPLETE (retroactive)
**Created:** 2026-04-06 (retroactive from commit aa28b4f)

## Goal
Seed the day's task list automatically from recurring templates, surface a morning brief with operational counts, and parse Slack for actionable commitments that auto-generate tasks.

## Deliverables
- `seed_recurring_if_empty()` — 11 default templates mapped to Mon–Sat schedule
- `spawn_today_recurring()` — deduplicating daily instance creation from templates
- Morning brief card with orders/tickets/inventory/Slack/Gmail counts (passive store)
- Slack trawl: keyword classifier that creates tasks with type-specific checklists
- Energy check-in: time-of-day auto-degradation via `ccAutoEnergy()`

## Tasks
1. `seed_recurring_if_empty()` — insert 11 default recurring_tasks if table empty (Mon–Sat coverage)
2. `spawn_today_recurring()` — query today's day_of_week, create task instances, dedup on recurring_id+date
3. Call `spawn_today_recurring()` from `GET /api/cc/today` if today has no tasks yet
4. `POST /api/cc/brief` — store morning brief payload (orders, tickets, inventory alerts, Slack count, Gmail count)
5. `GET /api/cc/brief` — return today's brief or empty defaults
6. `ccFetchBrief()` / `ccRenderBrief()` — brief card at top of queue panel with colored count badges
7. `classify_commitment(message_text)` — keyword regex matching: carrier (reship), refund, subscription actions
8. `process_slack_trawl(messages)` — iterate messages, classify, create tasks via `create_task()`
9. `COMMITMENT_CHECKLISTS` dict — per-type checklist templates (e.g. reship: ["Find order", "Create reship", "Notify customer"])
10. `POST /api/cc/slack-trawl` — accepts array of Slack messages, returns created task count
11. `ccAutoEnergy()` — high→medium after 3PM, medium→low after 5PM; sets energy if not already set today

## Files Modified
- `command_center.py` — seed_recurring_if_empty, spawn_today_recurring, brief endpoints, slack trawl
- `app.py` — /api/cc/brief (GET/POST), /api/cc/slack-trawl route
- `static/command-center/cc.js` — ccFetchBrief, ccRenderBrief, ccAutoEnergy

## Verification
- [x] First load of the day spawns today's recurring tasks (no duplicates on reload)
- [x] 11 default templates present after fresh DB init
- [x] Morning brief card renders with count badges
- [x] Slack trawl classifies carrier/reship/refund/subscription keywords correctly
- [x] Created tasks include type-specific checklists from COMMITMENT_CHECKLISTS
- [x] ccAutoEnergy degrades energy level by time of day
