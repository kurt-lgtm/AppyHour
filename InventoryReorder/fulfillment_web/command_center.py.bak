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

DAY_NAME_TO_INT = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _day_int(val) -> int:
    """Convert day_of_week value (str or int) to Python weekday int."""
    if isinstance(val, int):
        return val
    return DAY_NAME_TO_INT.get(str(val).lower().strip(), 0)


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

        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            options TEXT NOT NULL DEFAULT '[]',
            context TEXT DEFAULT '',
            source TEXT NOT NULL DEFAULT 'system',
            answer TEXT,
            answered_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_decisions_pending ON decisions(answered_at);

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            detail TEXT DEFAULT '',
            ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS timer_sessions (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 25,
            extended_minutes INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            actual_minutes INTEGER,
            outcome TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_timer_task ON timer_sessions(task_id);
        CREATE INDEX IF NOT EXISTS idx_timer_active ON timer_sessions(completed_at);

        CREATE TABLE IF NOT EXISTS skip_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            recurring_id TEXT,
            reason TEXT DEFAULT '',
            ts TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_skip_recurring ON skip_events(recurring_id);
        CREATE INDEX IF NOT EXISTS idx_skip_ts ON skip_events(ts);

        CREATE TABLE IF NOT EXISTS learning_calibrations (
            recurring_id TEXT PRIMARY KEY,
            sample_count INTEGER NOT NULL DEFAULT 0,
            p50_actual_minutes REAL,
            last_calibrated_at TEXT
        );
    """)
    # Idempotent migrations for columns added after initial release
    try:
        db.execute("ALTER TABLE blockers ADD COLUMN auto_resurfaced_at TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
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


def snooze_blocker(blocker_id: str, days: int = 1) -> dict | None:
    """Push check_back_at out by N days and clear auto_resurfaced_at so monitor re-runs."""
    db = get_db()
    new_checkback = (datetime.now(_TZ) + timedelta(days=days)).isoformat()
    db.execute(
        "UPDATE blockers SET check_back_at = ?, auto_resurfaced_at = NULL WHERE id = ?",
        (new_checkback, blocker_id),
    )
    # Re-block the task if it was auto-resurfaced
    row = db.execute("SELECT task_id FROM blockers WHERE id = ?", (blocker_id,)).fetchone()
    if row:
        db.execute("UPDATE tasks SET status = 'blocked' WHERE id = ?", (row["task_id"],))
    db.commit()
    log_activity("blocker_snoozed", f"blocker={blocker_id} days={days}")
    return _row_to_dict(db.execute("SELECT * FROM blockers WHERE id = ?", (blocker_id,)).fetchone())


def get_due_checkbacks() -> list[dict]:
    """Preview: blockers whose check_back_at has passed and haven't been resurfaced yet."""
    db = get_db()
    now = _now_iso()
    rows = db.execute(
        """SELECT b.*, t.title AS task_title FROM blockers b
           JOIN tasks t ON t.id = b.task_id
           WHERE b.check_back_at IS NOT NULL
             AND b.check_back_at <= ?
             AND b.resolved_at IS NULL
             AND b.auto_resurfaced_at IS NULL
           ORDER BY b.check_back_at""",
        (now,),
    ).fetchall()
    return _rows_to_list(rows)


def resurface_due_blockers() -> list[dict]:
    """Move tasks whose blockers have hit check_back_at back into the active queue.

    Keeps the blocker record intact (sets auto_resurfaced_at). User decides next:
    resolve (real unblock), snooze (still waiting), or delete task.
    Returns the list of resurfaced blocker rows.
    """
    db = get_db()
    now = _now_iso()
    rows = db.execute(
        """SELECT * FROM blockers
           WHERE check_back_at IS NOT NULL
             AND check_back_at <= ?
             AND resolved_at IS NULL
             AND auto_resurfaced_at IS NULL""",
        (now,),
    ).fetchall()

    resurfaced = []
    for r in rows:
        db.execute(
            "UPDATE blockers SET auto_resurfaced_at = ? WHERE id = ?", (now, r["id"])
        )
        db.execute("UPDATE tasks SET status = 'active' WHERE id = ?", (r["task_id"],))
        resurfaced.append(dict(r))
        log_activity(
            "blocker_resurfaced",
            f"task={r['task_id']} blocker={r['id']} waited_on={r['who'] or r['type']}",
        )

    if resurfaced:
        db.commit()
    return resurfaced


def get_recent_resurfaces(hours: int = 24) -> list[dict]:
    """Blockers auto-resurfaced in the last N hours — for daily brief + highlighting."""
    db = get_db()
    cutoff = (datetime.now(_TZ) - timedelta(hours=hours)).isoformat()
    rows = db.execute(
        """SELECT b.*, t.title AS task_title FROM blockers b
           JOIN tasks t ON t.id = b.task_id
           WHERE b.auto_resurfaced_at IS NOT NULL
             AND b.auto_resurfaced_at >= ?
             AND b.resolved_at IS NULL
           ORDER BY b.auto_resurfaced_at DESC""",
        (cutoff,),
    ).fetchall()
    return _rows_to_list(rows)


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
# Timer (Pomodoro)
# ---------------------------------------------------------------------------


def start_timer(task_id: str, duration_minutes: int = 25) -> dict:
    """Start a timer session for a task. Cancels any existing active session on the task."""
    db = get_db()
    task = get_task(task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    # Close any active session for this task (user restarted)
    db.execute(
        "UPDATE timer_sessions SET completed_at = ?, outcome = 'cancelled' "
        "WHERE task_id = ? AND completed_at IS NULL",
        (_now_iso(), task_id),
    )

    session_id = _new_id()
    now = _now_iso()
    db.execute(
        """INSERT INTO timer_sessions (id, task_id, started_at, duration_minutes)
           VALUES (?, ?, ?, ?)""",
        (session_id, task_id, now, duration_minutes),
    )
    db.commit()
    log_activity("timer_start", f"task={task_id} duration={duration_minutes}m")
    return get_timer_session(session_id)


def extend_timer(session_id: str, extra_minutes: int = 10) -> dict | None:
    """Add minutes to an active session."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM timer_sessions WHERE id = ? AND completed_at IS NULL",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    db.execute(
        "UPDATE timer_sessions SET extended_minutes = extended_minutes + ? WHERE id = ?",
        (extra_minutes, session_id),
    )
    db.commit()
    log_activity("timer_extend", f"session={session_id} extra={extra_minutes}m")
    return get_timer_session(session_id)


def stop_timer(session_id: str, outcome: str = "completed") -> dict | None:
    """Stop a timer session. Outcome: completed | cancelled | blocked.
    On 'completed', writes actual_minutes to the session AND rolls up onto the task."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM timer_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    if row["completed_at"]:
        return _row_to_dict(row)

    started = datetime.fromisoformat(row["started_at"])
    now_dt = datetime.now(_TZ)
    actual = max(1, int((now_dt - started).total_seconds() // 60))
    now_iso = now_dt.isoformat()

    db.execute(
        "UPDATE timer_sessions SET completed_at = ?, actual_minutes = ?, outcome = ? WHERE id = ?",
        (now_iso, actual, outcome, session_id),
    )

    if outcome == "completed":
        # Accumulate actual_minutes on the task (support multiple sessions per task)
        task_row = db.execute(
            "SELECT actual_minutes, recurring_id FROM tasks WHERE id = ?", (row["task_id"],)
        ).fetchone()
        prior = (task_row["actual_minutes"] if task_row and task_row["actual_minutes"] else 0)
        db.execute(
            "UPDATE tasks SET actual_minutes = ? WHERE id = ?",
            (prior + actual, row["task_id"]),
        )
        db.commit()
        # Trigger calibration refresh (cheap, stops at <5 samples)
        if task_row and task_row["recurring_id"]:
            try:
                calibrate_duration(task_row["recurring_id"])
            except Exception as e:  # noqa: BLE001
                log_activity("calibration_error", f"recurring={task_row['recurring_id']} err={e}")

    db.commit()
    log_activity("timer_stop", f"session={session_id} outcome={outcome} actual={actual}m")
    return get_timer_session(session_id)


def get_timer_session(session_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM timer_sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_dict(row)


def get_active_timer() -> dict | None:
    """Return the currently-running timer session (at most one), with task info."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM timer_sessions WHERE completed_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    session = dict(row)
    task = get_task(session["task_id"])
    session["task"] = task
    # Compute remaining seconds (negative = overrun)
    started = datetime.fromisoformat(session["started_at"])
    total_minutes = session["duration_minutes"] + session["extended_minutes"]
    elapsed = (datetime.now(_TZ) - started).total_seconds()
    session["remaining_seconds"] = int(total_minutes * 60 - elapsed)
    session["elapsed_seconds"] = int(elapsed)
    return session


def get_task_timer_history(task_id: str) -> list[dict]:
    """All timer sessions for a task, newest first."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM timer_sessions WHERE task_id = ? ORDER BY started_at DESC",
        (task_id,),
    ).fetchall()
    return _rows_to_list(rows)


# ---------------------------------------------------------------------------
# Learning (duration calibration + skip detection)
# ---------------------------------------------------------------------------

CALIBRATION_MIN_SAMPLES = 5
CALIBRATION_BUFFER = 1.10  # p50 * 1.10 (10% buffer)
SKIP_ALERT_THRESHOLD = 3
SKIP_WINDOW_DAYS = 21


def record_skip(task_id: str, reason: str = "") -> dict:
    """Record a skip event for learning. Returns task and updated skip count."""
    db = get_db()
    task_row = db.execute(
        "SELECT recurring_id FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    recurring_id = task_row["recurring_id"] if task_row else None

    db.execute(
        "INSERT INTO skip_events (task_id, recurring_id, reason, ts) VALUES (?, ?, ?, ?)",
        (task_id, recurring_id, reason, _now_iso()),
    )
    db.commit()
    log_activity("task_skipped", f"task={task_id} reason={reason}")

    skip_count = 0
    if recurring_id:
        cutoff = (datetime.now(_TZ) - timedelta(days=SKIP_WINDOW_DAYS)).isoformat()
        row = db.execute(
            "SELECT COUNT(*) AS c FROM skip_events WHERE recurring_id = ? AND ts >= ?",
            (recurring_id, cutoff),
        ).fetchone()
        skip_count = row["c"] if row else 0

    return {"task_id": task_id, "recurring_id": recurring_id, "skip_count": skip_count}


def _percentile(values: list[float], pct: float) -> float:
    """Simple percentile (linear interpolation). Assumes values non-empty."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def calibrate_duration(recurring_id: str) -> dict | None:
    """Compute p50*buffer estimate for a recurring task. Writes to learning_calibrations.
    Returns dict with sample_count, p50, recommended_estimate, or None if insufficient data."""
    db = get_db()
    rows = db.execute(
        """SELECT ts.actual_minutes FROM timer_sessions ts
           JOIN tasks t ON t.id = ts.task_id
           WHERE t.recurring_id = ? AND ts.outcome = 'completed' AND ts.actual_minutes IS NOT NULL""",
        (recurring_id,),
    ).fetchall()
    samples = [float(r["actual_minutes"]) for r in rows if r["actual_minutes"]]
    if len(samples) < CALIBRATION_MIN_SAMPLES:
        return {
            "recurring_id": recurring_id,
            "sample_count": len(samples),
            "p50_minutes": None,
            "recommended_estimate": None,
            "sufficient": False,
        }

    p50 = _percentile(samples, 0.50)
    recommended = max(1, int(round(p50 * CALIBRATION_BUFFER)))

    db.execute(
        """INSERT INTO learning_calibrations (recurring_id, sample_count, p50_actual_minutes, last_calibrated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(recurring_id) DO UPDATE SET
             sample_count = excluded.sample_count,
             p50_actual_minutes = excluded.p50_actual_minutes,
             last_calibrated_at = excluded.last_calibrated_at""",
        (recurring_id, len(samples), p50, _now_iso()),
    )
    db.commit()

    return {
        "recurring_id": recurring_id,
        "sample_count": len(samples),
        "p50_minutes": round(p50, 1),
        "recommended_estimate": recommended,
        "sufficient": True,
    }


def calibrate_all_recurring() -> list[dict]:
    """Run calibration for every recurring task. Returns list of successful calibrations."""
    db = get_db()
    rows = db.execute("SELECT id FROM recurring_tasks WHERE active = 1").fetchall()
    results = []
    for r in rows:
        result = calibrate_duration(r["id"])
        if result and result.get("sufficient"):
            results.append(result)
    return results


def apply_calibration(recurring_id: str) -> dict | None:
    """Apply calibrated estimate to the recurring_tasks template."""
    db = get_db()
    cal = db.execute(
        "SELECT * FROM learning_calibrations WHERE recurring_id = ?", (recurring_id,)
    ).fetchone()
    if cal is None or cal["sample_count"] < CALIBRATION_MIN_SAMPLES:
        return None
    recommended = max(1, int(round(cal["p50_actual_minutes"] * CALIBRATION_BUFFER)))
    db.execute(
        "UPDATE recurring_tasks SET estimated_minutes = ? WHERE id = ?",
        (recommended, recurring_id),
    )
    db.commit()
    log_activity("calibration_applied", f"recurring={recurring_id} new_estimate={recommended}m")
    return {"recurring_id": recurring_id, "new_estimate": recommended}


def get_learning_insights() -> dict:
    """Return duration drift + skip alerts for the daily brief."""
    db = get_db()

    # Duration drift: calibrated estimate differs from current template by ≥20%
    drift = []
    rows = db.execute(
        """SELECT r.id, r.title, r.estimated_minutes, c.p50_actual_minutes, c.sample_count
           FROM recurring_tasks r
           JOIN learning_calibrations c ON c.recurring_id = r.id
           WHERE c.sample_count >= ? AND r.active = 1""",
        (CALIBRATION_MIN_SAMPLES,),
    ).fetchall()
    for r in rows:
        if not r["estimated_minutes"] or not r["p50_actual_minutes"]:
            continue
        recommended = r["p50_actual_minutes"] * CALIBRATION_BUFFER
        pct_off = abs(recommended - r["estimated_minutes"]) / r["estimated_minutes"]
        if pct_off >= 0.20:
            drift.append({
                "recurring_id": r["id"],
                "title": r["title"],
                "current_estimate": r["estimated_minutes"],
                "recommended_estimate": max(1, int(round(recommended))),
                "sample_count": r["sample_count"],
                "direction": "under" if recommended > r["estimated_minutes"] else "over",
            })

    # Skip alerts: 3+ skips in window
    cutoff = (datetime.now(_TZ) - timedelta(days=SKIP_WINDOW_DAYS)).isoformat()
    skip_rows = db.execute(
        """SELECT s.recurring_id, COUNT(*) AS cnt, r.title
           FROM skip_events s
           LEFT JOIN recurring_tasks r ON r.id = s.recurring_id
           WHERE s.ts >= ? AND s.recurring_id IS NOT NULL
           GROUP BY s.recurring_id
           HAVING cnt >= ?""",
        (cutoff, SKIP_ALERT_THRESHOLD),
    ).fetchall()
    skip_alerts = [
        {
            "recurring_id": r["recurring_id"],
            "title": r["title"] or "(unknown)",
            "skip_count": r["cnt"],
            "window_days": SKIP_WINDOW_DAYS,
        }
        for r in skip_rows
    ]

    return {"duration_drift": drift, "skip_alerts": skip_alerts}


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

    score = (energy_fit * 0.35) + (time_press * 0.35) + (impact * 0.20) + (blocker * 0.10)
    return score


def urgency_tier(score: float) -> str:
    """Map urgency score → visual tier for UI color/glow."""
    if score >= 0.85:
        return "critical"   # amber glow, pulse
    elif score >= 0.65:
        return "high"       # amber text
    elif score >= 0.45:
        return "medium"     # default
    else:
        return "low"        # dim


# ---------------------------------------------------------------------------
# Decisions Queue (absorbed from mission-control)
# ---------------------------------------------------------------------------


def create_decision(
    question: str,
    options: list[str] | None = None,
    context: str = "",
    source: str = "system",
) -> dict:
    """Create a pending decision for the user to answer."""
    db = get_db()
    did = _new_id()
    now = _now_iso()
    opts = json.dumps(options or [])
    db.execute(
        "INSERT INTO decisions (id, question, options, context, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (did, question, opts, context, source, now),
    )
    db.commit()
    log_activity("decision_created", f"{did}: {question[:80]}")
    return {"id": did, "question": question, "options": options or [], "context": context, "source": source, "created_at": now}


def answer_decision(decision_id: str, answer: str) -> dict | None:
    """Answer a pending decision."""
    db = get_db()
    now = _now_iso()
    db.execute(
        "UPDATE decisions SET answer = ?, answered_at = ? WHERE id = ? AND answered_at IS NULL",
        (answer, now, decision_id),
    )
    db.commit()
    log_activity("decision_answered", f"{decision_id}: {answer[:80]}")
    row = db.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    if row is None:
        return None
    d = _row_to_dict(row)
    d["options"] = json.loads(d.get("options", "[]"))
    return d


def get_pending_decisions() -> list[dict]:
    """Get all unanswered decisions, newest first."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM decisions WHERE answered_at IS NULL ORDER BY created_at DESC"
    ).fetchall()
    result = _rows_to_list(rows)
    for d in result:
        d["options"] = json.loads(d.get("options", "[]"))
    return result


# ---------------------------------------------------------------------------
# Activity Log (absorbed from mission-control)
# ---------------------------------------------------------------------------


def log_activity(event: str, detail: str = "") -> None:
    """Append to activity log. Fire-and-forget."""
    try:
        db = get_db()
        db.execute(
            "INSERT INTO activity_log (event, detail, ts) VALUES (?, ?, ?)",
            (event, detail[:500], _now_iso()),
        )
        db.commit()
    except Exception:
        pass  # Never fail on logging


def get_activity_log(limit: int = 50) -> list[dict]:
    """Get recent activity, newest first."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM activity_log ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    return _rows_to_list(rows)


# ---------------------------------------------------------------------------
# Today View
# ---------------------------------------------------------------------------


def get_today_tasks(energy_level: str = "medium") -> dict:
    """Get today's prioritized task list, separated by category.

    Returns {frog, quick_wins, today, blocked, personal, completed_today, resurfaced}.
    """
    # Run blocker auto-monitor before building the view — time-based check-backs
    # resurface tasks automatically. Cheap (indexed query).
    try:
        resurface_due_blockers()
    except Exception as e:  # noqa: BLE001
        log_activity("resurface_error", str(e))

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

    resurfaced = get_recent_resurfaces(hours=24)

    return {
        "frog": frog,
        "quick_wins": quick_wins,
        "today": today,
        "blocked": blocked_tasks,
        "personal": personal_tasks,
        "completed_today": completed,
        "resurfaced": resurfaced,
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


def enrich_slack_task(task_id: str, shopify_orders: list[dict] | None = None) -> dict | None:
    """Enrich a slack-trawl task with customer/order data from Shopify.

    Parses customer hints from task notes, fuzzy-matches against shopify_orders,
    and adds order #, full name, and Slack permalink to task notes.

    shopify_orders: list of {name, customer, city, state, tags} from appyhour_fetch_orders.
    """
    task = get_task(task_id)
    if not task:
        return None

    notes = task.get("notes", "")
    title = task.get("title", "")

    # Extract name hints from title/notes
    # Patterns: "for Sarah M.", "for them", customer names in quotes
    import re
    name_hints = re.findall(r"for\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?)", title + " " + notes)

    enriched_lines = []

    # Build Slack permalink from source_ref
    source_ref = task.get("source_ref", "")
    if source_ref.startswith("slack:"):
        parts = source_ref.split(":")
        if len(parts) >= 3:
            channel_id = parts[1]
            msg_ts = parts[2]
            permalink = f"https://elevatefoods.slack.com/archives/{channel_id}/p{msg_ts.replace('.', '')}"
            enriched_lines.append(f"Slack thread: {permalink}")

    # Match customer name against Shopify orders
    if shopify_orders and name_hints:
        for hint in name_hints:
            hint_lower = hint.lower().strip().rstrip(".")
            for order in shopify_orders:
                customer = (order.get("customer") or "").lower()
                if hint_lower in customer or customer.startswith(hint_lower.split()[0]):
                    enriched_lines.append(
                        f"Matched: {order.get('customer')} — Order {order.get('name')} — {order.get('city', '')}, {order.get('state', '')}"
                    )
                    break

    if not enriched_lines:
        enriched_lines.append("No customer match found — manual lookup needed")

    new_notes = notes + "\n--- Enriched ---\n" + "\n".join(enriched_lines)
    return update_task(task_id, notes=new_notes)


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
            days_back = (check_date.weekday() - _day_int(rec["day_of_week"])) % 7
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
SCHEMA_VERSION = 2


def _build_full_snapshot() -> dict:
    """Build a complete snapshot dict covering all persisted state."""
    db = get_db()
    return {
        "schema_version": SCHEMA_VERSION,
        "date": date.today().isoformat(),
        "created_at": _now_iso(),
        "tasks": [dict(r) for r in db.execute("SELECT * FROM tasks").fetchall()],
        "checklist_items": [dict(r) for r in db.execute("SELECT * FROM checklist_items").fetchall()],
        "recurring_tasks": [dict(r) for r in db.execute("SELECT * FROM recurring_tasks").fetchall()],
        "blockers": [dict(r) for r in db.execute("SELECT * FROM blockers").fetchall()],
        "timer_sessions": [dict(r) for r in db.execute("SELECT * FROM timer_sessions").fetchall()],
        "skip_events": [dict(r) for r in db.execute("SELECT * FROM skip_events").fetchall()],
        "learning_calibrations": [
            dict(r) for r in db.execute("SELECT * FROM learning_calibrations").fetchall()
        ],
        "decisions": [dict(r) for r in db.execute("SELECT * FROM decisions").fetchall()],
    }


def create_daily_snapshot() -> str | None:
    """Create a daily JSON snapshot of all tasks. Returns path or None if already exists."""
    today_str = date.today().isoformat()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{today_str}.json"

    if path.exists():
        return None  # Already snapshotted today

    snapshot = _build_full_snapshot()

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


# Drive backup — reuses existing OAuth token (see drive-upload-protocol memory)
DRIVE_TOKEN_PATH = (
    Path(__file__).resolve().parent.parent / "dist" / "drive_oauth_token.json"
)
DRIVE_BACKUP_FOLDER_ID = "1TgvxK10tFAPJqhkYw-6u1Umnvp9wMJ3I"
BACKUP_STATE_PATH = DB_DIR / "backup_state.json"


def _load_backup_state() -> dict:
    if not BACKUP_STATE_PATH.exists():
        return {}
    try:
        return json.loads(BACKUP_STATE_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_backup_state(state: dict) -> None:
    BACKUP_STATE_PATH.write_text(json.dumps(state, indent=2))


def upload_backup_to_drive(snapshot: dict | None = None) -> dict:
    """Upload a full snapshot JSON to Google Drive. Returns dict with file_id and web_link.

    Raises RuntimeError if OAuth token missing or Google libs unavailable.
    """
    if snapshot is None:
        snapshot = _build_full_snapshot()

    if not DRIVE_TOKEN_PATH.exists():
        raise RuntimeError(f"Drive OAuth token not found at {DRIVE_TOKEN_PATH}")

    try:
        import io  # local import — only needed when uploading
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.http import MediaIoBaseUpload  # type: ignore
    except ImportError as e:
        raise RuntimeError(f"Google API libraries not installed: {e}") from e

    td = json.loads(DRIVE_TOKEN_PATH.read_text())
    creds = Credentials(
        token=td["token"],
        refresh_token=td.get("refresh_token"),
        token_uri=td.get("token_uri"),
        client_id=td.get("client_id"),
        client_secret=td.get("client_secret"),
        scopes=td.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        td["token"] = creds.token
        DRIVE_TOKEN_PATH.write_text(json.dumps(td, indent=2))

    drive = build("drive", "v3", credentials=creds)
    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()
    filename = f"cc-backup-{iso_year}-W{iso_week:02d}.json"
    body_bytes = json.dumps(snapshot, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(
        io.BytesIO(body_bytes), mimetype="application/json", resumable=False
    )
    result = drive.files().create(
        body={"name": filename, "parents": [DRIVE_BACKUP_FOLDER_ID]},
        media_body=media,
        fields="id,webViewLink",
        supportsAllDrives=True,
    ).execute()

    state = _load_backup_state()
    state["last_upload_at"] = _now_iso()
    state["last_upload_filename"] = filename
    state["last_upload_file_id"] = result.get("id")
    _save_backup_state(state)
    log_activity("drive_backup", f"file={filename} id={result.get('id')}")

    return {
        "file_id": result.get("id"),
        "web_link": result.get("webViewLink"),
        "filename": filename,
    }


def maybe_weekly_drive_backup() -> dict | None:
    """Upload to Drive if ≥6 days since last upload. Returns result dict or None if skipped."""
    state = _load_backup_state()
    last = state.get("last_upload_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (datetime.now(_TZ) - last_dt).days < 6:
                return None
        except ValueError:
            pass
    return upload_backup_to_drive()


def get_backup_status() -> dict:
    """Return backup state for UI."""
    state = _load_backup_state()
    return {
        "last_local_snapshot": None,
        "last_drive_upload_at": state.get("last_upload_at"),
        "last_drive_filename": state.get("last_upload_filename"),
        "last_drive_file_id": state.get("last_upload_file_id"),
        "token_configured": DRIVE_TOKEN_PATH.exists(),
    }


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


def build_morning_brief(
    inventory: dict | None = None,
    external: dict | None = None,
) -> dict:
    """Build + store morning brief from local data + optional external sources.

    inventory: {sku: qty} from running_inventory (passed by app.py which has access)
    external: {gorgias_open, gorgias_food_safety, slack_unreads, gmail_unreads,
               orders_unfulfilled} — from MCP tools, optional

    Returns the stored brief dict.
    """
    ext = external or {}
    stats = get_daily_stats()
    carryovers = get_carryover_tasks()
    today_tasks = get_today_tasks()

    # Inventory alerts: SKUs at 0 or negative
    inv_alerts = []
    if inventory:
        for sku, qty in sorted(inventory.items()):
            if qty <= 0:
                inv_alerts.append({"sku": sku, "qty": qty, "status": "OUT"})
            elif qty < 20:
                inv_alerts.append({"sku": sku, "qty": qty, "status": "LOW"})

    # Day-of-week context
    dow = date.today().strftime("%A")
    day_context = {
        "Monday": "Shipping review, Gorgias triage, plan week",
        "Tuesday": "Tommy call, demand pull, cut order (7PM EST)",
        "Wednesday": "React tool prep, inventory review, make PO",
        "Thursday": "React tool run, swaps, Shopify sync",
        "Friday": "Ship day, RMFG email, gel packs, weekly review",
        "Saturday": "Shipping monitoring (light)",
        "Sunday": "Off",
    }.get(dow, "")

    insights = get_learning_insights()
    resurfaced = today_tasks.get("resurfaced", [])

    brief_data = {
        "day": dow,
        "day_context": day_context,
        "task_count": stats.get("total", 0),
        "completed_today": stats.get("completed", 0),
        "carryover_count": len(carryovers),
        "frog": today_tasks.get("frog", {}).get("title") if today_tasks.get("frog") else None,
        "quick_win_count": len(today_tasks.get("quick_wins", [])),
        "blocked_count": len(today_tasks.get("blocked", [])),
        "resurfaced_count": len(resurfaced),
        "resurfaced": resurfaced,
        "inventory_alerts": inv_alerts,
        "orders_unfulfilled": ext.get("orders_unfulfilled", 0),
        "gorgias_open": ext.get("gorgias_open", 0),
        "gorgias_food_safety": ext.get("gorgias_food_safety", 0),
        "slack_unreads": ext.get("slack_unreads", 0),
        "gmail_unreads": ext.get("gmail_unreads", 0),
        "slack_trawl_created": ext.get("slack_trawl_created", 0),
        "learning_insights": insights,
    }

    store_morning_brief(brief_data)
    return brief_data


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


# ---------------------------------------------------------------------------
# Global Search (absorbed from OpenClaw Mission Control video)
# ---------------------------------------------------------------------------


def global_search(query: str, limit: int = 20) -> dict:
    """Search across tasks, activity log, and decisions. Returns grouped results."""
    if not query or len(query.strip()) < 2:
        return {"tasks": [], "activity": [], "decisions": []}

    db = get_db()
    q = f"%{query.strip()}%"

    task_rows = db.execute(
        "SELECT * FROM tasks WHERE title LIKE ? OR notes LIKE ? OR tags LIKE ? ORDER BY created_at DESC LIMIT ?",
        (q, q, q, limit),
    ).fetchall()

    activity_rows = db.execute(
        "SELECT * FROM activity_log WHERE event LIKE ? OR detail LIKE ? ORDER BY ts DESC LIMIT ?",
        (q, q, limit),
    ).fetchall()

    decision_rows = db.execute(
        "SELECT * FROM decisions WHERE question LIKE ? OR context LIKE ? ORDER BY created_at DESC LIMIT ?",
        (q, q, limit),
    ).fetchall()

    tasks = _rows_to_list(task_rows)
    decisions = _rows_to_list(decision_rows)
    for d in decisions:
        d["options"] = json.loads(d.get("options", "[]"))

    return {
        "tasks": tasks,
        "activity": _rows_to_list(activity_rows),
        "decisions": decisions,
        "total": len(tasks) + len(activity_rows) + len(decisions),
    }


# ---------------------------------------------------------------------------
# Recurring Weekly Grid (absorbed from OpenClaw Mission Control video)
# ---------------------------------------------------------------------------


def get_recurring_grid() -> dict:
    """Return recurring tasks organized by day-of-week for weekly grid view."""
    recurring = list_recurring()
    grid = {i: [] for i in range(7)}  # 0=Mon ... 6=Sun
    for rec in recurring:
        if not rec.get("active"):
            continue
        dow = _day_int(rec.get("day_of_week", 0))
        grid[dow].append({
            "id": rec["id"],
            "title": rec["title"],
            "time": rec.get("time", "09:00"),
            "energy": rec.get("energy", "medium"),
            "estimated_minutes": rec.get("estimated_minutes"),
            "priority": rec.get("priority", "medium"),
        })
    for dow in grid:
        grid[dow].sort(key=lambda r: r.get("time", "09:00"))

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return {
        "grid": {day_names[i]: grid[i] for i in range(7)},
        "total_recurring": sum(len(v) for v in grid.values()),
    }


# ---------------------------------------------------------------------------
# Health Check (absorbed from OpenClaw Mission Control video)
# ---------------------------------------------------------------------------


def health_check() -> dict:
    """Check CC system health: database, tables, task counts."""
    try:
        db = get_db()
        task_count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        active_count = db.execute("SELECT COUNT(*) FROM tasks WHERE status='active'").fetchone()[0]
        recurring_count = db.execute("SELECT COUNT(*) FROM recurring_tasks WHERE active=1").fetchone()[0]
        decision_count = db.execute("SELECT COUNT(*) FROM decisions WHERE answered_at IS NULL").fetchone()[0]
        brief = get_morning_brief()
        return {
            "status": "ok",
            "db_path": str(DB_PATH),
            "db_exists": DB_PATH.exists(),
            "tasks": task_count,
            "active_tasks": active_count,
            "recurring": recurring_count,
            "pending_decisions": decision_count,
            "brief_today": brief is not None,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
