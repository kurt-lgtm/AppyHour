"""Cooperative stage cancellation — the abandoned-writer fix (2026-08-31).

🔴 WHAT THIS PROVES, and why "the thread stopped" is not the claim that matters:
``sync_logon._run_stage`` used to stamp ``fail:Timeout`` and move on while the daemon
thread kept writing (~11,900 upserts to ~12:22) holding ``<db>.writelock`` forever. The
acceptance test for "no collisions" is therefore three assertions, not one:
  1. the stage STOPS, at a COMMITTED boundary (row count lands on a chunk multiple),
  2. it leaves NO live ``<db>.writelock`` behind,
  3. a SECOND WRITER PROCESS then succeeds immediately, where against the old long-hold
     stage it is refused.
Plus the still-alive path: a stage that ignores its token must raise a NAMED alarm, never
a quieter abandonment.

🔴 Every connection here targets a pytest ``tmp_path`` scratch DB. The live
``C:\\AppyHourData\\shipping.db`` is never opened, read-only or otherwise.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from appyhour_lib import db as dbmod
from appyhour_lib.cancel import CancelToken, StageCancelled, checkpoint

sync_logon = pytest.importorskip("sync_logon")


CHUNK = 100          # rows per committed unit in the fake stage
LOCK_FILE = ".writelock"


@pytest.fixture(autouse=True)
def _reset_lock_state():
    dbmod._refcounts.clear()
    yield
    dbmod._refcounts.clear()


@pytest.fixture
def scratch_db(tmp_path):
    p = tmp_path / "shipping.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE rows_written (i INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    return p


@pytest.fixture
def stamps(monkeypatch):
    """Capture heartbeat stamps + notifications instead of writing the real ledger."""
    seen: dict[str, list] = {"stamp": [], "notify": []}
    monkeypatch.setattr(sync_logon, "_stamp",
                        lambda name, status: seen["stamp"].append((name, status)))
    monkeypatch.setattr(sync_logon, "_notify",
                        lambda msg, level="info": seen["notify"].append((level, msg)))
    return seen


def _rowcount(db) -> int:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM rows_written").fetchone()[0]
    finally:
        con.close()


def _second_writer(db, *, lock_wait: float = 0.0, timeout: float = 60.0):
    """A genuinely SEPARATE process that tries to take the write lock and insert.

    In-process asserts cannot prove this: ``connect()``'s refcount makes a nested call in
    the same process reentrant, so only another process exercises the lock that actually
    serialises writers. Returns (ok, elapsed_seconds, output).
    """
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(dbmod.__file__).rsplit("appyhour_lib", 1)[0]!r})
        from appyhour_lib.db import connect, DBWriterBusy
        t0 = time.monotonic()
        try:
            c = connect({str(db)!r}, lock_wait={lock_wait!r})
        except DBWriterBusy as e:
            print(f"REFUSED after {{time.monotonic()-t0:.2f}}s: {{e}}")
            sys.exit(9)
        try:
            c.execute("INSERT INTO rows_written (i) VALUES (999999)")
            c.commit()
        finally:
            c.close()
        print(f"OK after {{time.monotonic()-t0:.2f}}s")
    """)
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode == 0, time.monotonic() - t0, (proc.stdout + proc.stderr).strip()


# ── the primitive ────────────────────────────────────────────────────────────

def test_checkpoint_with_no_token_is_a_noop():
    """A caller with no watchdog (pipeline_run, the CLI mains) must run to completion."""
    checkpoint(None, "nowhere")          # must not raise


def test_check_raises_only_after_cancel():
    tok = CancelToken("demo")
    tok.check("before")                  # not set → no-op
    tok.cancel("ceiling hit")
    with pytest.raises(StageCancelled) as e:
        tok.check("chunk 3/9")
    assert "chunk 3/9" in str(e.value) and "ceiling hit" in str(e.value)
    assert e.value.stage == "demo"


def test_write_lock_holder_sees_a_live_holder_and_clears_on_close(scratch_db):
    assert dbmod.write_lock_holder(scratch_db) is None
    con = dbmod.connect(scratch_db)
    try:
        holder = dbmod.write_lock_holder(scratch_db)
        assert holder is not None and holder["pid"] == __import__("os").getpid()
        with pytest.raises(dbmod.DBWriterBusy):
            dbmod.assert_write_lock_free(scratch_db)
    finally:
        con.close()
    assert dbmod.write_lock_holder(scratch_db) is None
    dbmod.assert_write_lock_free(scratch_db)      # must not raise


# ── the reproduction: BEFORE vs AFTER ────────────────────────────────────────

def test_before_long_hold_stage_starves_a_second_writer(scratch_db):
    """BEFORE: one connection held across the stage — the shape run_fulfillments had.

    This is the collision, reproduced: the second writer is refused while the stage runs.
    """
    stop = threading.Event()
    started = threading.Event()

    def legacy_stage():
        con = dbmod.connect(scratch_db)          # 🔴 held for the whole "stage"
        try:
            started.set()
            i = 0
            while not stop.is_set():
                con.execute("INSERT INTO rows_written (i) VALUES (?)", (i,))
                con.commit()
                i += 1
                time.sleep(0.005)
        finally:
            con.close()

    t = threading.Thread(target=legacy_stage, daemon=True)
    t.start()
    assert started.wait(5)
    try:
        ok, elapsed, out = _second_writer(scratch_db, lock_wait=3.0)
        assert not ok, f"second writer should have been refused, got: {out}"
        assert "REFUSED" in out
        assert elapsed >= 2.5, f"it should have WAITED for the lock, took {elapsed:.2f}s"
    finally:
        stop.set()
        t.join(10)


def test_after_cancelled_stage_stops_on_a_boundary_and_frees_the_lock(scratch_db, stamps):
    """AFTER: per-checkpoint lock + cancel token, driven by the real ``_run_stage``.

    Asserts all three parts of "no collisions": stopped, boundary-clean, lock free — and
    then that a second writer PROCESS succeeds immediately where the test above was refused.
    """
    chunks_done = []

    def stage(token):
        """Mirrors the fixed shape: open → write a batch → commit → CLOSE → then check."""
        for ci in range(1, 1000):
            con = dbmod.connect(scratch_db)
            try:
                con.executemany("INSERT INTO rows_written (i) VALUES (?)",
                                [(ci * CHUNK + k,) for k in range(CHUNK)])
                con.commit()
            finally:
                con.close()               # 🔴 lock released BEFORE the checkpoint
            chunks_done.append(ci)
            token.check(f"chunk {ci}")
            time.sleep(0.02)

    sync_logon._run_stage("fake_fulfillments", stage, timeout_s=1)

    # 1. it stopped
    assert not any(t.name == "stage-fake_fulfillments" and t.is_alive()
                   for t in threading.enumerate())
    # 2. on a committed boundary — never mid-chunk
    rows = _rowcount(scratch_db)
    assert rows == len(chunks_done) * CHUNK, "rows landed mid-chunk — partial write!"
    assert rows > 0, "the stage never got going; the test proves nothing"
    # 3. no lock left behind
    assert dbmod.write_lock_holder(scratch_db) is None
    dbmod.assert_write_lock_free(scratch_db)

    # …and the writer that used to starve now succeeds immediately.
    ok, elapsed, out = _second_writer(scratch_db, lock_wait=0.0)
    assert ok, f"second writer still blocked after cancel: {out}"
    assert elapsed < 20, f"second writer took {elapsed:.2f}s"

    status = dict(stamps["stamp"])["fake_fulfillments"]
    assert "cancelled-clean" in status, status
    assert any(lvl == "error" and "CANCELLED at a committed boundary" in m
               for lvl, m in stamps["notify"]), stamps["notify"]
    assert any("lock-proof OK" in m for _lvl, m in stamps["notify"]), stamps["notify"]


def test_still_alive_stage_raises_a_named_alarm(monkeypatch, stamps):
    """The stage ignores its token → HARD ERROR with a named alarm, never a quiet move-on."""
    monkeypatch.setattr(sync_logon, "STAGE_GRACE_S", 1)
    ran_to_completion = threading.Event()

    def deaf_stage(token):                      # never calls token.check
        time.sleep(4)
        ran_to_completion.set()

    with pytest.raises(sync_logon.ZombieStageError) as e:
        sync_logon._run_stage("deaf", deaf_stage, timeout_s=1)

    assert "ZOMBIE STAGE" in str(e.value)
    assert dict(stamps["stamp"])["deaf"].startswith("fail:TimeoutUncancellable")
    assert any(lvl == "critical" and "ZOMBIE STAGE" in m for lvl, m in stamps["notify"]), \
        "an uncancellable stage must raise a CRITICAL named alarm"
    ran_to_completion.wait(10)                  # let the daemon thread finish before teardown


def test_main_aborts_the_run_on_a_zombie_stage(monkeypatch, tmp_path, stamps):
    """Process exit is the ONLY thing that stops a deaf daemon thread, so main() must not
    continue into the remaining stages alongside an untracked writer."""
    calls = []
    monkeypatch.setattr(sync_logon, "_bootstrap_init", lambda: None)
    monkeypatch.setattr(sync_logon, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sync_logon, "APP_DIR", tmp_path)
    monkeypatch.setattr(sync_logon, "STAGE_GRACE_S", 1)

    def fake_run_stage(name, fn, timeout_s=None):
        calls.append(name)
        if name == "fulfillments":
            raise sync_logon.ZombieStageError("🔴 ZOMBIE STAGE: fulfillments")
    monkeypatch.setattr(sync_logon, "_run_stage", fake_run_stage)

    rc = sync_logon.main()
    assert rc == 3
    assert calls == ["carriers", "fulfillments"], \
        f"stages after the zombie must NOT run, got {calls}"


# ── the real loop, not a mock of it ──────────────────────────────────────────

class _StubPP:
    """Minimal ParcelPanel client: one shipment per order, no network."""

    def __init__(self):
        self.polled = 0

    def get_order_tracking(self, order_number, stats=None):
        self.polled += 1
        return {"order": {"shipments": [{"tracking_number": f"TRK{order_number}"}]}}

    def _normalize_parcel(self, ship, order_num):
        return {"tracking_number": ship["tracking_number"], "carrier": "FedEx",
                "delivery_status": "in_transit", "order_number": order_num}


def test_sync_parcel_panel_cancels_on_a_flush_boundary(tmp_path, monkeypatch):
    """The actual ``backfill_sync.sync_parcel_panel`` loop — the one that kept upserting.

    Cancel is set immediately; the loop must commit the first batch, release the lock, and
    stop AT that batch boundary — never mid-batch, never past it.
    """
    backfill_sync = pytest.importorskip("backfill_sync")
    sidb = pytest.importorskip("shipping_invoice_db")

    monkeypatch.setattr(backfill_sync, "PP_FLUSH_EVERY", 10)
    db_file = tmp_path / "shipping.db"
    con = sidb.init_db(str(tmp_path))
    con.executemany(
        "INSERT INTO fulfillments (order_number, tracking_number, fulfilled_at) VALUES (?,?,?)",
        [(str(1000 + i), f"T{i}", "2026-08-30") for i in range(40)])
    con.commit()
    con.close()

    token = CancelToken("fulfillments")
    token.cancel("test")                       # already cancelled before the first flush
    opened = []

    def _open():
        c = dbmod.connect(db_file)
        opened.append(c)
        return c

    with pytest.raises(StageCancelled) as e:
        backfill_sync.sync_parcel_panel(None, _StubPP(), since_days=None,
                                        token=token, open_conn=_open)

    assert "parcel_panel after order 10/40" in str(e.value)
    ro = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)
    try:
        written = ro.execute("SELECT COUNT(*) FROM delivery_status").fetchone()[0]
    finally:
        ro.close()
    assert written == 10, f"cancel landed off a flush boundary: {written} rows"
    assert dbmod.write_lock_holder(db_file) is None, "the lock survived the cancel"
