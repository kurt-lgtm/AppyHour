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

import sqlite3
from pathlib import Path

from .paths import db_path

__all__ = ["connect", "connect_ro", "integrity_ok", "BUSY_TIMEOUT_MS"]

BUSY_TIMEOUT_MS = 10_000
_WAL_AUTOCHECKPOINT_PAGES = 1000


def _apply_writer_pragmas(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute(f"PRAGMA wal_autocheckpoint={_WAL_AUTOCHECKPOINT_PAGES}")


def connect(path: str | Path | None = None, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a READ/WRITE connection to shipping.db with safe-concurrency pragmas.

    Use this for any code that INSERTs/UPDATEs/DELETEs. The ``busy_timeout``
    pragma is the critical bit - it makes a blocked writer wait instead of
    racing another process's checkpoint.

    Args:
        path: DB path. Defaults to the canonical :func:`paths.db_path`.
        timeout: Python-side connect timeout (seconds). Distinct from the
            SQLite-internal ``busy_timeout`` pragma; both matter.
    """
    target = Path(path) if path is not None else db_path()
    con = sqlite3.connect(str(target), timeout=timeout)
    _apply_writer_pragmas(con)
    return con


def connect_ro(path: str | Path | None = None, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a READ-ONLY connection (``mode=ro``).

    Use this for every reader (reports, analytics, the shipping-data skill,
    forecasting series pulls). A read-only connection cannot take a write lock
    or trigger a checkpoint, so it can never participate in the write race that
    caused the 2026-06-27 corruption. It still applies the WAL, so it sees all
    committed writes.
    """
    target = Path(path) if path is not None else db_path()
    uri = "file:" + target.as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=timeout)
    # busy_timeout still helps a reader wait out a checkpoint instead of erroring.
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return con


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
