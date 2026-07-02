"""Dead-man-switch heartbeat ledger — see HEARTBEAT_RULES.md (constraints SSOT) before changing.

`beat(name)` = fire-and-forget success marker; `read_ledger()` = checker's view. The ledger is a
plain JSON file in %APPDATA%/AppyHour — NEVER shipping.db (rule 3). beat() must never raise into
the host task (rule 2).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_LEDGER = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "AppyHour" / "heartbeats.json"


def beat(name: str) -> None:
    """Record a successful run of `name`. Swallows every failure — the host task's exit code
    must reflect the task, not the ledger (HEARTBEAT_RULES rule 2)."""
    try:
        _LEDGER.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        try:
            data = json.loads(_LEDGER.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data[name] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # atomic: write temp in same dir, replace
        fd, tmp = tempfile.mkstemp(dir=str(_LEDGER.parent), suffix=".hb")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(data, fp, indent=1)
            os.replace(tmp, _LEDGER)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        pass


def read_ledger() -> dict:
    """{name: iso-utc-ts}. Raises on unreadable ledger — the CHECKER must treat that as an alarm
    (silence is the failure signal, rule 1), so no swallowing here."""
    if not _LEDGER.exists():
        return {}
    return json.loads(_LEDGER.read_text(encoding="utf-8"))


def age_hours(iso_ts: str) -> float:
    then = datetime.fromisoformat(iso_ts)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0
