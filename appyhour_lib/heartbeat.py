"""Dead-man-switch heartbeat ledger — see HEARTBEAT_RULES.md (constraints SSOT) before changing.

`beat(name)` = fire-and-forget success marker; `read_ledger()` = checker's view. The ledger is a
plain JSON file at C:\\AppyHourData\\heartbeats.json — NEVER shipping.db (rule 3), and NEVER
%APPDATA% again (rule 3, 2026-08-31). beat() must never raise into the host task (rule 2).

🔴 WHY NOT %APPDATA% (2026-08-31): MSIX virtualizes it, so agent-run writes landed in the sandbox
overlay while real-context (schtask) writes landed in the real profile — TWO physical ledgers with
DISJOINT histories. Measured that day: the overlay carried 8 keys with `offsite-backup` frozen at
08-22, while the real profile carried that ONE key correct at 08-30. Both `automation_health.py`
and `freshness_sweep.py` read this ledger, so they went blind together, for the same reason, and
their rule-13 mutual check could not see it. `C:\\AppyHourData` is outside the virtualization scope
(same reason the canonical shipping.db and `replica_pull_stamp.json` live there) — one physical
file, written in either context, read cleanly from both.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_LEDGER = Path(r"C:\AppyHourData\heartbeats.json")

# DEPRECATION WINDOW (one cycle, opened 2026-08-31): read-only fallback so a beat written by a
# writer still running the old code is not lost. read_ledger() merges it newest-wins and says so
# LOUDLY on stderr — a silent fallback would just be the split ledger again, wearing a fix's name.
# Nothing writes here any more. Remove once no unmigrated writer/reader remains (the last known
# hand-rolled reader is `_outputs/scripts/freshness_sweep.py` D3, owned elsewhere — it must move to
# read_ledger() before this block goes).
_LEGACY_LEDGER = (Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
                  / "AppyHour" / "heartbeats.json")


def _parse(ts: str) -> datetime:
    """Ledger timestamp -> aware UTC datetime; unparseable sorts oldest (never wins a merge)."""
    try:
        then = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return then if then.tzinfo else then.replace(tzinfo=timezone.utc)


def beat(name: str) -> None:
    """Record a successful run of `name`. Swallows every failure — the host task's exit code
    must reflect the task, not the ledger (HEARTBEAT_RULES rule 2)."""
    try:
        _LEDGER.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        try:
            data = json.loads(_LEDGER.read_text(encoding="utf-8-sig"))
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


def _read_legacy() -> dict:
    """Deprecated %APPDATA% ledger. Never raises: it is a fallback, not the authority — a corrupt
    deprecated file must not fail the checker, but it must not be silent either."""
    try:
        if not _LEGACY_LEDGER.exists():
            return {}
        data = json.loads(_LEGACY_LEDGER.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"heartbeat: legacy ledger unreadable ({type(e).__name__}: {e}) at {_LEGACY_LEDGER}"
              " — ignored; canonical ledger is authoritative", file=sys.stderr)
        return {}


def read_ledger() -> dict:
    """{name: iso-utc-ts}. Raises on unreadable CANONICAL ledger — the CHECKER must treat that as
    an alarm (silence is the failure signal, rule 1), so no swallowing here.

    Merges the deprecated %APPDATA% ledger NEWEST-WINS PER KEY during the deprecation window. Not a
    copy of either file: on 2026-08-31 the overlay held more keys but a 9-day-stale `offsite-backup`,
    while the real profile held that one key current — taking either side whole carries a false
    alarm forward. Every fallback contribution is logged loudly (rule 1: a silent repair of a
    monitoring path is indistinguishable from the monitoring being broken)."""
    data = {}
    if _LEDGER.exists():
        data = json.loads(_LEDGER.read_text(encoding="utf-8-sig"))
    newer = {k: v for k, v in _read_legacy().items()
             if k not in data or _parse(v) > _parse(data[k])}
    if newer:
        print(f"heartbeat: LEGACY-LEDGER FALLBACK fired for {sorted(newer)} from {_LEGACY_LEDGER}"
              " — an unmigrated writer still beats the deprecated %APPDATA% path", file=sys.stderr)
        data.update(newer)
    return data


def age_hours(iso_ts: str) -> float:
    then = datetime.fromisoformat(iso_ts)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0
