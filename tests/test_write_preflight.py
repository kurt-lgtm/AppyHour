"""Tests for appyhour_lib.write_preflight — the pre-flight writer-collision check.

🔴 NEVER points at the live C:\\AppyHourData\\shipping.db. Every test builds its own
scratch DB under ``tmp_path`` (which ``paths.assert_canonical_db`` allows by name-and-tempdir
rule) and its own scratch coordination/git fixtures. No writer's ``main()`` is ever invoked —
several of them post to Slack, hit ParcelPanel, or edit live orders.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appyhour_lib import write_preflight as wp  # noqa: E402

# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def scratch_db(tmp_path: Path) -> Path:
    """A real sqlite file that is NOT named shipping.db (so no canonical guard fires)."""
    p = tmp_path / "scratch_ship.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE t (a INTEGER)")
    con.commit()
    con.close()
    return p


@pytest.fixture
def quiet_realgit(tmp_path: Path, monkeypatch, scratch_db: Path):
    """Neutralise every axis EXCEPT the pending/git one, which stays real."""
    monkeypatch.setattr(wp, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(wp, "BUSY_STATE", tmp_path / "busy-state.jsonl")
    monkeypatch.setattr(wp, "ACTIVE_CLAIMS", tmp_path / "ACTIVE-CLAIMS.md")
    monkeypatch.setattr(wp, "_live_bypassing_writers", lambda: [])
    monkeypatch.setattr(wp, "_imminent_tasks", lambda minutes: [])
    return scratch_db


@pytest.fixture
def quiet(quiet_realgit, monkeypatch):
    """Neutralise every axis except the one under test, so a failure names its own cause."""
    monkeypatch.setattr(wp, "_pending_edits", lambda files: ([], []))
    return quiet_realgit


# ── axis 1: SQLite-level (the only check that observes a REAL writer) ─────────

def test_begin_immediate_probe_detects_a_real_live_writer(quiet, scratch_db):
    """🔴 The load-bearing check. An advertised-writer check can be lied to; this one cannot."""
    holder = sqlite3.connect(str(scratch_db), timeout=1.0)
    holder.execute("BEGIN IMMEDIATE")            # a genuine exclusive writer, right now
    try:
        with pytest.raises(wp.WriterCollision) as e:
            wp.assert_no_conflicting_writer("test-surface", db_path=scratch_db, source_files=[])
        assert "BEGIN IMMEDIATE" in str(e.value)
    finally:
        holder.rollback()
        holder.close()


def test_clear_when_nothing_holds_the_db(quiet, scratch_db):
    rep = wp.assert_no_conflicting_writer("test-surface", db_path=scratch_db, source_files=[])
    assert rep.clear is True
    assert rep.collisions == []


def test_hot_wal_is_reported_not_fatal(quiet, scratch_db, tmp_path):
    """A large -wal is evidence of an in-flight/abandoned writer — surfaced as a WARNING.
    NEGATIVE: it must NOT hard-refuse; a big WAL is normal right after a bulk load."""
    (tmp_path / "scratch_ship.db-wal").write_bytes(b"\x00" * (wp.HOT_WAL_BYTES + 1))
    rep = wp.assert_no_conflicting_writer("test-surface", db_path=scratch_db, source_files=[])
    assert rep.clear is True
    assert any("wal" in w.lower() for w in rep.warnings)


# ── axis 2: surface-keyed lock ────────────────────────────────────────────────

def test_second_holder_of_the_same_surface_is_refused(quiet, scratch_db):
    with wp.writer_lock("recover-undelivered", db_path=scratch_db, source_files=[]), \
            pytest.raises(wp.WriterCollision) as e:
        wp.assert_no_conflicting_writer("recover-undelivered", db_path=scratch_db,
                                        source_files=[], _self_holds=False)
    assert "recover-undelivered" in str(e.value)


def test_a_different_surface_is_not_blocked(quiet, scratch_db):
    with wp.writer_lock("surface-a", db_path=scratch_db, source_files=[]):
        rep = wp.assert_no_conflicting_writer("surface-b", db_path=scratch_db, source_files=[])
        assert rep.clear is True


def test_lock_is_released_on_exception(quiet, scratch_db):
    with pytest.raises(ValueError), \
            wp.writer_lock("surface-x", db_path=scratch_db, source_files=[]):
        raise ValueError("boom")
    rep = wp.assert_no_conflicting_writer("surface-x", db_path=scratch_db, source_files=[])
    assert rep.clear is True


def test_stale_lock_with_a_dead_pid_is_broken_not_deadlocked(quiet, scratch_db, tmp_path):
    """🔴 A dead holder must NOT wall a writer forever."""
    lock = wp.LOCK_DIR / "surface-y.writer.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 999_999_999, "surface": "surface-y",
                                "session": "ghost", "started_at": "2020-01-01T00:00:00",
                                "started_at_epoch": time.time()}), encoding="utf-8")
    rep = wp.assert_no_conflicting_writer("surface-y", db_path=scratch_db, source_files=[])
    assert rep.clear is True
    assert any("stale" in w.lower() for w in rep.warnings)


def test_live_pid_lock_is_never_auto_stolen(quiet, scratch_db):
    """🔴 The counterpart of the test above: an ALIVE holder is never taken over."""
    lock = wp.LOCK_DIR / "surface-z.writer.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "surface": "surface-z",
                                "session": "someone-else", "started_at": "now",
                                "started_at_epoch": time.time()}), encoding="utf-8")
    with pytest.raises(wp.WriterCollision):
        wp.assert_no_conflicting_writer("surface-z", db_path=scratch_db, source_files=[],
                                        _self_holds=False)
    assert lock.exists(), "a live holder's lock must survive the refusal"


# ── axis 3: busy-state beats — UNKNOWN is NEVER clear ─────────────────────────

def _beat(path: Path, session: str, task: str, age_s: float, state: str = "working") -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"session": session, "gen": "g0", "seq": 1, "state": state,
                            "task": task, "ts": time.time() - age_s}) + "\n")


def test_fresh_beat_claiming_the_surface_collides(quiet, scratch_db):
    _beat(wp.BUSY_STATE, "UPSParserFix", "line-summing fix in shipping_invoice_db", age_s=30)
    with pytest.raises(wp.WriterCollision) as e:
        wp.assert_no_conflicting_writer("shipping_invoice_db", db_path=scratch_db,
                                        source_files=[])
    assert "UPSParserFix" in str(e.value)


def test_stale_beat_is_UNKNOWN_and_is_never_reported_as_clear(quiet, scratch_db):
    """🔴 Standing doctrine (COORDINATION_RECORDS_RULES gotcha 1): no beat / a stale beat
    classifies UNKNOWN — never idle, never 'clear'. It must surface as a WARNING."""
    _beat(wp.BUSY_STATE, "GhostSession", "shipping_invoice_db rework", age_s=999_999)
    rep = wp.assert_no_conflicting_writer("shipping_invoice_db", db_path=scratch_db,
                                          source_files=[])
    assert any("UNKNOWN" in w for w in rep.warnings), rep.warnings
    assert rep.unknown_sessions, "a stale beat must be recorded as UNKNOWN, not dropped"


def test_empty_busy_state_reports_unknown_not_clear(quiet, scratch_db):
    """Absence of beats is absence of evidence. The verdict may pass, but it must SAY so."""
    rep = wp.assert_no_conflicting_writer("anything", db_path=scratch_db, source_files=[])
    assert rep.coordination_verdict == "UNKNOWN"
    assert "UNKNOWN" in rep.summary()


def test_own_session_beat_does_not_collide_with_itself(quiet, scratch_db, monkeypatch):
    monkeypatch.setenv("AH_SESSION_NAME", "MySelf")
    _beat(wp.BUSY_STATE, "MySelf", "shipping_invoice_db", age_s=10)
    rep = wp.assert_no_conflicting_writer("shipping_invoice_db", db_path=scratch_db,
                                          source_files=[])
    assert rep.clear is True


# ── axis 4: PENDING writers — uncommitted work by another session ─────────────

def _git_repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=r, check=True, env=env)
    (r / "writer.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "writer.py"], cwd=r, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=r, check=True, env=env)
    return r


def test_uncommitted_edit_to_the_writers_own_source_is_a_pending_writer(quiet_realgit, tmp_path,
                                                                       scratch_db):
    """🔴 THE 2026-09-07 BURN. UPSParserFix had 93 uncommitted lines in
    shipping_invoice_db.py while a commit deleting those functions was being landed.
    Nothing warned either side. A dirty source file IS a writer — a pending one."""
    repo = _git_repo(tmp_path)
    target = repo / "writer.py"
    target.write_text("print(1)\nprint('another session was here')\n", encoding="utf-8")

    with pytest.raises(wp.WriterCollision) as e:
        wp.assert_no_conflicting_writer("invoice-ingest", db_path=scratch_db,
                                        source_files=[target])
    msg = str(e.value)
    assert "writer.py" in msg
    assert "uncommitted" in msg.lower() or "pending" in msg.lower()


def test_clean_source_file_is_not_a_pending_writer(quiet_realgit, tmp_path, scratch_db):
    repo = _git_repo(tmp_path)
    rep = wp.assert_no_conflicting_writer("invoice-ingest", db_path=scratch_db,
                                          source_files=[repo / "writer.py"])
    assert rep.clear is True


def test_untracked_file_outside_any_repo_does_not_crash(quiet_realgit, scratch_db, tmp_path):
    """A writer living in an unversioned dir (the workspace root is) must degrade to a
    WARNING, never an exception and never a false 'clean'."""
    loose = tmp_path / "loose_writer.py"
    loose.write_text("x=1\n", encoding="utf-8")
    rep = wp.assert_no_conflicting_writer("loose", db_path=scratch_db, source_files=[loose])
    assert rep.clear is True
    assert any("git" in w.lower() or "repo" in w.lower() for w in rep.warnings)


def test_active_claims_row_naming_the_surface_is_surfaced(quiet, scratch_db):
    wp.ACTIVE_CLAIMS.write_text(
        "| stream | surfaces |\n| upsparser | `shipping_invoice_db.py` line summing |\n",
        encoding="utf-8")
    rep = wp.assert_no_conflicting_writer("shipping_invoice_db", db_path=scratch_db,
                                          source_files=[])
    assert any("claim" in w.lower() for w in rep.warnings), rep.warnings


# ── axis 5: scheduled owners ──────────────────────────────────────────────────

def test_imminent_scheduled_writer_collides(quiet, scratch_db, monkeypatch):
    monkeypatch.setattr(wp, "_imminent_tasks",
                        lambda minutes: ["appyhour_sync_daily_noon fires 09:30"])
    with pytest.raises(wp.WriterCollision) as e:
        wp.assert_no_conflicting_writer("s", db_path=scratch_db, source_files=[])
    assert "appyhour_sync_daily_noon" in str(e.value)


def test_scheduled_owner_list_covers_the_real_sync_tasks():
    """🔴 Enumerated from schtasks 2026-09-07, not assumed. sync_logon's two schtasks were
    absent from db_write_gate.WRITER_TASKS — the single busiest writer on the box."""
    for name in ("appyhour_sync_daily_noon", "appyhour_sync_on_logon",
                 "AppyHour Carrier Invoice Sync", "GorgiasUpdate"):
        assert name in wp.WRITER_TASKS, f"{name} missing from WRITER_TASKS"


def test_process_enumeration_failure_refuses_rather_than_assuming_quiet(quiet, scratch_db,
                                                                       monkeypatch):
    """🔴 A probe that cannot run is not a clear result."""
    monkeypatch.setattr(wp, "_live_bypassing_writers",
                        lambda: ["(could not enumerate processes)"])
    with pytest.raises(wp.WriterCollision):
        wp.assert_no_conflicting_writer("s", db_path=scratch_db, source_files=[])


# ── the --force escape hatch ──────────────────────────────────────────────────

def test_bare_force_is_rejected(quiet, scratch_db):
    """🔴 'Do not add a bare --force.' Overriding requires NAMING the colliding writer."""
    _beat(wp.BUSY_STATE, "OtherSession", "surface-q work", age_s=10)
    with pytest.raises(wp.WriterCollision):
        wp.assert_no_conflicting_writer("surface-q", db_path=scratch_db, source_files=[],
                                        force_override=True)  # True is not a name


def test_force_naming_the_wrong_writer_is_rejected(quiet, scratch_db):
    _beat(wp.BUSY_STATE, "OtherSession", "surface-q work", age_s=10)
    with pytest.raises(wp.WriterCollision) as e:
        wp.assert_no_conflicting_writer("surface-q", db_path=scratch_db, source_files=[],
                                        force_override="SomebodyElse")
    assert "does not name" in str(e.value).lower() or "not match" in str(e.value).lower()


def test_force_naming_every_collider_is_allowed_and_is_loud(quiet, scratch_db, capsys):
    _beat(wp.BUSY_STATE, "OtherSession", "surface-q work", age_s=10)
    rep = wp.assert_no_conflicting_writer("surface-q", db_path=scratch_db, source_files=[],
                                          force_override="OtherSession")
    assert rep.forced is True
    out = capsys.readouterr().out
    assert "OVERRIDE" in out and "OtherSession" in out


def test_force_cannot_override_a_real_sqlite_write_lock(quiet, scratch_db):
    """🔴 The one thing --force must never buy: the empirical live-writer check. Overriding
    an advertised writer is a judgement call; overriding a held write lock is corruption."""
    holder = sqlite3.connect(str(scratch_db), timeout=1.0)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(wp.WriterCollision):
            wp.assert_no_conflicting_writer("s", db_path=scratch_db, source_files=[],
                                            force_override="anything")
    finally:
        holder.rollback()
        holder.close()


# ── report shape ──────────────────────────────────────────────────────────────

def test_report_names_which_writer_it_collided_with(quiet, scratch_db):
    _beat(wp.BUSY_STATE, "NamedWriter", "surface-r", age_s=5)
    with pytest.raises(wp.WriterCollision) as e:
        wp.assert_no_conflicting_writer("surface-r", db_path=scratch_db, source_files=[])
    assert "NamedWriter" in str(e.value)
    assert "surface-r" in str(e.value)


def test_refusal_raises_and_never_merely_warns(quiet, scratch_db):
    """The whole contract: this REFUSES. It does not return False for a caller to ignore."""
    assert issubclass(wp.WriterCollision, RuntimeError)
