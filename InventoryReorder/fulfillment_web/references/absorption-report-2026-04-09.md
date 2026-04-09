# Absorption Report — Command Center Enhancement
**Date:** 2026-04-09
**Sources:** mission-control, personal-management-system, darekkay/dashboard, personal_dashboard

## TIER 1 — High Impact, Low Effort (Implement Now)

### 1. Decisions Queue (from mission-control)
Agents/processes request decisions with options → user answers → execution proceeds.
**Why:** CC chat panel already exists. Add structured decision requests with options instead of free-text only. Prevents "I forgot to respond to that."
**Effort:** Add `decisions` table (id, question, options[], answered_at, answer), route, UI card.

### 2. Notification Urgency Scoring (from PMS)
Days-until-due → severity: critical <0, error 0-14, warn 14-30, info >30.
**Why:** We have deadlines but no visual urgency escalation. Tasks approaching deadline should glow hotter.
**Effort:** Add to `compute_urgency()` — already has time_pressure, just needs visual tiers.

### 3. Fix Double-Load Bug
`ccLoad()` fires 2-6x on init → request storm.
**Why:** Visible in server logs. Wastes bandwidth, hits SQLite unnecessarily.
**Effort:** Add `_ccLoaded` guard flag + debounce.

### 4. Auto Build-Brief on Page Load
cc.js should call `/api/cc/build-brief` on open, not just read stale brief.
**Why:** Brief is empty unless manually populated. Should auto-aggregate.
**Effort:** One fetch call in `ccLoad()`.

## TIER 2 — Medium Impact, Medium Effort (Next Session)

### 5. Cost Tracking Per Chat (from mission-control)
Track input/output tokens + model per chat message. Show running total.
**Why:** Chat panel uses Claude API. Users should see cost. Budget awareness.
**Effort:** Extend `record_chat_cost()` (already exists), add UI display.

### 6. Activity Log Event Stream (from mission-control)
Append-only event log: task_created, task_completed, brief_built, slack_trawl_run, blocker_resolved.
**Why:** Audit trail. Weekly review can show timeline. "What happened this week" becomes data-driven.
**Effort:** New table + append on each action. UI timeline in weekly review.

### 7. Widget Registry Pattern (from darekkay/dashboard)
Metadata-driven widget system: type → dimensions, config schema, data source, update cycle.
**Why:** CC sidebar has fixed widgets. Registry enables add/remove/reorder without code changes.
**Effort:** Medium — refactor sidebar to widget-based rendering.

### 8. Modular API Wrappers (from personal_dashboard)
Each MCP source as a wrapper class with consistent output: `{data, updated_at, status}`.
**Why:** build_morning_brief() currently takes raw dicts. Standardize all MCP data sources.
**Effort:** Create wrapper pattern for Gorgias, Shopify, Slack, Gmail, inventory.

### 9. Schedule Reminder with Processed State (from PMS)
Separate recurring template from individual reminder instances. Track `processed` flag.
**Why:** Our `spawn_today_recurring()` creates tasks but doesn't track "was this reminder shown."
**Effort:** Add `processed` column to spawned tasks or separate reminders table.

## TIER 3 — Interesting, Evaluate Later

### 10. Daemon/Autopilot Mode (from mission-control)
Background process polling tasks, spawning Claude sessions, enforcing concurrency.
**Why:** Cool but overkill for single-user. Our CC is interactive, not autonomous.
**When:** After 32GB RAM upgrade. Could run overnight batch operations.

### 11. Agent Crews with Skills Injection (from mission-control)
Named agents with skill libraries, lead+collaborator roles.
**Why:** We use forge agents already. Mission-control's pattern is more formal.
**When:** If we need persistent agent identities across sessions.

### 12. Encrypted Vault for External Actions (from mission-control)
AES-256-GCM vault for credentials, autonomy levels, spend limits.
**Why:** We store creds in settings JSON. Vault is more secure.
**When:** When security audit flags credential storage.

### 13. Calendar Heatmap Visualization (from personal_dashboard)
Daily completion chains as heatmap — visual motivation loop.
**Why:** Streaks are numbers. Heatmap makes consistency visible.
**When:** After time analytics are implemented.

### 14. Issue Subrecord Pattern (from PMS)
Progress notes + contact attempts as separate entities per issue.
**Why:** Gorgias tickets could have local tracking annotations.
**When:** If CS workflow needs deeper case management.

## Summary

| Tier | Count | Focus |
|------|-------|-------|
| TIER 1 | 4 items | Decisions queue, urgency visual, debounce fix, auto-brief |
| TIER 2 | 5 items | Cost tracking, activity log, widgets, API wrappers, reminders |
| TIER 3 | 5 items | Daemon, agent crews, vault, heatmap, subrecords |
