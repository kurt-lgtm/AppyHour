"""Single-writer advisory lock — appyhour_lib.db (2026-07-02).

🔴 These tests NEVER touch the live shipping.db. Every connection targets a
throwaway temp DB (pytest tmp_path); the .writelock lands beside it. The live
2-terminal collision test is a manual step for Kurt, not automated here.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from appyhour_lib import db as dbmod
from appyhour_lib.db import DBWriterBusy, connect, connect_ro


@pytest.fixture(autouse=True)
def _reset_lock_state():
    """Clear the module-global refcount so one test can't leak a held lock into the next."""
    dbmod._refcounts.clear()
    yield
    dbmod._refcounts.clear()


def _lockpath(db_file) -> str:
    return str(db_file) + ".writelock"


def _write_fake_lock(db_file, *, pid: int, create_time, started_epoch: float) -> None:
    """Simulate ANOTHER process's held lock (bypasses connect()'s refcount)."""
    payload = {
        "pid": pid, "create_time": create_time, "script": "fake_holder.py",
        "started_at": "2026-07-02T00:00:00", "started_at_epoch": started_epoch,
        "host": "test",
    }
    with open(_lockpath(db_file), "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _dead_pid() -> int:
    """A PID guaranteed not alive: spawn a child, then reap it."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# 1 — acquire → write → release
def test_acquire_write_release(tmp_path):
    dbf = tmp_path / "t.db"
    con = connect(path=dbf)
    assert (tmp_path / "t.db.writelock").exists()
    con.execute("CREATE TABLE t(x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    assert not (tmp_path / "t.db.writelock").exists()  # released on close


# 2 — second writer while a LIVE holder holds it → DBWriterBusy naming the holder
def test_second_writer_raises(tmp_path):
    dbf = tmp_path / "t.db"
    _write_fake_lock(dbf, pid=__import__("os").getpid(),
                     create_time=dbmod._self_create_time(), started_epoch=time.time())
    with pytest.raises(DBWriterBusy) as ei:
        connect(path=dbf, lock_wait=0)
    assert "fake_holder.py" in str(ei.value)


# 3 — dead-PID stale lock → auto-breaks + acquires
def test_dead_pid_stale_break(tmp_path):
    dbf = tmp_path / "t.db"
    _write_fake_lock(dbf, pid=_dead_pid(), create_time=None, started_epoch=time.time())
    con = connect(path=dbf, lock_wait=0)  # must NOT raise — stale lock broken
    try:
        assert (tmp_path / "t.db.writelock").exists()  # now ours
    finally:
        con.close()


# 4 — reused PID (alive pid, mismatched create_time) → treated stale → breaks
@pytest.mark.skipif(dbmod._self_create_time() is None,
                    reason="create_time only available on Windows")
def test_reused_pid_break(tmp_path):
    import os
    dbf = tmp_path / "t.db"
    wrong_ct = (dbmod._self_create_time() or 0) + 12345  # a live pid but different process identity
    _write_fake_lock(dbf, pid=os.getpid(), create_time=wrong_ct, started_epoch=time.time())
    con = connect(path=dbf, lock_wait=0)  # must NOT raise — reused-PID detected → broken
    try:
        assert (tmp_path / "t.db.writelock").exists()
    finally:
        con.close()


# 5 — reentrant nested connect in ONE process shares the lock; released at last close
def test_reentrant_nested(tmp_path):
    dbf = tmp_path / "t.db"
    c1 = connect(path=dbf)          # acquire (refcount 1)
    c2 = connect(path=dbf)          # re-enter (refcount 2) — must NOT raise
    assert dbmod._refcounts[_lockpath(dbf)] == 2
    c1.close()                      # refcount 1 — lock still held
    assert (tmp_path / "t.db.writelock").exists()
    c2.close()                      # refcount 0 — released
    assert not (tmp_path / "t.db.writelock").exists()


# 6 — connect_ro NEVER blocks, even while a writer holds the lock
def test_reader_never_blocks(tmp_path):
    import os
    dbf = tmp_path / "t.db"
    seed = connect(path=dbf); seed.execute("CREATE TABLE t(x)"); seed.execute("INSERT INTO t VALUES (7)"); seed.commit(); seed.close()
    _write_fake_lock(dbf, pid=os.getpid(),
                     create_time=dbmod._self_create_time(), started_epoch=time.time())
    ro = connect_ro(path=dbf)       # must succeed instantly despite held write lock
    try:
        assert ro.execute("SELECT x FROM t").fetchone()[0] == 7
    finally:
        ro.close()


# 7 — SIGKILL backstop: an over-MAX_AGE lock (alive pid, matching ct) is broken
def test_max_age_backstop(tmp_path):
    import os
    dbf = tmp_path / "t.db"
    _write_fake_lock(dbf, pid=os.getpid(), create_time=dbmod._self_create_time(),
                     started_epoch=time.time() - 100_000)  # older than default 1800s
    con = connect(path=dbf, lock_wait=0)  # must NOT raise — MAX_AGE broke it
    try:
        assert (tmp_path / "t.db.writelock").exists()
    finally:
        con.close()


# 8 — AH_WRITE_LOCK_DISABLE=1 fully bypasses the lock
def test_disable_escape_hatch(tmp_path, monkeypatch):
    monkeypatch.setenv("AH_WRITE_LOCK_DISABLE", "1")
    dbf = tmp_path / "t.db"
    _write_fake_lock(dbf, pid=__import__("os").getpid(),
                     create_time=dbmod._self_create_time(), started_epoch=time.time())
    con = connect(path=dbf, lock_wait=0)  # would raise if lock honored; disabled → passes
    try:
        con.execute("CREATE TABLE t(x)"); con.commit()
    finally:
        con.close()
