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


# rule 18 amendment 2026-09-11: a budgeted partial that COMMITTED ROWS is aliveâ”€â”€â”€â”€â”€
# A budgeted poller exits `partial:` on every normal run by design, so an `ok`-only reference
# made its escalation unsatisfiable (delivery_poll, 53h, queue draining normally, 2026-09-10).
# The floor: only a partial with progress > 0 clears it; zero progress must still page.

def _ago(hours_ago: float) -> str:
    return (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def test_progressing_partial_writes_last_progress(hb):
    sync_logon._stamp("delivery_poll", "partial:elapsed-budget:756 delivery rows committed", 756)
    h = json.loads(hb.read_text(encoding="utf-8"))
    assert h["delivery_poll_last_progress"], "committed rows left no progress stamp"
    assert "delivery_poll" not in h, "partial: must NOT advance last-success (12h throttle)"
    assert sync_logon._should_run("delivery_poll")


def test_zero_progress_partial_writes_no_last_progress_and_still_escalates(hb):
    """THE FLOOR. A partial that banked nothing is an outage, not a backlog."""
    sync_heartbeat.write({"delivery_poll_partial_since": _ago(53)})
    sync_logon._stamp("delivery_poll", "partial:elapsed-budget:0 delivery rows committed", 0)
    h = json.loads(hb.read_text(encoding="utf-8"))
    assert "delivery_poll_last_progress" not in h, "zero committed rows must not look like progress"
    findings: list[str] = []
    ah._grade_partial_legs(h, findings)
    assert findings and "PARTIAL with no ok" in findings[0], "zero-progress partial went silent"


def test_progressing_partial_does_not_escalate_past_36h(hb):
    """The 2026-09-10 measurement: 53h of by-design partials, queue draining, must be info."""
    sync_heartbeat.write({"delivery_poll_partial_since": _ago(53)})
    sync_logon._stamp("delivery_poll", "partial:elapsed-budget:756 delivery rows committed; "
                      "196 due remaining of 2576 unresolved; oldest due 3d; retired 0", 756)
    h = json.loads(hb.read_text(encoding="utf-8"))
    findings: list[str] = []
    ah._grade_partial_legs(h, findings)
    assert findings == [], f"healthy draining leg still paged: {findings}"
    ref, basis = ah._partial_reference(h, "delivery_poll")
    assert basis == "last committed rows"


def test_stale_progress_re_escalates(hb):
    """Progress is not a free pass: rows committed 53h ago and nothing since still pages."""
    h = {"delivery_poll_status": "partial:elapsed-budget:1 delivery rows committed",
         "delivery_poll_partial_since": _ago(90), "delivery_poll_last_progress": _ago(53)}
    findings: list[str] = []
    ah._grade_partial_legs(h, findings)
    assert findings and "measured from last committed rows" in findings[0]


def test_ok_wins_when_newer_than_progress(hb):
    h = {"delivery_poll_status": "partial:elapsed-budget:5 delivery rows committed",
         "delivery_poll": _ago(2), "delivery_poll_last_progress": _ago(40)}
    ref, basis = ah._partial_reference(h, "delivery_poll")
    assert basis == "last ok"


def test_ok_clears_last_progress_and_partial_since(hb):
    sync_heartbeat.write({"delivery_poll_partial_since": _ago(53),
                          "delivery_poll_last_progress": _ago(1)})
    sync_logon._stamp("delivery_poll", "ok:12 delivery rows; 0 due remaining", 12)
    h = json.loads(hb.read_text(encoding="utf-8"))
    assert "delivery_poll_last_progress" not in h
    assert "delivery_poll_partial_since" not in h
    assert h["delivery_poll"]


def test_last_progress_is_refreshed_unlike_partial_since(hb):
    sync_heartbeat.write({"delivery_poll_partial_since": _ago(53),
                          "delivery_poll_last_progress": _ago(53)})
    sync_logon._stamp("delivery_poll", "partial:elapsed-budget:756 rows committed", 756)
    h = json.loads(hb.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(h["delivery_poll_partial_since"]) < datetime.now() - timedelta(hours=50)
    assert datetime.fromisoformat(h["delivery_poll_last_progress"]) > datetime.now() - timedelta(minutes=5)


def test_last_progress_does_not_hold_the_cross_leg_48h_gate_green(hb):
    """One draining backlog must not mask every other frozen leg."""
    sync_heartbeat.write({"carriers": _ago(200), "delivery_poll_last_progress": _ago(0.1),
                          "delivery_poll_status": "partial:elapsed-budget:756 rows committed"})
    findings: list[str] = []
    ah.check_sync_heartbeat(findings)
    assert any("ingest sync heartbeat stale" in f for f in findings), findings


def test_delivery_poll_budget_exit_is_partial_with_a_committed_count():
    res = {"written": 756, "complete": False, "stop_reason": "elapsed-budget",
           "remaining": 196, "unresolved": 2576, "oldest_due_days": 3, "retired": 0}
    s = sync_logon._delivery_poll_stamp(res)
    assert s.startswith("partial:elapsed-budget:756 delivery rows committed")
    assert "oldest due 3d" in s

