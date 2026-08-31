"""Canonical SQLite connection helper for the shared shipping.db.

Why this module exists
======================
On 2026-06-27 the live ``shipping.db`` corrupted (138 MB -> 22 MB, malformed)
during normal operation. Root cause: ``shipping.db`` is written by *many*
concurrent processes (6 leaked MCP servers + the weather cron + sync_logon +
the Gorgias tee), and **every one of them opened the DB raw** -
``sqlite3.connect(path)`` with no ``busy_timeout`` and no enforced
``journal_mode``. SQLite is perfectly happy with many readers + serialized
writers, but ONLY if every connection sets ``busy_timeout`` so a second writer
*waits* for the lock instead of racing a checkpoint. Without it, a checkpoint
running while another process held the WAL truncated the main file.

The fix is to make concurrent access *safe*, not to forbid it. Every consumer
that touches ``shipping.db`` MUST open it through :func:`connect` (writers) or
:func:`connect_ro` (readers). The pragmas below are the difference between
"6 processes coexist fine" and "torn database".

Single-writer lock (2026-07-02)
===============================
``busy_timeout`` serializes lock *waits* but does NOT stop two independent
*processes* from each managing/checkpointing the WAL at the same time - which
re-corrupted the DB on 2026-07-01 when a manual ingest raced the live MCP
servers. So :func:`connect` (the writer path) also takes an **advisory
single-writer lock**: a ``<db>.writelock`` file created atomically
(``O_CREAT|O_EXCL``) beside the real DB. At most ONE writer process holds it;
a second :func:`connect` waits up to ``AH_WRITE_LOCK_WAIT`` seconds (default 90)
then raises :class:`DBWriterBusy` naming the holder, instead of corrupting.

* Crash-safe: a lock whose holder PID is dead (or whose PID was reused - detected
  by comparing the stored process *create time*) is broken and re-acquired.
  ``AH_WRITE_LOCK_MAX_AGE`` (default 1800s) is a belt-and-suspenders backstop for
  a SIGKILL where ``atexit`` never ran.
* Reentrant: nested :func:`connect` in one process share the lock via a refcount;
  it releases only when the last write connection closes.
* Readers never lock: :func:`connect_ro` is untouched - the health check,
  monitors, and analysis tools must never block.
* Escape hatch: ``AH_WRITE_LOCK_DISABLE=1`` restores the pre-lock behavior.

Canonical-path guard (2026-08-31)
=================================
🔴 Neither of the two paragraphs above is the corruption story. A controlled experiment
(4 writer processes, scratch DBs) ran ~2,900 transactions clean on ONE path with no
``busy_timeout``, and corrupted 5/5 on TWO NAMES for one file (NTFS hardlink) *with*
``busy_timeout=10000``. The mechanism is two names → two ``-shm`` lock namespaces → two
independent checkpointers folding their own WAL into one shared image. The advisory lock
cannot see it: the lock file is ``str(target) + ".writelock"``, so a second name gets its
own lock and both writers are granted one (measured).

So :func:`connect` now calls :func:`appyhour_lib.paths.assert_canonical_db` and RAISES
:class:`NonCanonicalDBPath` on a non-canonical ``shipping.db``. A one-shot run from the
wrong directory breaks — deliberately; the message names the offending path, the canonical
path, and the fix. Promoted from ``sync_logon._resolve_db_guarded`` (the only writer that
had it) rather than reinvented.

NEGATIVE: this prevents a REGRESSION to a second name. It would NOT have prevented
2026-07-03 — the canonical path that day was the MSIX-virtualized ``%APPDATA%`` one, and
MSIX splits packaged from unpackaged writers at the same name string. The 7/08 move to
``C:\\AppyHourData`` is what stopped the corruptions. Do not report the guard as that fix.

Pragma rationale
================
* ``journal_mode=WAL``     - many concurrent readers don't block the writer.
                             Persists in the file header, but we set it every
                             open so a freshly-restored DB is never left in the
                             default rollback-journal mode.
* ``busy_timeout=10000``   - THE corruption fix. NOT persisted - every single
                             connection must set it. A blocked writer waits up
                             to 10 s for the lock instead of erroring out and
                             racing.
* ``synchronous=NORMAL``   - safe + fast under WAL (fsync on checkpoint, not
                             every commit). FULL is overkill for WAL; OFF risks
                             corruption on power loss.
* ``foreign_keys=ON``      - enforce relational integrity (off by default).
* ``wal_autocheckpoint``   - keep the WAL bounded so it can't grow unbounded.

Readers use ``mode=ro`` (immutable=0) so they apply the WAL and see committed
writes, while being structurally unable to take a write lock or trigger a
checkpoint - which keeps the pool of *writers* small (the thing that races).
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .paths import NonCanonicalDBPath, assert_canonical_db, db_path

__all__ = [
    "connect", "connect_ro", "integrity_ok", "BUSY_TIMEOUT_MS",
    "DBWriterBusy", "LockedConnection", "NonCanonicalDBPath",
    "write_lock_holder", "assert_write_lock_free",
]

BUSY_TIMEOUT_MS = 10_000
_WAL_AUTOCHECKPOINT_PAGES = 1000

# Single-writer lock defaults (all env-overridable).
_DEFAULT_WRITE_LOCK_WAIT = 90.0      # seconds a 2nd writer waits before DBWriterBusy
_DEFAULT_WRITE_LOCK_MAX_AGE = 1800.0  # seconds; break a lock older than this (SIGKILL backstop)
_LOCK_POLL = 0.25                     # seconds between contention polls


class DBWriterBusy(RuntimeError):
    """Raised by :func:`connect` when another live process holds the write lock."""


def _apply_writer_pragmas(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute(f"PRAGMA wal_autocheckpoint={_WAL_AUTOCHECKPOINT_PAGES}")


# ── Process liveness / create-time (stdlib only; ctypes on Windows) ───────────
# create_time defeats PID reuse: a live PID whose create_time != the stored one
# is a *different* process, so the lock is stale.
if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    # Set restypes/argtypes so 64-bit HANDLEs aren't truncated to 32-bit int.
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _FT = ctypes.POINTER(wintypes.FILETIME)
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE, _FT, _FT, _FT, _FT]

    def _filetime_int(ft: wintypes.FILETIME) -> int:
        return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

    def _pid_alive(pid: int) -> bool:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            _k32.CloseHandle(h)

    def _proc_create_time(pid: int) -> int | None:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                        ctypes.byref(k), ctypes.byref(u)):
                return None
            return _filetime_int(c)
        finally:
            _k32.CloseHandle(h)

    def _self_create_time() -> int | None:
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        if not _k32.GetProcessTimes(_k32.GetCurrentProcess(), ctypes.byref(c),
                                    ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
            return None
        return _filetime_int(c)
else:  # POSIX fallback (no create_time reuse guard; MAX_AGE covers staleness)
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, not ours
        except OSError:
            return False
        return True

    def _proc_create_time(pid: int) -> int | None:  # noqa: ARG001
        return None

    def _self_create_time() -> int | None:
        return None


# ── Advisory single-writer lock ───────────────────────────────────────────────
_refcounts: dict[str, int] = {}          # lockpath -> nesting depth in THIS process
_refcount_lock = threading.Lock()
_atexit_registered = False


def _lock_disabled() -> bool:
    return os.environ.get("AH_WRITE_LOCK_DISABLE", "").strip().lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def _read_lock(lockpath: str) -> dict | None:
    try:
        with open(lockpath, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None


def _holder_msg(lockpath: str, holder: dict | None) -> str:
    if not holder:
        return f"another writer holds {os.path.basename(lockpath)} (unreadable holder record)"
    return (f"another writer holds shipping.db: PID {holder.get('pid')} "
            f"{holder.get('script', '?')} since {holder.get('started_at', '?')}")


def _lock_is_stale(lockpath: str, holder: dict | None, max_age: float) -> bool:
    now = time.time()
    if holder is None:
        # Empty/torn/unparseable lock file. A live process writes its payload in
        # microseconds, so only break if the file has been sitting unreadable a while.
        try:
            return (now - os.path.getmtime(lockpath)) > min(max_age, 30.0)
        except OSError:
            return True
    pid = holder.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return True
    stored_ct = holder.get("create_time")
    live_ct = _proc_create_time(pid)
    if stored_ct is not None and live_ct is not None and stored_ct != live_ct:
        return True  # PID was reused by a different process
    started = holder.get("started_at_epoch")
    if isinstance(started, (int, float)) and (now - started) > max_age:
        return True  # SIGKILL backstop — atexit never ran
    return False


def _create_lockfile(lockpath: str, wait_s: float, max_age_s: float) -> None:
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        try:
            fd = os.open(lockpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _read_lock(lockpath)
            if _lock_is_stale(lockpath, holder, max_age_s):
                try:
                    os.unlink(lockpath)
                except FileNotFoundError:
                    pass
                continue  # retry immediately
            if time.monotonic() >= deadline:
                raise DBWriterBusy(_holder_msg(lockpath, holder))
            time.sleep(_LOCK_POLL)
            continue
        else:
            payload = {
                "pid": os.getpid(),
                "create_time": _self_create_time(),
                "script": os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else "?",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "started_at_epoch": time.time(),
                "host": socket.gethostname(),
            }
            try:
                os.write(fd, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(fd)
            return


def _acquire_write_lock(lockpath: str, wait_s: float, max_age_s: float) -> None:
    global _atexit_registered
    with _refcount_lock:
        if _refcounts.get(lockpath, 0) == 0:
            _create_lockfile(lockpath, wait_s, max_age_s)  # may raise DBWriterBusy
        _refcounts[lockpath] = _refcounts.get(lockpath, 0) + 1
        if not _atexit_registered:
            atexit.register(_release_all_locks)
            _atexit_registered = True


def _release_write_lock(lockpath: str) -> None:
    with _refcount_lock:
        n = _refcounts.get(lockpath, 0)
        if n <= 1:
            _refcounts.pop(lockpath, None)
            try:
                os.unlink(lockpath)
            except FileNotFoundError:
                pass
        else:
            _refcounts[lockpath] = n - 1


def _release_all_locks() -> None:
    with _refcount_lock:
        for lockpath in list(_refcounts):
            try:
                os.unlink(lockpath)
            except OSError:
                pass
        _refcounts.clear()


class LockedConnection(sqlite3.Connection):
    """A shipping.db write connection that releases its single-writer lock on close."""

    _lock_path: str | None = None  # instance attr set by connect(); None = holds no lock

    def close(self) -> None:  # noqa: D401
        lockpath = getattr(self, "_lock_path", None)
        if lockpath:
            self._lock_path = None
            _release_write_lock(lockpath)
        super().close()


def connect(path: str | Path | None = None, *, timeout: float = 30.0,
            write_lock: bool = True, lock_wait: float | None = None) -> sqlite3.Connection:
    """Open a READ/WRITE connection to shipping.db with safe-concurrency pragmas
    AND an advisory single-writer lock.

    Use this for any code that INSERTs/UPDATEs/DELETEs. The ``busy_timeout``
    pragma serializes lock waits; the ``<db>.writelock`` file guarantees at most
    one writer *process* (the thing that actually corrupts the WAL).

    Args:
        path: DB path. Defaults to the canonical :func:`paths.db_path`. The lock
            file lives beside this path, so a temp DB (tests) gets its own lock.
        timeout: Python-side connect timeout (seconds).
        write_lock: acquire the single-writer lock (default True). Pass False to
            skip it (rare — e.g. a schema-only bootstrap you know is exclusive).
        lock_wait: seconds to wait for the lock before :class:`DBWriterBusy`.
            None → ``AH_WRITE_LOCK_WAIT`` env or 90s. 0 → fail fast.

    Raises:
        DBWriterBusy: another live process already holds the write lock.
        NonCanonicalDBPath: ``target`` is a SECOND NAME for the shared shipping.db image
            (legacy %APPDATA%, a relative dir, the repo-local copy). Hard refusal, not a
            warning — see :func:`appyhour_lib.paths.assert_canonical_db` for why a second
            name is the corruption mechanism and why the advisory lock cannot catch it.
            🔴 ``connect_ro`` is deliberately NOT guarded: a ``mode=ro`` connection cannot
            take a write lock or trigger a checkpoint, so it cannot participate in the
            race, and read-only work on a scratch copy is the sanctioned way to
            investigate this DB at all.
    """
    target = Path(path) if path is not None else db_path()
    assert_canonical_db(target, caller="appyhour_lib.db.connect")
    con = sqlite3.connect(str(target), timeout=timeout, factory=LockedConnection)
    _apply_writer_pragmas(con)
    con._lock_path = None
    if write_lock and not _lock_disabled():
        lockpath = str(target) + ".writelock"
        if lock_wait is not None:
            wait = float(lock_wait)
        else:
            wait = _env_float("AH_WRITE_LOCK_WAIT", _DEFAULT_WRITE_LOCK_WAIT)
        max_age = _env_float("AH_WRITE_LOCK_MAX_AGE", _DEFAULT_WRITE_LOCK_MAX_AGE)
        try:
            _acquire_write_lock(lockpath, wait, max_age)
        except DBWriterBusy:
            con.close()  # _lock_path is None → releases nothing, just closes sqlite
            raise
        con._lock_path = lockpath
    return con


def connect_ro(path: str | Path | None = None, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a READ-ONLY connection (``mode=ro``).

    Use this for every reader (reports, analytics, the shipping-data skill,
    forecasting series pulls). A read-only connection cannot take a write lock
    or trigger a checkpoint, so it can never participate in the write race that
    caused the 2026-06-27 corruption. It still applies the WAL, so it sees all
    committed writes. Readers NEVER touch the single-writer lock.
    """
    target = Path(path) if path is not None else db_path()
    uri = "file:" + target.as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=timeout)
    # busy_timeout still helps a reader wait out a checkpoint instead of erroring.
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return con


def write_lock_holder(path: str | Path | None = None) -> dict | None:
    """Return the LIVE holder record of ``<db>.writelock``, else ``None``.

    ``None`` means "no writer process holds this DB right now" — either the file is
    absent, or it is stale by the same rules :func:`connect` uses to break it (dead PID,
    reused PID, past ``AH_WRITE_LOCK_MAX_AGE``). Read-only: it never creates, breaks, or
    waits on the lock, so a monitor or a test may call it freely.

    🔴 This is the ACCEPTANCE PROBE for stage cancellation (2026-08-31). "The stage
    stopped" is not the claim that matters; "the stage left no lock behind" is. A
    cancelled stage that unwound without closing its connection still holds this file,
    and the next writer still starves.
    NEGATIVE: a free lockfile does NOT prove the DB is unwritten — 25 of 33 writers open
    it with raw ``sqlite3.connect`` and never touch the lock (``scripts/db_write_gate.py``).
    Use it to prove a lock-taking writer released, not to prove the DB is quiet.
    """
    target = Path(path) if path is not None else db_path()
    lockpath = str(target) + ".writelock"
    if not os.path.exists(lockpath):
        return None
    holder = _read_lock(lockpath)
    max_age = _env_float("AH_WRITE_LOCK_MAX_AGE", _DEFAULT_WRITE_LOCK_MAX_AGE)
    if _lock_is_stale(lockpath, holder, max_age):
        return None
    return holder or {"pid": None, "script": "?", "started_at": "?"}


def assert_write_lock_free(path: str | Path | None = None) -> None:
    """Raise :class:`DBWriterBusy` if a live writer still holds ``<db>.writelock``.

    The assertion form of :func:`write_lock_holder`, for use right after a stage is
    cancelled: "no collisions" is a testable claim, and this is the test.
    """
    holder = write_lock_holder(path)
    if holder is not None:
        target = Path(path) if path is not None else db_path()
        raise DBWriterBusy(_holder_msg(str(target) + ".writelock", holder))


def integrity_ok(con: sqlite3.Connection) -> bool:
    """Return True iff ``PRAGMA quick_check`` reports 'ok'.

    Cheap structural check (much faster than ``integrity_check``). Use this as a
    gate before snapshotting a backup so a corrupt DB never overwrites a good
    backup, and after a restore to confirm health.
    """
    try:
        rows = con.execute("PRAGMA quick_check(1)").fetchall()
    except sqlite3.DatabaseError:
        return False
    return len(rows) == 1 and rows[0][0] == "ok"
