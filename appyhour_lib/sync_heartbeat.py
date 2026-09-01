"""Ingest sync heartbeat — see HEARTBEAT_RULES.md (constraints SSOT) before changing.

Per-run state for the logon/daily ingest legs (`carriers`, `fulfillments`, `auto_import`,
`shopify_orders`, `post_ingest_backup`). Written by `GelPackCalculator/sync_logon.py` and
`GelPackCalculator/pipeline_run.py`; read by `scripts/automation_health.check_sync_heartbeat`,
which alarms when the newest leg is older than 48h.

🔴 WHY NOT %APPDATA% (2026-09-01) — the same split that took `heartbeats.json` off that path on
2026-08-31, one file later. MSIX virtualizes `%APPDATA%`, so `sync_logon` (real context, launched
by the `appyhour_sync_on_logon` schtask) wrote the real profile while `automation_health` (agent
context, packaged) read the package-private overlay. TWO physical files, DISJOINT histories, and
the checker could only ever see a frozen fork of the one it was grading. Measured that morning:
the overlay was frozen at 2026-08-25 and the checker reported **"ingest sync heartbeat stale:
6.8d"** while the real file had been written **13:22 the same day** and every leg was current.
That is not a checker that lags — it is a checker structurally unable to see its own subject, on
the one signal whose entire job is to tell Kurt the ingest died. `C:\\AppyHourData` is outside the
virtualization scope (same reason the canonical `shipping.db`, `heartbeats.json` and
`replica_pull_stamp.json` live there): one physical file, written in either context, read cleanly
from both.

🔴 THE SEEDING TRAP, and why `merge()` is per-key. The two copies did not differ by staleness —
they held DISJOINT histories, so "copy whichever looks fuller" is wrong in both directions. On
2026-09-01 the overlay carried `fulfillments_status: "ok"` (from 08-25) while the real profile
carried `fulfillments_status: "fail:Timeout:600s:cancelled-clean"` from that morning. Taking the
overlay whole would have carried a stale **ok** over a live **failure** — a monitoring path
repaired into lying, which is strictly worse than the false alarm it replaced.

🔴 A `_status` KEY HAS NO TIMESTAMP OF ITS OWN — do not merge it independently. `_stamp()` advances
the bare `<name>` key only on success (it gates the 12h throttle; a failure that advanced it muted
its own retry for 12h — the 2026-07-27 bug) and writes `<name>_last_attempt` otherwise. So the
moment a status was written is `max(<name>, <name>_last_attempt)`, and that is the key the status
must travel with. Comparing statuses by the success timestamp alone loses exactly the failure
above: the real profile's last *success* was 08-31, OLDER than nothing on the other side, while its
last *attempt* was today.

Nothing writes the legacy path any more. `read()` merges it newest-wins for one deprecation cycle
and says so LOUDLY on stderr — a silent fallback is just the split ledger again, wearing a fix's
name.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

__all__ = ["CANONICAL", "LEGACY", "read", "write", "merge", "stamp_time"]

CANONICAL = Path(r"C:\AppyHourData\sync_heartbeat.json")

# DEPRECATION WINDOW (one cycle, opened 2026-09-01): read-only fallback so a stamp written by a
# writer still running the old code is not lost. Nothing writes here. Remove once no unmigrated
# writer remains — the prod tree at C:\AppyHourProd is deployed separately, so this must outlive
# the dev commit by at least one deploy.
LEGACY = (Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
          / "AppyHour" / "sync_heartbeat.json")

# Timestamps in this file are NAIVE LOCAL — `datetime.now().isoformat(timespec="seconds")`. Both
# consumers compare against a naive `datetime.now()`, so do NOT "fix" them to UTC-aware here: a
# mixed naive/aware comparison raises TypeError, and an offset shift would silently move the 48h
# staleness gate by the UTC offset. `heartbeats.json` is aware-UTC; this file is not. Different
# file, different convention, deliberately unchanged by the move.
_MIN = datetime.min


def _parse(ts: object) -> datetime:
    """Heartbeat timestamp -> naive local datetime; unparseable sorts oldest (never wins a merge)."""
    try:
        then = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return _MIN
    return then.replace(tzinfo=None) if then.tzinfo else then


def _base_name(key: str) -> str:
    """`fulfillments_status` -> `fulfillments`; `fulfillments_last_attempt` -> `fulfillments`."""
    for suffix in ("_status", "_last_attempt"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def stamp_time(data: dict, name: str) -> datetime:
    """When `_stamp(name, ...)` last ran = max(last success, last attempt).

    This — not the bare `<name>` key — is the recency of `<name>_status`. See the module
    docstring: a leg whose last success is old but whose last ATTEMPT is minutes ago carries the
    newer status, and that status is usually the failure someone needs to see.
    """
    return max(_parse(data.get(name)), _parse(data.get(f"{name}_last_attempt")))


def merge(base: dict, other: dict) -> dict:
    """`base` updated with every key `other` holds more recently. Never mutates either argument.

    Timestamp keys (`<name>`, `<name>_last_attempt`) compare on their own value. A `<name>_status`
    key compares on `stamp_time(name)` of each side, because it has no timestamp of its own.
    Ties keep `base` — the caller passes the authority as `base`.
    """
    out = dict(base)
    for key, val in other.items():
        if key not in out:
            out[key] = val
            continue
        if key.endswith("_status"):
            name = _base_name(key)
            if stamp_time(other, name) > stamp_time(base, name):
                out[key] = val
        elif _parse(val) > _parse(out[key]):
            out[key] = val
    return out


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def _read_legacy() -> dict:
    """Deprecated %APPDATA% copy. Never raises: it is a fallback, not the authority — a corrupt
    deprecated file must not fail the checker, but it must not be silent either."""
    try:
        if not LEGACY.exists():
            return {}
        return _read_json(LEGACY)
    except Exception as e:  # noqa: BLE001 — a broken fallback must not mask the canonical read
        print(f"sync_heartbeat: legacy copy unreadable ({type(e).__name__}: {e}) at {LEGACY}"
              " — ignored; canonical copy is authoritative", file=sys.stderr)
        return {}


def read() -> dict:
    """The ingest heartbeat. RAISES on an unreadable CANONICAL file — the checker must treat that
    as an alarm, so nothing is swallowed here (callers that must not crash keep their own guard).

    Merges the deprecated %APPDATA% copy newest-wins per key during the deprecation window, and
    logs every contribution LOUDLY: a silent repair of a monitoring path is indistinguishable from
    the monitoring being broken.
    """
    data = _read_json(CANONICAL) if CANONICAL.exists() else {}
    legacy = _read_legacy()
    if not legacy:
        return data
    merged = merge(data, legacy)
    contributed = sorted(k for k in merged if k not in data or merged[k] != data.get(k))
    if contributed:
        print(f"sync_heartbeat: LEGACY FALLBACK fired for {contributed} from {LEGACY}"
              " — an unmigrated writer still stamps the deprecated %APPDATA% path", file=sys.stderr)
    return merged


def write(data: dict) -> None:
    """Atomically replace the canonical file. NEVER falls back to %APPDATA% — a writer that fell
    back would re-create the split this move just removed.

    Atomic (temp in the same directory + `os.replace`) rather than the plain `write_text` both
    writers used before: a crash mid-write left a truncated file, and an unreadable heartbeat is
    graded exactly like a dead ingest.
    """
    CANONICAL.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CANONICAL.parent), suffix=".synchb")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2)
        os.replace(tmp, CANONICAL)
    finally:
        if os.path.exists(tmp):
            with contextlib.suppress(OSError):
                os.remove(tmp)
