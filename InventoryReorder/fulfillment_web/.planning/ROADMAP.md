# Command Center — Roadmap v1.0

## Milestone 1.0: MVP — "Tell Me What To Do"

### Phase 1: Task Engine + Data Model
**Goal:** Core task CRUD — create, read, update, delete tasks with recurring support, personal stream, and per-task checklists.
**Delivers:** Backend task engine in a new `command_center.py` module. SQLite storage. API routes `/api/cc/*`. Task data model with urgency scoring. Each task supports a checklist (ordered subtask items with done/not-done state). Checklist templates for recurring tasks (e.g., "Process reship" always has: verify order, check inventory, create reship, notify customer, update Gorgias).
**Depends on:** Nothing (foundation phase)

### Phase 2: UI Shell + Tab Integration
**Goal:** Command Center tab appears first in the fulfillment app. Three-panel layout renders with skeleton loading.
**Delivers:** New tab in `index.html`, `static/command-center/cc.css`, `static/command-center/cc.js`. Dark navy theme. Task cards display. Keyboard nav (J/K/Enter/D).
**Depends on:** Phase 1 (needs API to fetch tasks)

### Phase 3: Timer + Task Execution Flow
**Goal:** Click Start on a task → timer counts, subtasks check off, Done/Blocked/Skip buttons work.
**Delivers:** `cc-timer.js`, active task view with expanded card, subtask list, arc-style countdown for deadlines, auto-pause on window unfocus.
**Depends on:** Phase 2 (needs task cards to expand into)

### Phase 4: Day-of-Week Rules + Morning Brief + Slack Trawl
**Goal:** Recurring tasks auto-populate on their day. Morning brief pulls MCP data and shows business pulse. Daily Slack trawl of #reship-and-order-requests creates tasks for promises made to customer service.
**Delivers:** Recurring task config in Settings tab, day-of-week engine, morning brief card with MCP aggregation, energy check-in gate, Slack channel trawl that parses messages for action items and creates tasks with checklists.
**Depends on:** Phase 1 (task engine), Phase 2 (UI to display)

### Phase 5: Blocker Handling + Monitoring
**Goal:** Mark task blocked → it disappears from view → system watches MCP sources → resurfaces when cleared.
**Delivers:** Blocker form (4 types), "Waiting On" section, MCP polling for resolution (Slack/Gmail keyword match), quiet chat notification on clear.
**Depends on:** Phase 3 (needs active task flow), Phase 4 (needs MCP integration)

### Phase 6: Priority Engine + Energy Scheduling
**Goal:** Tasks auto-sort by urgency score. Energy check-in adjusts order. Frog detection. WIP limits.
**Delivers:** Fully operationalized scoring (energy_fit × 0.35 + time_pressure × 0.35 + impact × 0.20 + blocker_risk × 0.10), afternoon energy degradation, "lightened day" option, overwhelm detection at 12 items.
**Depends on:** Phase 1 (task model), Phase 4 (energy check-in)

### Phase 7: Ask Claude Chat Panel
**Goal:** Chat panel on the right side that can pull live MCP data, draft emails, research questions, create follow-up tasks.
**Delivers:** `cc-chat.js`, SSE streaming via `/api/cc/chat/stream`, anthropic SDK integration, Haiku default with opt-in Deep Think button, action buttons (Send via Gmail, Add to tasks), $2/mo budget cap.
**Depends on:** Phase 2 (UI panel), Phase 4 (MCP integration for context injection)

### Phase 8: End-of-Day + Weekly Review
**Goal:** Wrap-up summary shows accomplishments. Friday weekly review pre-fills from week data. Streaks. Progress bars.
**Delivers:** EOD summary card (user-initiated after 4pm EDT), weekly review template, weekly streaks (pause-able), progress visualization, time analytics (estimated vs actual).
**Depends on:** Phase 3 (timer data), Phase 6 (priority data)

### Phase 9: Polish + Compassionate Design Pass
**Goal:** Full emotional design audit. Bad Day Protocol. Auto load shedding. Accessibility. Backup. Mobile companion.
**Delivers:** Triage flow for piled-up tasks, ARIA roles + companion icons, daily JSON backup to `~/.cc/snapshots/`, weekly Drive upload, idle detection, break reminders, onboarding flow (4-week ramp).
**Depends on:** All prior phases (polish pass)

## Phase Dependencies
```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 5
   │            │                      ↑
   │            └──→ Phase 4 ──────────┘
   │                   │
   │                   └──→ Phase 6
   │                   └──→ Phase 7
   │
   └──→ Phase 6
   └──→ Phase 8 (needs Phase 3 + 6)
                    
Phase 9: depends on all
```

## What's NOT in v1.0
- Mobile PWA companion (v1.1)
- Cloud sync between machines (v1.1)
- Calendar integration (v1.2)
- Team features / Tommy's view (v2.0)
