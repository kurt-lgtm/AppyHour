"""HEARTBEAT_RULES rule 18 — three-way ingest stamp (ok / partial: / fail:) + single-instance guard.

Every test here is offline and touches NO live state: `sync_heartbeat.CANONICAL` is redirected
to `tmp_path`, the lock file is a `tmp_path` file, and no stage function is ever called (they
write the canonical shipping.db). Liveness probes target this process and a subprocess we own.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from appyhour_lib import sync_heartbeat
from appyhour_lib.cancel import CancelToken, note_progress
from appyhour_lib.db import pid_alive

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import automation_health as ah  # noqa: E402

sync_logon = pytest.importorskip("sync_logon")


# ── token progress → stamp class (pure) ──────────────────────────────────────

def test_zero_progress_is_still_fail():
    assert sync_logon._cancelled_stamp(600, 0) == "fail:Timeout:600s:cancelled-clean"


def test_banked_progress_is_partial():
    assert (sync_logon._cancelled_stamp(600, 263)
            == "partial:Timeout:600s:263 rows committed; remainder re-selected next run")


def test_token_counts_only_positive_commits_and_tolerates_none():
    t = CancelToken("fulfillments")
    assert t.progress == 0
    t.note_progress(200)
    t.note_progress(0)
    note_progress(t, 63)
    note_progress(None, 999)          # no watchdog → nothing to count into, no error
    assert t.progress == 263


# ── _stamp: partial never advances last-success; ok does ────────────────────

@pytest.fixture
def hb(tmp_path, monkeypatch):
    canon = tmp_path / "sync_heartbeat.json"
    monkeypatch.setattr(sync_heartbeat, "CANONICAL", canon)
    monkeypatch.setattr(sync_heartbeat, "LEGACY", tmp_path / "no_legacy.json")
    return canon


def test_partial_stamp_leaves_last_success_untouched(hb):
    old = "2026-09-01T09:00:00"
    sync_heartbeat.write({"fulfillments": old})
    sync_logon._stamp("fulfillments", sync_logon._cancelled_stamp(600, 263))
    h = json.loads(hb.read_text(encoding="utf-8"))
    assert h["fulfillments"] == old, "partial: advanced last-success — next logon would sleep 12h"
    assert h["fulfillments_status"].startswith("partial:Timeout:600s:263 rows")
    assert h["fulfillments_last_attempt"] > old
    assert sync_logon._should_run("fulfillments"), "throttle must let the next run drain the remainder"


def test_ok_stamp_advances_last_success(hb):
    old = "2026-09-01T09:00:00"
    sync_heartbeat.write({"fulfillments": old, "fulfillments_status": "partial:Timeout:600s:1 rows"})
    sync_logon._stamp("fulfillments", "ok")
    h = json.loads(hb.read_text(encoding="utf-8"))
    assert h["fulfillments"] > old
    assert h["fulfillments_status"] == "ok"


def test_merge_carries_newer_partial_over_older_ok():
    # sync_heartbeat.merge needs no special case: partial: writes _last_attempt, which stamp_time uses
    base = {"fulfillments": "2026-09-01T09:00:00", "fulfillments_status": "ok"}
    other = {"fulfillments": "2026-09-01T09:00:00", "fulfillments_last_attempt": "2026-09-03T09:20:00",
             "fulfillments_status": "partial:Timeout:600s:263 rows committed; remainder re-selected next run"}
    assert sync_heartbeat.merge(base, other)["fulfillments_status"].startswith("partial:")


# ── health reader: partial recent → info; partial > 36h with no ok → CRITICAL ─

def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def test_partial_recent_is_not_a_finding(capsys):
    now = datetime(2026, 9, 3, 12, 0, 0)
    data = {"fulfillments": _iso(now - timedelta(hours=27)),
            "fulfillments_last_attempt": _iso(now - timedelta(hours=1)),
            "fulfillments_status": "partial:Timeout:600s:263 rows committed; remainder re-selected next run"}
    findings: list[str] = []
    ah._grade_partial_legs(data, findings, now=now)
    assert findings == []
    assert "info: fulfillments_status" in capsys.readouterr().out


def test_partial_stale_36h_is_critical():
    now = datetime(2026, 9, 3, 12, 0, 0)
    data = {"fulfillments": _iso(now - timedelta(hours=37)),
            "fulfillments_last_attempt": _iso(now - timedelta(hours=1)),   # fresh attempt must NOT save it
            "fulfillments_status": "partial:Timeout:600s:263 rows committed; remainder re-selected next run"}
    findings: list[str] = []
    ah._grade_partial_legs(data, findings, now=now)
    assert len(findings) == 1 and findings[0].startswith("ingest leg fulfillments PARTIAL with no ok for 37h")
    assert ah.finding_key(findings[0]) == "ingest-partial-fulfillments"
    assert ah.finding_key(findings[0]) == ah.finding_key(findings[0].replace("37h", "61h"))


def test_check_sync_heartbeat_does_not_list_partial_as_not_ok(monkeypatch):
    now = datetime.now()
    data = {"fulfillments": _iso(now - timedelta(hours=2)),
            "fulfillments_status": "partial:Timeout:600s:5 rows committed; remainder re-selected next run",
            "carriers": _iso(now - timedelta(hours=2)), "carriers_status": "fail:Timeout:1800s:cancelled-clean"}
    monkeypatch.setattr(ah, "read_sync_heartbeat", lambda: data)
    findings: list[str] = []
    ah.check_sync_heartbeat(findings)
    assert findings == ["ingest legs not ok: carriers_status"]


# ── single-instance lock (tmp_path only) ─────────────────────────────────────

def _exited_pid() -> int:
    """PID of a child we spawned and fully reaped — provably dead."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    assert child.wait(timeout=30) == 0
    return child.pid


def test_pid_alive_true_for_self_false_for_exited_child():
    assert pid_alive(os.getpid())
    assert not pid_alive(_exited_pid())
    assert pid_alive(os.getpid()), "probe must not have harmed the prober (os.kill(pid,0) on Windows kills)"


def test_lock_fresh_acquired_and_released(tmp_path):
    lock = tmp_path / "sync_logon.lock"
    sync_logon._acquire_lock(lock)
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    sync_logon._release_lock(lock)
    assert not lock.exists()


def test_lock_live_pid_refuses(tmp_path):
    lock = tmp_path / "sync_logon.lock"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock.write_text(json.dumps({"pid": proc.pid, "started_at": "2026-09-03T09:20:00"}), encoding="utf-8")
        with pytest.raises(sync_logon.AlreadyRunning, match=f"pid {proc.pid} since 2026-09-03T09:20:00"):
            sync_logon._acquire_lock(lock)
        assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == proc.pid, "refusal must not touch the lock"
    finally:
        proc.kill()
        proc.wait()


def test_lock_dead_pid_taken_over(tmp_path, capsys):
    lock = tmp_path / "sync_logon.lock"
    lock.write_text(json.dumps({"pid": _exited_pid(), "started_at": "2026-09-03T09:20:00"}), encoding="utf-8")
    sync_logon._acquire_lock(lock)
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert "stale" in capsys.readouterr().out
    sync_logon._release_lock(lock)


def test_release_never_deletes_a_successors_lock(tmp_path):
    lock = tmp_path / "sync_logon.lock"
    lock.write_text(json.dumps({"pid": 4242, "started_at": "x"}), encoding="utf-8")
    sync_logon._release_lock(lock)
    assert lock.exists()
