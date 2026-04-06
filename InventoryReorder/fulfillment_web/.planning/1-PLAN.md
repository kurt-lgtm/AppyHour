# Phase 1: Task Engine + Data Model

## Objective
Build the backend task engine — create, read, update, delete tasks with recurring support, personal stream, per-task checklists, and urgency scoring. This is the foundation everything else builds on.

## Architecture Decision
**SQLite for tasks, JSON settings for config.**
- Tasks: SQLite (`~/.cc/command_center.db`) — high write frequency (subtask checks, timer, status changes)
- Config: Existing `inventory_reorder_settings.json` under `command_center_config` key — schedule, timer defaults, etc.
- Why SQLite over JSON: atomic writes per subtask, no full-file rewrite on every check, survives crashes, enables future queries (time analytics)

## Files to Create

### 1. `command_center.py` (~300 lines)
Core task engine module. Follows existing module pattern (like `curation_manager.py`).

**Data Model — Task:**
```python
task = {
    "id": "uuid4",
    "title": "Stop using OnTrac for Sarah M.",
    "type": "work",                    # work | personal
    "status": "active",                # active | done | blocked | waiting | archived
    "source": "slack-trawl",           # manual | recurring | slack-trawl | mcp-alert
    "source_ref": "slack:C04ABC123:1712345678.000100",  # link back to origin
    "priority": "high",               # high | medium | low (user-set)
    "energy": "medium",               # high | medium | low (what energy suits this)
    "deadline": "2026-04-08T19:00:00-04:00",  # nullable
    "estimated_minutes": 15,           # nullable, learned over time
    "actual_minutes": null,            # filled on completion
    "recurring_id": null,              # links to recurring_tasks table if auto-generated
    "day_of_week": null,               # 0=Mon..6=Sun, for recurring display
    "created_at": "2026-04-06T09:30:00-04:00",
    "completed_at": null,
    "urgency_score": 0.0,              # computed, not stored
    "notes": "",
    "tags": ["carrier", "customer-promise"]
}
```

**Data Model — Checklist Item:**
```python
checklist_item = {
    "id": "uuid4",
    "task_id": "parent-task-uuid",
    "title": "Find customer in Shopify",
    "position": 0,                     # display order
    "done": false,
    "completed_at": null
}
```

**Data Model — Recurring Task:**
```python
recurring_task = {
    "id": "uuid4",
    "title": "Weekly call with Tommy — cut order review",
    "type": "work",
    "day_of_week": 1,                  # 0=Mon..6=Sun (Tuesday)
    "time": "10:00",                   # display time, not alarm
    "energy": "high",
    "estimated_minutes": 45,
    "priority": "high",
    "checklist_template": [            # pre-populated on each instance
        "Review demand numbers",
        "Check inventory vs demand gaps",
        "Call Tommy",
        "Submit cut order"
    ],
    "active": true,
    "skip_on_low_energy": false
}
```

**Data Model — Blocker:**
```python
blocker = {
    "id": "uuid4",
    "task_id": "parent-task-uuid",
    "type": "person",                  # person | data | system | time | unknown
    "who": "Tommy",                    # nullable
    "note": "Waiting for MONG allocation decision",
    "monitor_source": "slack",         # slack | gmail | mcp | time | none
    "monitor_query": "Tommy",          # keyword to watch for
    "check_back_at": "2026-04-07T10:00:00-04:00",
    "created_at": "2026-04-06T14:00:00-04:00",
    "resolved_at": null
}
```

**Functions:**
```python
# Database
init_db() -> None                      # Create tables if not exist
get_db() -> sqlite3.Connection         # Thread-local connection

# Tasks CRUD
create_task(title, type, **kwargs) -> dict
get_task(task_id) -> dict | None
update_task(task_id, **kwargs) -> dict
delete_task(task_id) -> None
list_tasks(status, type, day_of_week) -> list[dict]

# Checklist
add_checklist_item(task_id, title, position) -> dict
toggle_checklist_item(item_id) -> dict
reorder_checklist(task_id, item_ids) -> None
get_checklist(task_id) -> list[dict]

# Recurring
create_recurring(title, day_of_week, **kwargs) -> dict
get_recurring_tasks() -> list[dict]
spawn_today_recurring() -> list[dict]   # Create today's instances from templates
update_recurring(recurring_id, **kwargs) -> dict
delete_recurring(recurring_id) -> None

# Blockers
create_blocker(task_id, type, **kwargs) -> dict
resolve_blocker(blocker_id) -> dict
get_active_blockers() -> list[dict]

# Urgency Scoring
compute_urgency(task) -> float          # Returns 0.0-1.0
  # energy_fit * 0.35 + time_pressure * 0.35 + impact * 0.20 + blocker_risk * 0.10

# Bulk
get_today_tasks(energy_level) -> list[dict]  # Sorted by urgency, filtered by day
get_personal_tasks() -> list[dict]
```

### 2. Routes added to `app.py` (~80 lines)

```python
# Task CRUD
@app.route('/api/cc/tasks', methods=['GET'])           # list (filter by status, type, day)
@app.route('/api/cc/tasks', methods=['POST'])          # create
@app.route('/api/cc/tasks/<id>', methods=['GET'])      # get one
@app.route('/api/cc/tasks/<id>', methods=['PATCH'])    # update
@app.route('/api/cc/tasks/<id>', methods=['DELETE'])   # delete

# Checklist
@app.route('/api/cc/tasks/<id>/checklist', methods=['GET'])     # get items
@app.route('/api/cc/tasks/<id>/checklist', methods=['POST'])    # add item
@app.route('/api/cc/tasks/<id>/checklist/<item_id>/toggle', methods=['POST'])  # check/uncheck
@app.route('/api/cc/tasks/<id>/checklist/reorder', methods=['POST'])  # reorder

# Recurring
@app.route('/api/cc/recurring', methods=['GET'])       # list all
@app.route('/api/cc/recurring', methods=['POST'])      # create
@app.route('/api/cc/recurring/<id>', methods=['PATCH']) # update
@app.route('/api/cc/recurring/<id>', methods=['DELETE'])# delete
@app.route('/api/cc/recurring/spawn', methods=['POST'])# spawn today's instances

# Blockers
@app.route('/api/cc/blockers', methods=['GET'])        # list active
@app.route('/api/cc/blockers', methods=['POST'])       # create
@app.route('/api/cc/blockers/<id>/resolve', methods=['POST'])  # resolve

# Today view
@app.route('/api/cc/today', methods=['GET'])           # today's prioritized list
```

### 3. Config in settings JSON

```python
# Added to inventory_reorder_settings.json under "command_center_config"
{
    "command_center_config": {
        "timer_default_minutes": 25,
        "wip_limit": 3,
        "energy_level": null,           # set each morning
        "energy_level_date": null,      # resets daily
        "weekly_schedule": {
            "0": ["Shipping review", "Gorgias triage", "Plan week"],
            "1": ["Tommy call 10AM", "Demand pull", "Cut order (7PM EST)", "Pay bills"],
            "2": ["React tool prep", "Inventory review", "Make PO"],
            "3": ["React tool run", "Swaps", "Shopify sync"],
            "4": ["Ship day", "RMFG email", "Gel packs", "Weekly review"],
            "5": ["Shipping monitoring"],
            "6": []
        },
        "slack_trawl_channel": "reship-and-order-requests",
        "slack_trawl_user": "kurt",
        "db_path": "~/.cc/command_center.db"
    }
}
```

## Implementation Steps

1. Create `~/.cc/` directory for database
2. Write `command_center.py` with SQLite schema + all CRUD functions
3. Write urgency scoring function with lookup tables
4. Add Flask routes to `app.py` (import command_center at top)
5. Seed initial recurring tasks from weekly schedule config
6. Test: create task, add checklist, toggle items, spawn recurring, compute urgency

## Do NOT
- Touch existing `app.js`, `styles.css`, or `index.html` (that's Phase 2)
- Use the JSON settings file for task storage (SQLite only)
- Add any UI (backend only this phase)
- Import heavy dependencies (SQLite is stdlib)

## Success Criteria
- `GET /api/cc/today` returns a prioritized task list
- Tasks have checklists that toggle independently
- Recurring tasks spawn instances on their designated day
- Urgency score computed correctly (energy × time × impact × blocker)
- Blockers created and resolved via API
- Personal vs work tasks separated by type field
- All data persists in SQLite across app restarts
