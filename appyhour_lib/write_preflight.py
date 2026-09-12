"""Pre-flight writer-collision check — the gate every shipping.db writer passes before it mutates.

Rules SSOT: ``ShippingReports/WRITE_PREFLIGHT_RULES.md``. Read that before changing anything here.

🔴 WHY THIS EXISTS — negatives first
====================================
1. **Three WAL corruptions (2026-06-27, 07-01, 07-03) were caused by a SECOND CONCURRENT
   WRITER with no mutual awareness** — not by "a repair script ran". Under MSIX one path
   string resolved to two physical files, so two writers each checkpointed their own WAL
   into one image. ``appyhour_lib.paths.assert_canonical_db`` now stops the two-names half
   and ``appyhour_lib.db.connect`` stops the two-lock-taking-writers half — but **25 of 33
   writers open the DB with raw ``sqlite3.connect`` and never touch the advisory lock**
   (measured 2026-08-20, ``scripts/db_write_gate.py``). "The lockfile is free" therefore
   proves nothing on its own. Only the ``BEGIN IMMEDIATE`` probe and the PROCESS scan
   observe a writer that never advertised itself.

2. **2026-09-07 — the PENDING half, which nothing checked at all.** A review agent went to
   land ``GelPackCalculator@2e60c45`` and found **93 lines of another session's uncommitted
   work** in ``shipping_invoice_db.py``: session ``UPSParserFix`` was hand-implementing the
   same line-summing fix inside the very functions that commit deletes. Neither side was
   warned. A writer does not have to be RUNNING to collide — uncommitted in-flight work on
   the same source file is a writer that is about to run, and it is invisible to every
   process-level check ever built here. That is the axis Kurt named: *"then make it so that
   we check against any running writers ... or pending writers"*.

🔴 The negatives that shape the API
===================================
* **It REFUSES; it never warns-and-proceeds.** :func:`assert_no_conflicting_writer` raises
  :class:`WriterCollision`. A caller that wanted a boolean would ignore it — that is how the
  advisory lock got bypassed 25 times.
* **UNKNOWN is NEVER clear** (COORDINATION_RECORDS_RULES gotcha 1). A session with no beat
  or a stale beat is UNKNOWN, not idle. Absence of beats is absence of evidence, so the
  report says ``coordination_verdict = UNKNOWN`` rather than implying the field is quiet.
  Do NOT "simplify" this into a pass.
* **A stale lock is broken; a LIVE lock is never auto-stolen.** A dead holder must not wall
  a writer forever (that is a deadlock, not safety). An alive holder is a real writer and
  taking its lock is exactly the collision this module exists to prevent.
* **A probe that could not RUN is not a clear result.** Failing to enumerate processes or
  scheduled tasks refuses, rather than assuming the machine is quiet.
* **``--force`` must NAME every writer it overrides.** A bare ``--force`` is a bypass with a
  polite name; naming the collider is what makes the override a decision instead of a habit.
  🔴 The empirical checks (a held sqlite write lock, a failed probe) are **not overridable at
  all** — overriding an *advertised* writer is a judgement call, overriding a *held write
  lock* is corruption.

Usage::

    from appyhour_lib.write_preflight import writer_lock

    with writer_lock("delivery-status-recovery", source_files=[__file__]) as con:
        con.execute("UPDATE ...")

or, to check without holding anything::

    assert_no_conflicting_writer("delivery-status-recovery", source_files=[__file__])
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .db import connect as _db_connect
from .db import pid_alive
from .paths import DATA_ROOT, db_path

__all__ = [
    "WriterCollision", "PreflightReport", "assert_no_conflicting_writer", "writer_lock",
    "preflight", "WRITER_TASKS", "BYPASSING_WRITERS",
]

# ── Where the coordination substrate lives (module-level so tests can redirect them) ──
WORKSPACE = Path(r"C:\Users\Work\Claude Projects")
BUSY_STATE = WORKSPACE / "_coordination" / "busy-state.jsonl"
ACTIVE_CLAIMS = WORKSPACE / "ACTIVE-CLAIMS.md"
# 🔴 Beside the canonical DB, NOT %APPDATA% — a virtualized lock is invisible to a packaged
# caller and silently un-guards (the exact seed lesson from sync_logon.LOCK_PATH).
LOCK_DIR = DATA_ROOT / "writer_locks"

# A beat older than this is UNKNOWN. Matches coord.py's default staleness window.
STALE_BEAT_S = 1800.0
# A lock whose holder is alive but this old is a SIGKILL/atexit-never-ran backstop.
LOCK_MAX_AGE_S = 3600.0
# A -wal this large is evidence of an in-flight or abandoned writer. WARNING, never a refusal:
# a big WAL is entirely normal straight after a bulk load, so refusing on it would train
# operators to pass --force, which is worse than the signal is worth.
HOT_WAL_BYTES = 64 * 1024 * 1024
DEFAULT_BLACKOUT_MIN = int(os.environ.get("AH_GATE_BLACKOUT_MIN", "20"))

# 🔴 Processes whose LIVENESS blocks a write. Shared with scripts/db_write_gate.py, which
# imports this tuple rather than keeping a second copy. Do NOT remove an entry because it
# migrated to db.connect(): a writer that takes the lock PER BATCH is unlocked for most of
# its run and will write again seconds from now, so check_lockfile would wave it through.
BYPASSING_WRITERS = (
    "AppyHourMCP/server.py",
    "AppyHourMCP\\server.py",
    "sync_logon.py",
    "sync_carrier_invoices.py",
    "daily_shipping_sync.py",
    "gel_pack_webview.py",
    "gel_pack_shopify.py",
    "auto_import.py",
    "weather_sync_cron.py",
    "sync_shopify_orders.py",
    "pp_backfill_aged_out.py",
    "recover_undelivered_from_invoice.py",
)

# 🔴 ENUMERATED from `Get-ScheduledTask` on 2026-09-07, not assumed. The previous list in
# db_write_gate.py was missing BOTH sync_logon schtasks — `appyhour_sync_on_logon` and
# `appyhour_sync_daily_noon` — i.e. the single busiest writer on this machine was absent
# from the "is a scheduled writer about to fire" check. Re-derive with:
#   Get-ScheduledTask | ForEach-Object { $i=$_|Get-ScheduledTaskInfo; "$($_.TaskName) $($i.NextRunTime)" }
WRITER_TASKS = (
    "AppyHour Carrier Invoice Sync",
    "appyhour_sync_on_logon",
    "appyhour_sync_daily_noon",
    "appyhour_daily_mon", "appyhour_daily_tue", "appyhour_daily_wed",
    "appyhour_daily_thu", "appyhour_daily_fri",
    "AppyHour Weekly Offsite Backup",
    "AppyHour Zone Floor Rebuild",
    "AppyHour-vF-Archive-Refresh",
    "appyhour-db-healthcheck",
    "GorgiasUpdate",
)


class WriterCollision(RuntimeError):
    """Preflight refused: another writer is running, pending, or imminent. Nothing was written."""


@dataclass
class Collision:
    axis: str
    writer: str
    detail: str
    overridable: bool = True

    def __str__(self) -> str:
        return f"[{self.axis}] {self.writer}: {self.detail}"


@dataclass
class PreflightReport:
    surface: str
    db: str
    clear: bool = False
    forced: bool = False
    collisions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    unknown_sessions: list = field(default_factory=list)
    coordination_verdict: str = "UNKNOWN"

    def summary(self) -> str:
        lines = [f"write-preflight surface={self.surface!r} db={self.db}",
                 f"  coordination: {self.coordination_verdict}"
                 f" (UNKNOWN is never idle — see COORDINATION_RECORDS_RULES gotcha 1)"]
        for c in self.collisions:
            lines.append(f"  x COLLISION {c}")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        if self.clear and not self.collisions:
            lines.append("  CLEAR — no conflicting writer detected.")
        return "\n".join(lines)


# ── axis 1: SQLite level — the only check that observes a REAL writer ────────────────────

def _begin_immediate_probe(path: Path, collisions: list) -> None:
    """Empirical proof nobody holds the write lock RIGHT NOW.

    Short timeout on purpose: this is a probe, not a wait. If someone has it we want to
    abort, not queue behind them and write the moment they let go.
    🔴 NOT overridable. Overriding an advertised writer is a judgement call; writing over a
    held sqlite write lock is the corruption mechanism itself.
    """
    try:
        con = sqlite3.connect(str(path), timeout=3.0)
        try:
            con.execute("PRAGMA busy_timeout=3000")
            con.execute("BEGIN IMMEDIATE")
            con.rollback()
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        collisions.append(Collision(
            "sqlite", "an unidentified live writer",
            f"BEGIN IMMEDIATE refused ({e}) — another process holds the write lock on "
            f"{path}. This is the empirical check: it sees writers that never advertised "
            f"themselves (25 of 33 use raw sqlite3.connect).", overridable=False))
    except Exception as e:  # noqa: BLE001 — a probe that cannot run is not a clear result
        collisions.append(Collision(
            "sqlite", "probe failure",
            f"write-lock probe failed: {type(e).__name__}: {e} — refusing rather than "
            f"assuming the DB is quiet.", overridable=False))


def _wal_warnings(path: Path, warnings: list) -> None:
    for suffix, label in (("-wal", "WAL"), ("-shm", "shared-memory")):
        side = Path(str(path) + suffix)
        try:
            if not side.exists():
                continue
            size = side.stat().st_size
        except OSError:
            continue
        if suffix == "-wal" and size > HOT_WAL_BYTES:
            warnings.append(
                f"hot {label} sidecar: {side.name} is {size / 1e6:.0f} MB — evidence of an "
                f"in-flight or abandoned writer. Not a refusal (a large WAL is normal after "
                f"a bulk load), but do not start a long write on top of it blind.")


def _advisory_lock_holder(path: Path, collisions: list) -> None:
    """The ``<db>.writelock`` from appyhour_lib.db. Covers only lock-taking writers — which
    is precisely why it is one axis of five and not the whole check."""
    try:
        from .db import write_lock_holder
        holder = write_lock_holder(path)
    except Exception as e:  # noqa: BLE001
        collisions.append(Collision("advisory-lock", "probe failure",
                                    f"{type(e).__name__}: {e}", overridable=False))
        return
    if holder:
        collisions.append(Collision(
            "advisory-lock", str(holder.get("script", "?")),
            f"holds <db>.writelock: PID {holder.get('pid')} since "
            f"{holder.get('started_at', '?')}"))


# ── axis 2: surface-keyed lock (generalised from sync_logon.LOCK_PATH) ───────────────────

def _lock_path(surface: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in surface)
    return LOCK_DIR / f"{safe}.writer.lock"


def _read_lock(p: Path) -> dict | None:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _lock_is_stale(holder: dict | None) -> bool:
    """🔴 Dead pid → stale (must not deadlock forever). Alive pid → NEVER stale by age alone
    unless it is past the SIGKILL backstop; an alive holder is a real writer."""
    if holder is None:
        return True                                   # torn/unreadable payload
    pid = holder.get("pid")
    if not isinstance(pid, int) or pid <= 0 or not pid_alive(pid):
        return True
    started = holder.get("started_at_epoch")
    return isinstance(started, (int | float)) and (time.time() - started) > LOCK_MAX_AGE_S


def _check_surface_lock(surface: str, collisions: list, warnings: list,
                        self_holds: bool) -> None:
    p = _lock_path(surface)
    if not p.exists():
        return
    holder = _read_lock(p)
    if _lock_is_stale(holder):
        warnings.append(
            f"stale writer lock {p.name} (holder pid "
            f"{(holder or {}).get('pid', '?')} is not alive) — it will be taken over. A dead "
            f"holder must not wall a writer forever.")
        return
    if self_holds and isinstance(holder, dict) and holder.get("pid") == os.getpid():
        return                                        # our own re-entrant check
    collisions.append(Collision(
        "surface-lock", str((holder or {}).get("session") or (holder or {}).get("script", "?")),
        f"holds the {surface!r} writer lock: pid {(holder or {}).get('pid')} since "
        f"{(holder or {}).get('started_at', '?')}. 🔴 A LIVE holder is never auto-stolen."))


# ── axis 3: busy-state beats — UNKNOWN is NEVER idle ─────────────────────────────────────

def _self_session() -> str:
    return (os.environ.get("AH_SESSION_NAME")
            or os.environ.get("CLAUDE_SESSION_NAME") or "").strip()


def _surface_tokens(surface: str, source_files) -> list:
    toks = {surface.lower()}
    for f in source_files or ():
        toks.add(Path(f).stem.lower())
    return [t for t in toks if len(t) >= 4]


def _check_beats(surface: str, source_files, collisions: list, warnings: list,
                 unknown: list) -> str:
    """Read busy-state.jsonl directly (same schema coord.py writes: session/gen/seq/state/
    task/ts). Deliberately NOT shelling out to coord.py: a preflight that depends on another
    process starting is one more thing that can fail open."""
    rows = []
    try:
        if BUSY_STATE.exists():
            with open(BUSY_STATE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError as e:
        warnings.append(f"could not read busy-state ({e}) — coordination is UNKNOWN.")
        return "UNKNOWN"

    latest = {}
    for r in rows:
        s = r.get("session")
        if not s:
            continue
        prev = latest.get(s)
        if prev is None or r.get("gen") != prev.get("gen") or r.get("seq", 0) >= prev.get("seq", 0):
            latest[s] = r

    if not latest:
        warnings.append(
            "busy-state.jsonl carries no beats — coordination verdict UNKNOWN, NOT clear. "
            "Absence of a beat is absence of evidence (COORDINATION_RECORDS_RULES gotcha 1).")
        return "UNKNOWN"

    me = _self_session().lower()
    toks = _surface_tokens(surface, source_files)
    now, any_unknown, any_fresh = time.time(), False, False
    for session, row in latest.items():
        if session.lower() == me:
            continue
        state, ts = row.get("state"), row.get("ts")
        try:
            age = now - float(ts)
        except (TypeError, ValueError):
            age = float("inf")
        task = str(row.get("task") or "")
        hit = any(t in task.lower() or t in session.lower() for t in toks)
        if state == "done":
            continue                                   # terminal beats do not go stale
        if age > STALE_BEAT_S or state not in ("working", "blocked"):
            any_unknown = True
            unknown.append(f"{session} (age {age / 3600:.1f}h, raw={state})")
            if hit:
                warnings.append(
                    f"🔴 UNKNOWN session {session!r} last claimed something matching "
                    f"{surface!r} ({task[:90]!r}) but its beat is {age / 3600:.1f}h stale. "
                    f"UNKNOWN is NEVER idle — read that session's transcript before writing.")
            continue
        any_fresh = True
        if hit:
            collisions.append(Collision(
                "busy-state", session,
                f"has a FRESH beat (age {age:.0f}s, state={state}) claiming work that matches "
                f"surface {surface!r}: {task[:160]!r}"))
    if any_unknown or not any_fresh:
        if not any_unknown and not any_fresh:
            warnings.append("no live peer beats — coordination verdict UNKNOWN, not idle.")
        return "UNKNOWN"
    return "OBSERVED"


def _check_active_claims(surface: str, source_files, warnings: list) -> None:
    """ACTIVE-CLAIMS is ownership, not liveness — so it WARNS, never refuses (claims go
    stale and a stale claim is a false wall). But an unread claim is how two sessions end up
    on one file, so it must be surfaced."""
    try:
        text = ACTIVE_CLAIMS.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    toks = _surface_tokens(surface, source_files)
    for line in text.splitlines():
        low = line.lower()
        if not low.strip().startswith("|"):
            continue
        for t in toks:
            if t in low:
                stream = line.strip().strip("|").split("|")[0].strip()
                warnings.append(
                    f"ACTIVE-CLAIMS row {stream!r} names {t!r} — another stream may own this "
                    f"surface. Check its handoff before writing (a claim is ownership, not "
                    f"liveness, so this is a warning, not a refusal).")
                break


# ── axis 4: PENDING writers — uncommitted work on the writer's own source ────────────────

def _git(args: list, cwd: Path):
    return subprocess.run(["git", *args], cwd=str(cwd),  # noqa: S603, S607
                          capture_output=True, text=True, timeout=30)


def _pending_edits(files) -> tuple:
    """Return ``(collisions, warnings)`` for uncommitted work on the writer's own sources.

    🔴 THE 2026-09-07 UPSParserFix BURN LIVES HERE. A dirty source file is a PENDING writer:
    another session is mid-edit on the code that is about to run. Nothing else in this module
    can see it — it is not a process, holds no lock, and beats nothing.

    NEGATIVE: a file in NO git repo (the workspace root is unversioned; GelPackCalculator is
    a separate nested repo gitignored from its parent) must WARN, never silently pass as
    clean and never raise. "I could not check" and "it is clean" are different answers.
    """
    collisions, warnings = [], []
    by_repo: dict = {}
    for f in files or ():
        p = Path(f).resolve()
        if not p.exists():
            warnings.append(f"source file {p} does not exist — cannot check for pending edits.")
            continue
        try:
            r = _git(["rev-parse", "--show-toplevel"], p.parent)
        except (OSError, subprocess.SubprocessError) as e:
            warnings.append(f"git unavailable for {p.name} ({type(e).__name__}) — pending-writer "
                            f"check SKIPPED, not passed.")
            continue
        if r.returncode != 0:
            warnings.append(f"{p.name} is in no git repo — pending-writer check could not run "
                            f"for it. Not the same as clean.")
            continue
        by_repo.setdefault(Path(r.stdout.strip()), []).append(p)

    for repo, paths in by_repo.items():
        try:
            st = _git(["status", "--porcelain", "--", *[str(x) for x in paths]], repo)
        except (OSError, subprocess.SubprocessError) as e:
            warnings.append(f"git status failed in {repo} ({type(e).__name__}) — SKIPPED.")
            continue
        if st.returncode != 0:
            warnings.append(f"git status failed in {repo}: {st.stderr.strip()[:120]} — SKIPPED.")
            continue
        for line in st.stdout.splitlines():
            if not line.strip():
                continue
            code, _, name = line.partition(" ")
            name = line[3:].strip() if len(line) > 3 else name
            branch = ""
            with contextlib.suppress(Exception):
                branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
            collisions.append(Collision(
                "pending", f"an uncommitted editor of {Path(name).name}",
                f"{name} has UNCOMMITTED changes ({line[:2].strip() or '??'}) in {repo} "
                f"(branch {branch or '?'}). Another session is mid-edit on this writer's own "
                f"source — a PENDING writer. This is the 2026-09-07 UPSParserFix collision: "
                f"93 lines of in-flight work in shipping_invoice_db.py that no process-level "
                f"check could see. Land or stash it, or name the session with --force-writer."))
    return collisions, warnings


# ── axis 5: running processes + imminent scheduled owners ────────────────────────────────

def _live_bypassing_writers() -> list:
    """The check that actually covers the raw-connect writers the advisory lock cannot see."""
    # 🔴 2026-09-12: this read `.stdout` straight off `subprocess.run(...)` and then called
    # `.splitlines()` on it. When the child produces no captured stdout the attribute is None, so
    # the guard died with `AttributeError: 'NoneType' object has no attribute 'splitlines'` INSTEAD
    # of refusing — a probe that crashes is strictly worse than one that says it could not run,
    # because the traceback reads as "the tool is broken" rather than "the answer is unknown", and
    # the obvious next move is to bypass it. Hit live while repairing `shipments.acct`.
    #
    # 🔴 AN EMPTY PROCESS LIST IS NOT "NO COLLIDERS". This machine always has running processes, so
    # empty output means the enumeration FAILED (no PowerShell on PATH, a sandbox that blocks
    # Win32_Process, a nonzero exit). Treating it as quiet would silently disable the one axis that
    # covers the 25-of-33 writers on raw `sqlite3.connect` — the axis the advisory lock cannot see.
    # Fail CLOSED, same doctrine as the exception branch.
    # 🔴 NEVER `text=True` HERE. Python decodes with the ANSI codepage (cp1252 on this machine) and
    # ONE process whose command line carries a byte cp1252 has no mapping for — 0x8f, measured live
    # 2026-09-12 at position 139,321 — raises UnicodeDecodeError and destroys the ENTIRE
    # enumeration. Not that process: all of them. So the axis that covers the raw-connect writers
    # has been silently returning nothing on this machine, and the failure surfaced only as a
    # `None.splitlines()` crash three layers up. Decode bytes ourselves, replacing what we cannot
    # map: a mangled character in one command line must never cost us the other 400 rows.
    # (Workspace rule: "Write files with explicit UTF-8. cp1252 default breaks on Unicode.")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
             "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
            capture_output=True, timeout=60)
    except Exception as e:  # noqa: BLE001
        return [f"(could not enumerate processes — refusing rather than assuming quiet: "
                f"{type(e).__name__})"]
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0 or not out.strip():
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        return [f"(process enumeration returned nothing — rc={proc.returncode}; refusing rather "
                f"than assuming quiet{': ' + err[0][:120] if err else ''})"]
    me, found = str(os.getpid()), []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        pid, cmd = line.split("\t", 1)
        if pid.strip() == me:
            continue
        for needle in BYPASSING_WRITERS:
            if needle.lower() in cmd.lower():
                found.append(f"pid {pid.strip()}: {os.path.basename(needle)}")
                break
    return found


def _imminent_tasks(minutes: int) -> list:
    """A writer starting 30 seconds before a scheduled writer is a collision waiting to
    happen. 🔴 A task with NO NextRunTime (``appyhour_sync_on_logon``) is not 'never' — it
    is trigger-driven and can fire at any logon; it is covered by the PROCESS check."""
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-ScheduledTask | ForEach-Object { $i=$_|Get-ScheduledTaskInfo; "
             "\"$($_.TaskName)`t$($i.NextRunTime)\" }"],
            capture_output=True, text=True, timeout=90).stdout
    except Exception:  # noqa: BLE001
        return ["(could not read scheduled tasks — refusing rather than assuming quiet)"]
    soon, horizon = [], datetime.now() + timedelta(minutes=minutes)
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, nxt = (x.strip() for x in line.split("\t", 1))
        if name not in WRITER_TASKS or not nxt:
            continue
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                when = datetime.strptime(nxt, fmt)
            except ValueError:
                continue
            if datetime.now() <= when <= horizon:
                soon.append(f"{name} fires {when:%Y-%m-%d %I:%M %p}")
            break
    return soon


# ── the gate ─────────────────────────────────────────────────────────────────────────────

def preflight(surface: str, *, db_path_: Path | None = None, source_files=(),
              blackout_min: int = DEFAULT_BLACKOUT_MIN,
              self_holds: bool = True) -> PreflightReport:
    """Run every axis and return the report. Never raises on a collision — see
    :func:`assert_no_conflicting_writer` for the enforcing form."""
    target = Path(db_path_) if db_path_ is not None else db_path()
    rep = PreflightReport(surface=surface, db=str(target))

    # 🔴 Sidecars are inspected BEFORE the probe opens the DB. sqlite removes a stray `-wal`
    # on connect when the header is not WAL, so probing first destroys the evidence this
    # check exists to read. Observe, then perturb — never the reverse.
    _wal_warnings(target, rep.warnings)
    _begin_immediate_probe(target, rep.collisions)
    _advisory_lock_holder(target, rep.collisions)
    _check_surface_lock(surface, rep.collisions, rep.warnings, self_holds)
    rep.coordination_verdict = _check_beats(surface, source_files, rep.collisions,
                                            rep.warnings, rep.unknown_sessions)
    _check_active_claims(surface, source_files, rep.warnings)

    pend_c, pend_w = _pending_edits(source_files)
    rep.collisions.extend(pend_c)
    rep.warnings.extend(pend_w)

    for w in _live_bypassing_writers():
        overridable = "could not enumerate" not in w
        rep.collisions.append(Collision("process", w, "a lock-BYPASSING writer is alive",
                                        overridable=overridable))
    for t in _imminent_tasks(blackout_min):
        overridable = "could not read" not in t
        rep.collisions.append(Collision(
            "scheduled", t, f"a scheduled writer fires within {blackout_min} minutes",
            overridable=overridable))

    rep.clear = not rep.collisions
    return rep


def assert_no_conflicting_writer(surface: str, *, db_path: Path | None = None,
                                 source_files=(), blackout_min: int = DEFAULT_BLACKOUT_MIN,
                                 force_override=None,
                                 _self_holds: bool = True) -> PreflightReport:
    """REFUSE to proceed unless no other writer is running, pending, or imminent.

    Args:
        surface: what is about to be written, e.g. ``"delivery-status-recovery"``. Also the
            key of the mutual-exclusion lock, so two runs of the same writer serialise.
        db_path: DB to probe. Defaults to the canonical ``C:\\AppyHourData\\shipping.db``.
        source_files: the writer's OWN source files. This is what powers the pending-writer
            axis; pass ``[__file__]`` at minimum, plus any module it is about to rewrite.
        force_override: ``None`` (normal) or a STRING naming the colliding writer(s) to
            override, comma-separated. 🔴 ``True`` and other non-strings are rejected: a bare
            force is a bypass with a polite name. Every overridable collision must be named,
            and the empirical sqlite/probe checks cannot be overridden at all.

    Raises:
        WriterCollision: naming which writer it collided with, on which axis.
    """
    rep = preflight(surface, db_path_=db_path, source_files=source_files,
                    blackout_min=blackout_min, self_holds=_self_holds)
    if not rep.collisions:
        return rep

    if force_override is None:
        raise WriterCollision(_refusal(rep))

    if not isinstance(force_override, str) or not force_override.strip():
        raise WriterCollision(
            "🔴 A BARE --force IS REFUSED. Overriding requires naming the colliding writer "
            "explicitly (--force-writer \"<name>\"), so that the override is a decision "
            "about a known writer rather than a habit.\n" + _refusal(rep))

    named = {n.strip().lower() for n in force_override.split(",") if n.strip()}
    hard = [c for c in rep.collisions if not c.overridable]
    if hard:
        raise WriterCollision(
            "🔴 THESE COLLISIONS CANNOT BE OVERRIDDEN AT ALL — they are empirical, not "
            "advertised. Writing over a held sqlite write lock (or past a probe that could "
            "not run) is the corruption mechanism itself, not a judgement call.\n"
            + "\n".join(f"  x {c}" for c in hard))
    unnamed = [c for c in rep.collisions
               if not any(n in c.writer.lower() or n in c.detail.lower() for n in named)]
    if unnamed:
        raise WriterCollision(
            f"🔴 --force {force_override!r} does not name every colliding writer. Unnamed:\n"
            + "\n".join(f"  x {c}" for c in unnamed) +
            "\nName each one explicitly, or quiesce it.")

    print("=" * 78)
    print(f"🔴 WRITE-PREFLIGHT OVERRIDE for surface {surface!r} — proceeding DESPITE:")
    for c in rep.collisions:
        print(f"    x {c}")
    print(f"  Overridden by explicit name: {force_override}")
    print("=" * 78)
    rep.forced = True
    rep.clear = True
    return rep


def _refusal(rep: PreflightReport) -> str:
    return (f"🔴 WRITE REFUSED for surface {rep.surface!r} — "
            f"{len(rep.collisions)} conflicting writer(s). Nothing was written.\n"
            + "\n".join(f"  x {c}" for c in rep.collisions)
            + ("\n" + "\n".join(f"  ! {w}" for w in rep.warnings) if rep.warnings else "")
            + f"\n  coordination verdict: {rep.coordination_verdict} "
              f"(UNKNOWN is NEVER idle)\n"
              f"  Quiesce the writer(s) above and re-run, or override by NAME.")


@contextlib.contextmanager
def writer_lock(surface: str, *, db_path: Path | None = None, source_files=(),
                blackout_min: int = DEFAULT_BLACKOUT_MIN, force_override=None,
                open_connection: bool = False):
    """Preflight, then HOLD the surface lock for the duration of the write.

    The check alone is a time-of-check/time-of-use race: two writers can both pass a clean
    preflight a millisecond apart. The lock is what makes the verdict hold. It is taken
    AFTER the preflight passes and released on the way out, exception or not.

    Yields the :class:`PreflightReport`, or a write connection when ``open_connection=True``.
    """
    rep = assert_no_conflicting_writer(surface, db_path=db_path, source_files=source_files,
                                       blackout_min=blackout_min, force_override=force_override)
    p = _lock_path(surface)
    p.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    for _attempt in range(2):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _read_lock(p)
            if not _lock_is_stale(holder):
                raise WriterCollision(
                    f"surface {surface!r} lock taken between preflight and acquire by "
                    f"pid {(holder or {}).get('pid')} — refusing (this is the race the lock "
                    f"exists to close).") from None
            print(f"[write-preflight] stale lock {p.name} (pid "
                  f"{(holder or {}).get('pid', '?')} not alive) — taking over")
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump({"pid": os.getpid(), "surface": surface,
                       "session": _self_session() or os.path.basename(sys.argv[0] or "?"),
                       "script": os.path.basename(sys.argv[0] or "?"),
                       "host": socket.gethostname(),
                       "started_at": datetime.now().isoformat(timespec="seconds"),
                       "started_at_epoch": time.time()}, fp)
        acquired = True
        break
    if not acquired:
        raise WriterCollision(f"could not acquire the {surface!r} writer lock after a "
                              f"stale takeover")

    con = None
    try:
        if open_connection:
            con = _db_connect(Path(db_path) if db_path is not None else None)
            yield con
            con.commit()
        else:
            yield rep
    except Exception:
        if con is not None:
            with contextlib.suppress(Exception):
                con.rollback()
        raise
    finally:
        if con is not None:
            with contextlib.suppress(Exception):
                con.close()
        # Release ONLY if it still carries our pid — never a successor's after a takeover.
        try:
            if (_read_lock(p) or {}).get("pid") == os.getpid():
                p.unlink()
        except OSError:
            pass


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Pre-flight writer-collision check for shipping.db")
    ap.add_argument("surface", nargs="?", default="manual-check")
    ap.add_argument("--source-file", action="append", default=[],
                    help="a source file of the writer (repeatable) — powers the pending check")
    ap.add_argument("--blackout-min", type=int, default=DEFAULT_BLACKOUT_MIN)
    args = ap.parse_args(argv)
    rep = preflight(args.surface, source_files=args.source_file,
                    blackout_min=args.blackout_min)
    print(rep.summary())
    return 0 if rep.clear else 1


if __name__ == "__main__":
    raise SystemExit(main())
