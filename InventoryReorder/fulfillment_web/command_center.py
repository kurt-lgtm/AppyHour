"""Command Center — Task engine with SQLite storage, urgency scoring, and checklist support."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DB_DIR = Path.home() / ".cc"
DB_PATH = DB_DIR / "command_center.db"

_local = threading.local()


def get_db() -> sqlite3.Connection:
    """Thread-local SQLite connection with row factory."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'work',
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'manual',
            source_ref TEXT,
            priority TEXT NOT NULL DEFAULT 'medium',
            energy TEXT NOT NULL DEFAULT 'medium',
            deadline TEXT,
            estimated_minutes INTEGER,
            actual_minutes INTEGER,
            recurring_id TEXT,
            day_of_week INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            notes TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            FOREIGN KEY (recurring_id) REFERENCES recurring_tasks(id)
        );

        CREATE TABLE IF NOT EXISTS checklist_items (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            title TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recurring_tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'work',
            day_of_week INTEGER NOT NULL,
            time TEXT DEFAULT '09:00',
            energy TEXT NOT NULL DEFAULT 'medium',
            estimated_minutes INTEGER,
            priority TEXT NOT NULL DEFAULT 'medium',
            checklist_template TEXT DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1,
            skip_on_low_energy INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS blockers (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'unknown',
            who TEXT,
            note TEXT DEFAULT '',
            monitor_source TEXT DEFAULT 'none',
            monitor_query TEXT,
            check_back_at TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type);
        CREATE INDEX IF NOT EXISTS idx_tasks_day ON tasks(day_of_week);
        CREATE INDEX IF NOT EXISTS idx_checklist_task ON checklist_items(task_id);
        CREATE INDEX IF NOT EXISTS idx_blockers_task ON blockers(task_id);
        CREATE INDEX IF NOT EXISTS idx_blockers_resolved ON blockers(resolved_at);

        CREATE TABLE IF NOT EXISTS morning_briefs (
            date TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TZ = ZoneInfo("America/New_York")


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Tasks CRUD
# ---------------------------------------------------------------------------


def create_task(
    title: str,
    type: str = "work",
    *,
    source: str = "manual",
    source_ref: str | None = None,
    priority: str = "medium",
    energy: str = "medium",
    deadline: str | None = None,
    estimated_minutes: int | None = None,
    recurring_id: str | None = None,
    day_of_week: int | None = None,
    notes: str = "",
    tags: list[str] | None = None,
    checklist: list[str] | None = None,
) -> dict:
    """Create a task. Optionally include checklist items."""
    db = get_db()
    task_id = _new_id()
    now = _now_iso()
    tags_json = json.dumps(tags or [])

    db.execute(
        """INSERT INTO tasks (id, title, type, status, source, source_ref, priority,
           energy, deadline, estimated_minutes, recurring_id, day_of_week,
           created_at, notes, tags)
           VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            task_id,
            title,
            type,
            source,
            source_ref,
            priority,
            energy,
            deadline,
            estimated_minutes,
            recurring_id,
            day_of_week,
            now,
            notes,
            tags_json,
        ),
    )

    if checklist:
        for i, item_title in enumerate(checklist):
            db.execute(
                "INSERT INTO checklist_items (id, task_id, title, position) VALUES (?, ?, ?, ?)",
                (_new_id(), task_id, item_title, i),
            )

    db.commit()
    return get_task(task_id)


def get_task(task_id: str) -> dict | None:
    """Get a single task with its checklist and active blocker."""
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    task["tags"] = json.loads(task.get("tags", "[]"))
    task["checklist"] = get_checklist(task_id)
    task["blocker"] = _get_active_blocker_for_task(task_id)
    return task


def update_task(task_id: str, **kwargs) -> dict | None:
    """Update task fields. Pass only fields to change."""
    db = get_db()
    allowed = {
        "title",
        "type",
        "status",
        "priority",
        "energy",
        "deadline",
        "estimated_minutes",
        "actual_minutes",
        "notes",
        "tags",
        "completed_at",
        "created_at",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_task(task_id)

    if "tags" in updates and isinstance(updates["tags"], list):
        updates["tags"] = json.dumps(updates["tags"])

    if updates.get("status") == "done" and "completed_at" not in updates:
        updates["completed_at"] = _now_iso()

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]
    db.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
    db.commit()
    return get_task(task_id)


def delete_task(task_id: str) -> None:
    """Delete a task and its checklist items + blockers (CASCADE)."""
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()


def list_tasks(
    status: str | None = None,
    type: str | None = None,
    day_of_week: int | None = None,
) -> list[dict]:
    """List tasks with optional filters."""
    db = get_db()
    conditions = []
    params = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if type:
        conditions.append("type = ?")
        params.append(type)
    if day_of_week is not None:
        conditions.append("(day_of_week = ? OR day_of_week IS NULL)")
        params.append(day_of_week)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = db.execute(f"SELECT * FROM tasks {where} ORDER BY created_at DESC", params).fetchall()

    tasks = []
    for row in rows:
        task = dict(row)
        task["tags"] = json.loads(task.get("tags", "[]"))
        task["checklist"] = get_checklist(task["id"])
        task["blocker"] = _get_active_blocker_for_task(task["id"])
        tasks.append(task)
    return tasks


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------


def get_checklist(task_id: str) -> list[dict]:
    """Get ordered checklist items for a task."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM checklist_items WHERE task_id = ? ORDER BY position",
        (task_id,),
    ).fetchall()
    return _rows_to_list(rows)


def add_checklist_item(task_id: str, title: str, position: int | None = None) -> dict:
    """Add a checklist item. Auto-positions at end if position not given."""
    db = get_db()
    if position is None:
        row = db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM checklist_items WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        position = row["next_pos"]

    item_id = _new_id()
    db.execute(
        "INSERT INTO checklist_items (id, task_id, title, position) VALUES (?, ?, ?, ?)",
        (item_id, task_id, title, position),
    )
    db.commit()
    return _row_to_dict(db.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone())


def toggle_checklist_item(item_id: str) -> dict:
    """Toggle a checklist item's done state."""
    db = get_db()
    row = db.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    new_done = 0 if row["done"] else 1
    completed_at = _now_iso() if new_done else None
    db.execute(
        "UPDATE checklist_items SET done = ?, completed_at = ? WHERE id = ?",
        (new_done, completed_at, item_id),
    )
    db.commit()
    return _row_to_dict(db.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone())


def reorder_checklist(task_id: str, item_ids: list[str]) -> None:
    """Reorder checklist items by setting position from list order."""
    db = get_db()
    for i, item_id in enumerate(item_ids):
        db.execute(
            "UPDATE checklist_items SET position = ? WHERE id = ? AND task_id = ?",
            (i, item_id, task_id),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Recurring Tasks
# ---------------------------------------------------------------------------


def create_recurring(
    title: str,
    day_of_week: int,
    *,
    type: str = "work",
    time: str = "09:00",
    energy: str = "medium",
    estimated_minutes: int | None = None,
    priority: str = "medium",
    checklist_template: list[str] | None = None,
    skip_on_low_energy: bool = False,
) -> dict:
    """Create a recurring task template."""
    db = get_db()
    rec_id = _new_id()
    template_json = json.dumps(checklist_template or [])

    db.execute(
        """INSERT INTO recurring_tasks (id, title, type, day_of_week, time, energy,
           estimated_minutes, priority, checklist_template, active, skip_on_low_energy)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (
            rec_id,
            title,
            type,
            day_of_week,
            time,
            energy,
            estimated_minutes,
            priority,
            template_json,
            int(skip_on_low_energy),
        ),
    )
    db.commit()
    return get_recurring(rec_id)


def get_recurring(recurring_id: str) -> dict | None:
    """Get a single recurring task template."""
    db = get_db()
    row = db.execute("SELECT * FROM recurring_tasks WHERE id = ?", (recurring_id,)).fetchone()
    if row is None:
        return None
    rec = dict(row)
    rec["checklist_template"] = json.loads(rec.get("checklist_template", "[]"))
    rec["active"] = bool(rec["active"])
    rec["skip_on_low_energy"] = bool(rec["skip_on_low_energy"])
    return rec


def list_recurring() -> list[dict]:
    """List all recurring task templates."""
    db = get_db()
    rows = db.execute("SELECT * FROM recurring_tasks ORDER BY day_of_week, time").fetchall()
    result = []
    for row in rows:
        rec = dict(row)
        rec["checklist_template"] = json.loads(rec.get("checklist_template", "[]"))
        rec["active"] = bool(rec["active"])
        rec["skip_on_low_energy"] = bool(rec["skip_on_low_energy"])
        result.append(rec)
    return result


def update_recurring(recurring_id: str, **kwargs) -> dict | None:
    """Update recurring task fields."""
    db = get_db()
    allowed = {
        "title",
        "type",
        "day_of_week",
        "time",
        "energy",
        "estimated_minutes",
        "priority",
        "checklist_template",
        "active",
        "skip_on_low_energy",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_recurring(recurring_id)

    if "checklist_template" in updates and isinstance(updates["checklist_template"], list):
        updates["checklist_template"] = json.dumps(updates["checklist_template"])
    if "active" in updates:
        updates["active"] = int(updates["active"])
    if "skip_on_low_energy" in updates:
        updates["skip_on_low_energy"] = int(updates["skip_on_low_energy"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [recurring_id]
    db.execute(f"UPDATE recurring_tasks SET {set_clause} WHERE id = ?", values)
    db.commit()
    return get_recurring(recurring_id)


def delete_recurring(recurring_id: str) -> None:
    """Delete a recurring task template."""
    db = get_db()
    db.execute("DELETE FROM recurring_tasks WHERE id = ?", (recurring_id,))
    db.commit()


def spawn_today_recurring(energy_level: str = "medium") -> list[dict]:
    """Create today's task instances from active recurring templates.

    Skips templates that already spawned today (dedup by recurring_id + date).
    Skips low-energy-skip templates when energy is low.
    """
    db = get_db()
    today_dow = datetime.now(_TZ).weekday()
    today_str = date.today().isoformat()

    templates = db.execute(
        "SELECT * FROM recurring_tasks WHERE active = 1 AND day_of_week = ?",
        (today_dow,),
    ).fetchall()

    spawned = []
    for tmpl in templates:
        tmpl = dict(tmpl)
        tmpl["checklist_template"] = json.loads(tmpl.get("checklist_template", "[]"))

        # Skip if low energy and template says skip
        if energy_level == "low" and tmpl.get("skip_on_low_energy"):
            continue

        # Dedup: check if already spawned today
        existing = db.execute(
            "SELECT id FROM tasks WHERE recurring_id = ? AND created_at LIKE ?",
            (tmpl["id"], f"{today_str}%"),
        ).fetchone()
        if existing:
            continue

        task = create_task(
            title=tmpl["title"],
            type=tmpl.get("type", "work"),
            source="recurring",
            priority=tmpl.get("priority", "medium"),
            energy=tmpl.get("energy", "medium"),
            estimated_minutes=tmpl.get("estimated_minutes"),
            recurring_id=tmpl["id"],
            day_of_week=tmpl["day_of_week"],
            checklist=tmpl.get("checklist_template", []),
        )
        spawned.append(task)

    return spawned


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------


def create_blocker(
    task_id: str,
    type: str = "unknown",
    *,
    who: str | None = None,
    note: str = "",
    monitor_source: str = "none",
    monitor_query: str | None = None,
    check_back_at: str | None = None,
) -> dict:
    """Create a blocker and set the task status to 'blocked'."""
    db = get_db()
    blocker_id = _new_id()
    now = _now_iso()

    db.execute(
        """INSERT INTO blockers (id, task_id, type, who, note, monitor_source,
           monitor_query, check_back_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (blocker_id, task_id, type, who, note, monitor_source, monitor_query, check_back_at, now),
    )
    db.execute("UPDATE tasks SET status = 'blocked' WHERE id = ?", (task_id,))
    db.commit()
    return _row_to_dict(db.execute("SELECT * FROM blockers WHERE id = ?", (blocker_id,)).fetchone())


def resolve_blocker(blocker_id: str) -> dict:
    """Resolve a blocker and set the task back to 'active'."""
    db = get_db()
    now = _now_iso()
    db.execute("UPDATE blockers SET resolved_at = ? WHERE id = ?", (now, blocker_id))

    row = db.execute("SELECT task_id FROM blockers WHERE id = ?", (blocker_id,)).fetchone()
    if row:
        # Only reactivate if no other active blockers
        other = db.execute(
            "SELECT id FROM blockers WHERE task_id = ? AND resolved_at IS NULL AND id != ?",
            (row["task_id"], blocker_id),
        ).fetchone()
        if not other:
            db.execute("UPDATE tasks SET status = 'active' WHERE id = ?", (row["task_id"],))

    db.commit()
    return _row_to_dict(db.execute("SELECT * FROM blockers WHERE id = ?", (blocker_id,)).fetchone())


def get_active_blockers() -> list[dict]:
    """Get all unresolved blockers."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM blockers WHERE resolved_at IS NULL ORDER BY created_at",
    ).fetchall()
    return _rows_to_list(rows)


def _get_active_blocker_for_task(task_id: str) -> dict | None:
    """Get the active blocker for a specific task (if any)."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM blockers WHERE task_id = ? AND resolved_at IS NULL LIMIT 1",
        (task_id,),
    ).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Urgency Scoring
# ---------------------------------------------------------------------------

# Energy fit cross-table: user_energy → task_energy → score (0.0-1.0)
ENERGY_FIT = {
    "high": {"high": 1.0, "medium": 0.7, "low": 0.3},
    "medium": {"high": 0.5, "medium": 1.0, "low": 0.7},
    "low": {"high": 0.1, "medium": 0.5, "low": 1.0},
}

# Impact by priority
IMPACT_SCORE = {"high": 1.0, "medium": 0.6, "low": 0.3}

# Blocker risk by status
BLOCKER_RISK = {"active": 1.0, "blocked": 0.1, "waiting": 0.2, "done": 0.0, "archived": 0.0}


def _time_pressure(task: dict) -> float:
    """Compute time pressure based on deadline proximity. 0.0 = no pressure, 1.0 = overdue/imminent."""
    deadline_str = task.get("deadline")
    if not deadline_str:
        return 0.3  # No deadline = mild baseline

    try:
        deadline = datetime.fromisoformat(deadline_str)
        now = datetime.now(_TZ)
        hours_until = (deadline - now).total_seconds() / 3600

        if hours_until <= 0:
            return 1.0  # Overdue
        elif hours_until <= 2:
            return 0.95  # Imminent
        elif hours_until <= 8:
            return 0.8  # Today
        elif hours_until <= 24:
            return 0.6  # Tomorrow
        elif hours_until <= 72:
            return 0.4  # This week
        else:
            return 0.2  # Far out
    except (ValueError, TypeError):
        return 0.3


def compute_urgency(task: dict, energy_level: str = "medium") -> float:
    """Compute urgency score for a task. Returns 0.0-1.0.

    Score = energy_fit * 0.35 + time_pressure * 0.35 + impact * 0.20 + blocker_risk * 0.10
    """
    energy_fit = ENERGY_FIT.get(energy_level, {}).get(task.get("energy", "medium"), 0.5)
    time_press = _time_pressure(task)
    impact = IMPACT_SCORE.get(task.get("priority", "medium"), 0.6)
    blocker = BLOCKER_RISK.get(task.get("status", "active"), 0.5)

    return (energy_fit * 0.35) + (time_press * 0.35) + (impact * 0.20) + (blocker * 0.10)


# ---------------------------------------------------------------------------
# Today View
# ---------------------------------------------------------------------------


def get_today_tasks(energy_level: str = "medium") -> dict:
    """Get today's prioritized task list, separated by category.

    Returns {frog, quick_wins, today, blocked, personal, completed_today}.
    """
    db = get_db()
    today_str = date.today().isoformat()

    # Active work tasks (not blocked, not done)
    work_tasks = list_tasks(status="active", type="work")
    blocked_tasks = list_tasks(status="blocked", type="work")
    personal_tasks = list_tasks(status="active", type="personal")

    # Completed today
    completed = db.execute(
        "SELECT * FROM tasks WHERE status = 'done' AND completed_at LIKE ?",
        (f"{today_str}%",),
    ).fetchall()
    completed = [dict(r) for r in completed]

    # Score and sort work tasks
    for task in work_tasks:
        task["urgency_score"] = compute_urgency(task, energy_level)
    work_tasks.sort(key=lambda t: t["urgency_score"], reverse=True)

    # Categorize
    frog = None
    quick_wins = []
    today = []

    for task in work_tasks:
        est = task.get("estimated_minutes") or 30
        if est <= 5:
            quick_wins.append(task)
        elif frog is None and task.get("energy") in ("high", "medium"):
            frog = task
        else:
            today.append(task)

    # Score personal too
    for task in personal_tasks:
        task["urgency_score"] = compute_urgency(task, energy_level)
    personal_tasks.sort(key=lambda t: t["urgency_score"], reverse=True)

    return {
        "frog": frog,
        "quick_wins": quick_wins,
        "today": today,
        "blocked": blocked_tasks,
        "personal": personal_tasks,
        "completed_today": completed,
        "energy_level": energy_level,
        "date": today_str,
        "day_of_week": datetime.now(_TZ).strftime("%A"),
    }


# ---------------------------------------------------------------------------
# Seed defaults
# ---------------------------------------------------------------------------

DEFAULT_RECURRING = [
    {
        "title": "Shipping review + Gorgias triage",
        "day_of_week": 0,
        "energy": "medium",
        "priority": "high",
        "estimated_minutes": 30,
        "checklist_template": ["Check weekend shipping issues", "Review Gorgias queue", "Resolve food safety tickets"],
    },
    {
        "title": "Plan the week",
        "day_of_week": 0,
        "energy": "high",
        "priority": "medium",
        "estimated_minutes": 20,
        "checklist_template": ["Review last week's carry-forwards", "Check upcoming deadlines", "Set top 3 priorities"],
    },
    {
        "title": "Weekly call with Tommy — cut order review",
        "day_of_week": 1,
        "energy": "high",
        "priority": "high",
        "estimated_minutes": 45,
        "time": "10:00",
        "checklist_template": [
            "Review demand numbers",
            "Check inventory vs demand gaps",
            "Discuss allocations with Tommy",
            "Submit cut order by 7PM EST",
        ],
    },
    {
        "title": "Pull Recharge demand",
        "day_of_week": 1,
        "energy": "medium",
        "priority": "high",
        "estimated_minutes": 15,
        "checklist_template": ["Run demand pull", "Review charge counts", "Flag anomalies"],
    },
    {
        "title": "Pay bills",
        "day_of_week": 1,
        "energy": "low",
        "priority": "medium",
        "estimated_minutes": 20,
        "skip_on_low_energy": True,
    },
    {
        "title": "React tool prep + inventory review",
        "day_of_week": 2,
        "energy": "medium",
        "priority": "high",
        "estimated_minutes": 30,
        "checklist_template": [
            "Review inventory snapshot",
            "Check depletion since last week",
            "Prep React tool inputs",
        ],
    },
    {
        "title": "Make PO for next week",
        "day_of_week": 2,
        "energy": "medium",
        "priority": "high",
        "estimated_minutes": 20,
        "checklist_template": ["Check runway for all SKUs", "Draft PO quantities", "Send PO to RMFG"],
    },
    {
        "title": "React tool run + swaps + Shopify sync",
        "day_of_week": 3,
        "energy": "high",
        "priority": "high",
        "estimated_minutes": 90,
        "checklist_template": [
            "Run React tool",
            "Review output for errors",
            "Execute swap cascade",
            "Sync to Shopify",
            "Verify order counts",
        ],
    },
    {
        "title": "Ship day prep + RMFG email",
        "day_of_week": 4,
        "energy": "high",
        "priority": "high",
        "estimated_minutes": 60,
        "checklist_template": [
            "Gel pack calculations",
            "Finalize shipping manifest",
            "Email RMFG final files",
            "Confirm carrier routing",
        ],
    },
    {
        "title": "Weekly review",
        "day_of_week": 4,
        "energy": "low",
        "priority": "medium",
        "estimated_minutes": 30,
        "time": "16:00",
        "checklist_template": [
            "Review completed tasks",
            "Check Waiting On list",
            "Review backlog",
            "Plan next week's priorities",
            "Archive done items",
        ],
    },
    {
        "title": "Shipping monitoring",
        "day_of_week": 5,
        "energy": "low",
        "priority": "medium",
        "estimated_minutes": 20,
        "checklist_template": ["Check carrier tracking", "Flag delays", "Review delivery stats"],
    },
]


def seed_recurring_if_empty() -> int:
    """Seed default recurring tasks if none exist. Returns count seeded."""
    existing = list_recurring()
    if existing:
        return 0

    count = 0
    for rec in DEFAULT_RECURRING:
        create_recurring(**rec)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Slack Trawl — Commitment Detection
# ---------------------------------------------------------------------------

# Slack config
SLACK_CHANNEL_ID = "C095UVCKCBB"  # #reship-and-order-requests
SLACK_USER_ID = "U08R19137UL"  # Kurt

# Commitment type → checklist template
COMMITMENT_CHECKLISTS = {
    "carrier_change": [
        "Find customer in Shopify",
        "Add routing override tag",
        "Confirm with CS in Slack thread",
    ],
    "reship": [
        "Verify original order in Shopify",
        "Check inventory for correct items",
        "Create reship order",
        "Notify customer via Gorgias",
        "Update Gorgias ticket as resolved",
    ],
    "refund": [
        "Open order in Shopify",
        "Process refund (correct amount)",
        "Confirm refund processed",
        "Notify CS in thread",
    ],
    "subscription_change": [
        "Find customer in Recharge",
        "Make the change",
        "Verify change applied",
        "Notify CS in thread",
    ],
    "follow_up": [],  # User fills in next action
}

# Keywords that signal each commitment type
COMMITMENT_SIGNALS = {
    "carrier_change": [
        "don't use",
        "stop using",
        "no more",
        "switch to",
        "ontrac",
        "usps",
        "ups",
        "fedex",
        "carrier",
        "not use",
        "won't use",
        "will not use",
    ],
    "reship": [
        "reship",
        "re-ship",
        "send another",
        "send replacement",
        "ship again",
        "send again",
        "replacement",
    ],
    "refund": [
        "refund",
        "credit",
        "reimburse",
        "money back",
        "charge back",
        "comp",
        "free",
    ],
    "subscription_change": [
        "switch to",
        "change to",
        "swap to",
        "move to",
        "cancel",
        "skip",
        "pause",
        "add to",
        "curation",
        "cmed",
        "mong",
        "spm",
        "mdt",
    ],
    "follow_up": [
        "i'll look into",
        "let me check",
        "i'll get back",
        "will check",
        "will look",
        "looking into",
        "i'll find out",
        "let me see",
        "will follow up",
    ],
}


def classify_commitment(text: str) -> tuple[str, str]:
    """Classify a message into a commitment type and generate a task title.

    Returns (commitment_type, suggested_title).
    """
    text_lower = text.lower()

    # Check each type in priority order
    for ctype in ["reship", "refund", "carrier_change", "subscription_change", "follow_up"]:
        signals = COMMITMENT_SIGNALS[ctype]
        for signal in signals:
            if signal in text_lower:
                # Generate title from the commitment
                titles = {
                    "carrier_change": f"Carrier change: {text[:80]}",
                    "reship": f"Reship: {text[:80]}",
                    "refund": f"Refund: {text[:80]}",
                    "subscription_change": f"Subscription change: {text[:80]}",
                    "follow_up": f"Follow up: {text[:80]}",
                }
                return ctype, titles[ctype]

    return "follow_up", f"Follow up: {text[:80]}"


def process_slack_trawl(messages: list[dict]) -> list[dict]:
    """Process Slack messages from the trawl and create tasks.

    Handles two types:
    1. Kurt's messages (commitments/promises he made) → tasks with checklists
    2. CS requests TO Kurt (mentions @Kurt) that he hasn't replied to → "Respond" tasks

    messages: list of {text, ts, thread_ts, user, channel} from Slack MCP.
    Deduplicates by message timestamp.

    Returns list of created tasks.
    """
    db = get_db()
    created = []

    # Collect thread_ts values where Kurt has replied (to skip already-handled requests)
    kurt_thread_replies = set()
    for msg in messages:
        if msg.get("user") == SLACK_USER_ID and msg.get("thread_ts"):
            kurt_thread_replies.add(msg["thread_ts"])

    for msg in messages:
        text = msg.get("text", "").strip()
        if not text or len(text) < 10:
            continue

        ts = msg.get("ts", "")
        source_ref = f"slack:{SLACK_CHANNEL_ID}:{ts}"

        # Dedup: skip if task already exists for this message
        existing = db.execute(
            "SELECT id FROM tasks WHERE source_ref = ?",
            (source_ref,),
        ).fetchone()
        if existing:
            continue

        if msg.get("user") == SLACK_USER_ID:
            # Kurt's own message — classify as commitment
            ctype, title = classify_commitment(text)
            checklist = COMMITMENT_CHECKLISTS.get(ctype, [])

            task = create_task(
                title=title,
                type="work",
                source="slack-trawl",
                source_ref=source_ref,
                priority="high" if ctype in ("reship", "refund") else "medium",
                energy="medium",
                notes=f"Your message: {text}\nThread: {msg.get('thread_ts', 'N/A')}",
                tags=["slack-promise", ctype],
                checklist=checklist if checklist else None,
            )
            created.append(task)

        elif f"<@{SLACK_USER_ID}" in text:
            # CS mentioned Kurt — check if he already replied in the thread
            thread_ts = msg.get("thread_ts") or ts
            if thread_ts in kurt_thread_replies:
                continue  # Already replied, skip

            # Determine what CS is asking for
            text_lower = text.lower()
            if "reship" in text_lower:
                title = f"Respond: reship request — {text[:60]}"
                priority = "high"
            elif "credit" in text_lower or "courtesy" in text_lower:
                title = f"Respond: credit/courtesy request — {text[:60]}"
                priority = "high"
            elif "swap" in text_lower or "exclude" in text_lower or "allergy" in text_lower:
                title = f"Respond: swap/allergy request — {text[:60]}"
                priority = "medium"
            elif "missing" in text_lower:
                title = f"Respond: missing items — {text[:60]}"
                priority = "high"
            else:
                title = f"Respond: CS request — {text[:60]}"
                priority = "medium"

            task = create_task(
                title=title,
                type="work",
                source="slack-trawl",
                source_ref=source_ref,
                priority=priority,
                energy="medium",
                estimated_minutes=5,
                notes=f"CS message: {text}\nThread: {thread_ts}",
                tags=["slack-request", "needs-response"],
                checklist=[
                    "Read the full thread in Slack",
                    "Decide: approve, deny, or investigate",
                    "Reply in thread",
                ],
            )
            created.append(task)

    return created


# ---------------------------------------------------------------------------
# Morning Brief
# ---------------------------------------------------------------------------


def store_morning_brief(brief_data: dict) -> None:
    """Store morning brief data in the database for the UI to display.

    brief_data should contain:
        orders_unfulfilled: int
        gorgias_open: int
        gorgias_food_safety: int
        inventory_alerts: list[{sku, qty, runway_weeks}]
        slack_unreads: int
        gmail_unreads: int
        slack_trawl_created: int
    """
    db = get_db()
    today_str = date.today().isoformat()

    db.execute(
        "INSERT OR REPLACE INTO morning_briefs (date, data, created_at) VALUES (?, ?, ?)",
        (today_str, json.dumps(brief_data), _now_iso()),
    )
    db.commit()


def get_morning_brief() -> dict | None:
    """Get today's morning brief data."""
    db = get_db()
    today_str = date.today().isoformat()

    row = db.execute(
        "SELECT data FROM morning_briefs WHERE date = ?",
        (today_str,),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["data"])


# ---------------------------------------------------------------------------
# Stats / Streaks
# ---------------------------------------------------------------------------


def get_streaks() -> list[dict]:
    """Calculate weekly streaks for key recurring tasks.

    A streak counts consecutive weeks where the task was completed on its day.
    """
    db = get_db()
    streaks = []

    # Get all recurring tasks
    recurring = list_recurring()
    for rec in recurring:
        if not rec.get("active"):
            continue

        # Count consecutive weeks of completion (looking back)
        weeks = 0
        check_date = date.today()
        while weeks < 52:  # Max 1 year lookback
            # Find the most recent occurrence of this day-of-week before check_date
            days_back = (check_date.weekday() - rec["day_of_week"]) % 7
            if days_back == 0 and check_date == date.today():
                days_back = 0  # Today counts
            target_date = check_date - timedelta(days=days_back)

            if target_date > date.today():
                target_date -= timedelta(days=7)

            target_str = target_date.isoformat()

            # Check if a task from this recurring was completed on that date
            completed = db.execute(
                """SELECT id FROM tasks
                   WHERE recurring_id = ? AND status = 'done'
                   AND completed_at LIKE ?""",
                (rec["id"], f"{target_str}%"),
            ).fetchone()

            if completed:
                weeks += 1
                check_date = target_date - timedelta(days=1)
            else:
                break

        if weeks > 0:
            streaks.append(
                {
                    "title": rec["title"],
                    "weeks": weeks,
                    "day_of_week": rec["day_of_week"],
                }
            )

    streaks.sort(key=lambda s: s["weeks"], reverse=True)
    return streaks


def get_daily_stats() -> dict:
    """Get stats for today: completed, total, time tracked."""
    db = get_db()
    today_str = date.today().isoformat()

    completed = db.execute(
        "SELECT COUNT(*) as n FROM tasks WHERE status = 'done' AND completed_at LIKE ?",
        (f"{today_str}%",),
    ).fetchone()["n"]

    total_minutes = db.execute(
        "SELECT COALESCE(SUM(actual_minutes), 0) as n FROM tasks WHERE completed_at LIKE ?",
        (f"{today_str}%",),
    ).fetchone()["n"]

    active = db.execute(
        "SELECT COUNT(*) as n FROM tasks WHERE status = 'active'",
    ).fetchone()["n"]

    blocked = db.execute(
        "SELECT COUNT(*) as n FROM tasks WHERE status = 'blocked'",
    ).fetchone()["n"]

    return {
        "completed_today": completed,
        "active": active,
        "blocked": blocked,
        "minutes_tracked": total_minutes,
        "date": today_str,
    }


# ---------------------------------------------------------------------------
# End of Day Summary
# ---------------------------------------------------------------------------


def get_eod_summary() -> dict:
    """Generate end-of-day wrap-up summary.

    Shows what was accomplished (not what was missed).
    """
    db = get_db()
    today_str = date.today().isoformat()
    now = datetime.now(_TZ)

    # Completed today
    completed = db.execute(
        """SELECT title, actual_minutes, source, completed_at
           FROM tasks WHERE status = 'done' AND completed_at LIKE ?
           ORDER BY completed_at""",
        (f"{today_str}%",),
    ).fetchall()

    # Moving to tomorrow (active tasks not done)
    carrying = db.execute(
        """SELECT title, priority, estimated_minutes
           FROM tasks WHERE status = 'active' AND type = 'work'
           ORDER BY created_at""",
    ).fetchall()

    # Open blockers
    blockers = db.execute(
        """SELECT b.who, b.note, b.type, t.title
           FROM blockers b JOIN tasks t ON b.task_id = t.id
           WHERE b.resolved_at IS NULL""",
    ).fetchall()

    # Time tracked
    total_min = sum(r["actual_minutes"] or 0 for r in completed)

    # Tomorrow preview (next day's recurring tasks)
    tomorrow_dow = (now.weekday() + 1) % 7
    tomorrow_recurring = db.execute(
        "SELECT title FROM recurring_tasks WHERE active = 1 AND day_of_week = ?",
        (tomorrow_dow,),
    ).fetchall()

    return {
        "date": today_str,
        "day_of_week": now.strftime("%A"),
        "completed": [dict(r) for r in completed],
        "completed_count": len(completed),
        "carrying_forward": [dict(r) for r in carrying],
        "carrying_count": len(carrying),
        "open_blockers": [dict(r) for r in blockers],
        "blocker_count": len(blockers),
        "minutes_tracked": total_min,
        "tomorrow_preview": [dict(r) for r in tomorrow_recurring],
    }


def get_weekly_review() -> dict:
    """Generate weekly review data for Friday wrap-up.

    Covers Mon-Fri of the current week.
    """
    db = get_db()
    now = datetime.now(_TZ)

    # Find Monday of this week
    monday = now.date() - timedelta(days=now.weekday())
    week_start = monday.isoformat()
    week_end = (monday + timedelta(days=5)).isoformat()

    # Completed this week
    completed = db.execute(
        """SELECT title, actual_minutes, estimated_minutes, completed_at, source
           FROM tasks WHERE status = 'done' AND completed_at >= ? AND completed_at < ?
           ORDER BY completed_at""",
        (week_start, week_end + "Z"),
    ).fetchall()

    # Time analytics
    total_actual = sum(r["actual_minutes"] or 0 for r in completed)
    total_estimated = sum(r["estimated_minutes"] or 0 for r in completed)

    # Tasks by source
    by_source = {}
    for r in completed:
        src = r["source"] or "manual"
        by_source[src] = by_source.get(src, 0) + 1

    # Blockers this week
    blockers_created = db.execute(
        "SELECT COUNT(*) as n FROM blockers WHERE created_at >= ?",
        (week_start,),
    ).fetchone()["n"]

    blockers_resolved = db.execute(
        "SELECT COUNT(*) as n FROM blockers WHERE resolved_at IS NOT NULL AND resolved_at >= ?",
        (week_start,),
    ).fetchone()["n"]

    # Waiting on (still unresolved)
    waiting = db.execute(
        """SELECT b.who, b.note, b.type, t.title, b.created_at
           FROM blockers b JOIN tasks t ON b.task_id = t.id
           WHERE b.resolved_at IS NULL
           ORDER BY b.created_at""",
    ).fetchall()

    # Streaks
    streaks = get_streaks()

    return {
        "week_start": week_start,
        "week_end": week_end,
        "completed": [dict(r) for r in completed],
        "completed_count": len(completed),
        "total_actual_minutes": total_actual,
        "total_estimated_minutes": total_estimated,
        "faster_than_estimated": total_estimated > 0 and total_actual < total_estimated,
        "time_saved_minutes": max(0, total_estimated - total_actual) if total_estimated > 0 else 0,
        "by_source": by_source,
        "blockers_created": blockers_created,
        "blockers_resolved": blockers_resolved,
        "waiting_on": [dict(r) for r in waiting],
        "streaks": streaks,
    }


# ---------------------------------------------------------------------------
# Ask Claude Chat
# ---------------------------------------------------------------------------

# Chat history per session (resets on app restart)
_chat_history: list[dict] = []

# Monthly budget tracking
_chat_budget_path = DB_DIR / "chat_budget.json"
CHAT_MONTHLY_BUDGET_CENTS = 200  # $2.00/month default


def _load_chat_budget() -> dict:
    """Load chat budget tracking."""
    if _chat_budget_path.exists():
        with open(_chat_budget_path) as f:
            return json.load(f)
    return {"month": "", "spent_cents": 0}


def _save_chat_budget(budget: dict) -> None:
    """Save chat budget tracking."""
    _chat_budget_path.parent.mkdir(parents=True, exist_ok=True)
    with open(_chat_budget_path, "w") as f:
        json.dump(budget, f)


def check_chat_budget() -> tuple[bool, int, int]:
    """Check if chat budget allows another message.

    Returns (allowed, spent_cents, limit_cents).
    """
    budget = _load_chat_budget()
    current_month = date.today().strftime("%Y-%m")

    # Reset monthly counter
    if budget.get("month") != current_month:
        budget = {"month": current_month, "spent_cents": 0}
        _save_chat_budget(budget)

    return (
        budget["spent_cents"] < CHAT_MONTHLY_BUDGET_CENTS,
        budget["spent_cents"],
        CHAT_MONTHLY_BUDGET_CENTS,
    )


def record_chat_cost(input_tokens: int, output_tokens: int, model: str) -> None:
    """Record cost of a chat message."""
    # Haiku pricing: $0.80/M input, $4/M output (approximate)
    # Sonnet pricing: $3/M input, $15/M output
    if "haiku" in model:
        cost_cents = (input_tokens * 0.00008) + (output_tokens * 0.0004)
    else:
        cost_cents = (input_tokens * 0.0003) + (output_tokens * 0.0015)

    budget = _load_chat_budget()
    current_month = date.today().strftime("%Y-%m")
    if budget.get("month") != current_month:
        budget = {"month": current_month, "spent_cents": 0}

    budget["spent_cents"] += round(cost_cents, 2)
    _save_chat_budget(budget)


def build_system_prompt(energy_level: str = "medium") -> str:
    """Build the system prompt for the Ask Claude chat panel."""
    now = datetime.now(_TZ)
    today_tasks = get_today_tasks(energy_level)
    active_blockers = get_active_blockers()

    frog_title = today_tasks["frog"]["title"] if today_tasks["frog"] else "none"
    task_count = len(today_tasks.get("today", [])) + len(today_tasks.get("quick_wins", []))
    blocked_count = len(today_tasks.get("blocked", []))
    personal_count = len(today_tasks.get("personal", []))

    # Build inventory alerts if available
    brief = get_morning_brief()
    inv_alerts = ""
    if brief and brief.get("inventory_alerts"):
        inv_alerts = "\n".join(
            f"  - {a['sku']}: {a.get('qty', '?')} units, {a.get('runway_weeks', '?')} weeks runway"
            for a in brief["inventory_alerts"]
        )

    return f"""You are the Ask Claude assistant inside the Command Center app for AppyHour (Elevate Foods), a subscription cheese/charcuterie box company.

Current context:
- Date: {now.strftime("%A, %B %d, %Y")} at {now.strftime("%I:%M %p")} EDT
- Energy level: {energy_level}
- Today's frog: {frog_title}
- Work tasks remaining: {task_count}
- Blocked tasks: {blocked_count}
- Personal tasks: {personal_count}
{f"- Inventory alerts:{chr(10)}{inv_alerts}" if inv_alerts else "- No inventory alerts"}

Your role:
- Answer questions about the business, operations, inventory, shipping
- Help draft emails (POs to RMFG, customer responses)
- Research tasks the user doesn't know how to do
- Suggest next actions
- Be concise and direct — this is a sidebar chat, not a full conversation

You are a collaborator, not a coach. State facts and offer help. Never say "you've got this" or evaluate performance. Never use red/urgent/failure language — use "needs attention" or "worth checking" instead.

Keep responses under 150 words unless the user asks for detail."""


def get_chat_history() -> list[dict]:
    """Get current session chat history."""
    return _chat_history


def add_chat_message(role: str, content: str) -> None:
    """Add a message to chat history."""
    _chat_history.append({"role": role, "content": content})
    # Keep last 20 messages to prevent context growth
    if len(_chat_history) > 20:
        _chat_history[:] = _chat_history[-20:]


def clear_chat_history() -> None:
    """Clear chat history."""
    _chat_history.clear()


# ---------------------------------------------------------------------------
# Backup / Snapshots
# ---------------------------------------------------------------------------

SNAPSHOT_DIR = DB_DIR / "snapshots"
SNAPSHOT_RETENTION_DAYS = 30


def create_daily_snapshot() -> str | None:
    """Create a daily JSON snapshot of all tasks. Returns path or None if already exists."""
    today_str = date.today().isoformat()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{today_str}.json"

    if path.exists():
        return None  # Already snapshotted today

    db = get_db()
    tasks = db.execute("SELECT * FROM tasks").fetchall()
    checklist = db.execute("SELECT * FROM checklist_items").fetchall()
    recurring = db.execute("SELECT * FROM recurring_tasks").fetchall()
    blockers = db.execute("SELECT * FROM blockers").fetchall()

    snapshot = {
        "date": today_str,
        "created_at": _now_iso(),
        "tasks": [dict(r) for r in tasks],
        "checklist_items": [dict(r) for r in checklist],
        "recurring_tasks": [dict(r) for r in recurring],
        "blockers": [dict(r) for r in blockers],
    }

    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2)

    # Clean old snapshots
    _cleanup_old_snapshots()

    return str(path)


def _cleanup_old_snapshots() -> int:
    """Remove snapshots older than retention period. Returns count removed."""
    if not SNAPSHOT_DIR.exists():
        return 0
    cutoff = date.today() - timedelta(days=SNAPSHOT_RETENTION_DAYS)
    removed = 0
    for f in SNAPSHOT_DIR.glob("*.json"):
        try:
            file_date = date.fromisoformat(f.stem)
            if file_date < cutoff:
                f.unlink()
                removed += 1
        except ValueError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Bad Day Protocol — Triage piled-up tasks
# ---------------------------------------------------------------------------


def get_carryover_tasks() -> list[dict]:
    """Get tasks that have been active for more than 1 day (carried forward).

    These are candidates for the Bad Day Protocol triage.
    """
    db = get_db()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    rows = db.execute(
        """SELECT * FROM tasks
           WHERE status = 'active' AND created_at < ?
           ORDER BY created_at""",
        (yesterday,),
    ).fetchall()

    tasks = []
    for row in rows:
        task = dict(row)
        task["tags"] = json.loads(task.get("tags", "[]"))
        task["checklist"] = get_checklist(task["id"])
        # Calculate age in days
        try:
            created = datetime.fromisoformat(task["created_at"])
            age = (datetime.now(_TZ) - created).days
            task["age_days"] = age
        except (ValueError, TypeError):
            task["age_days"] = 0
        tasks.append(task)

    return tasks


def triage_task(task_id: str, action: str) -> dict | None:
    """Triage a carried-forward task.

    action: 'keep' | 'archive' | 'done'
    """
    if action == "keep":
        # Reset created_at to today so it stops looking old
        return update_task(task_id, created_at=_now_iso(), notes="Triaged: still needed")
    elif action == "archive":
        return update_task(task_id, status="archived")
    elif action == "done":
        return update_task(task_id, status="done")
    return get_task(task_id)
