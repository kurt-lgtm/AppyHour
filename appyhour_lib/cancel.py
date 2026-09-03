"""Cooperative cancellation for long-running stages that write ``shipping.db``.

🔴 WHY (2026-08-31 — the abandoned-writer defect, HEARTBEAT_RULES rule 14):
``sync_logon._run_stage`` ran each stage in a daemon thread and, at the wall-clock
ceiling, stamped ``fail:Timeout`` and moved on. **Python cannot kill a thread**, so the
"abandoned" stage kept writing — measured ~11,900 upserts continuing to ~12:22 — while
holding the advisory ``<db>.writelock`` its owner would never release. That untracked
writer killed ``daily_shipping_sync`` three runs running with ``database is locked``.
A timeout that only *moves on* is not a timeout; it is a second writer nobody is tracking.
Per-checkpoint locking made that collision survivable. This module is what removes it.

🔴 NEGATIVES — read every one before adding a ``check()`` call anywhere
--------------------------------------------------------------------
* **NEVER check inside a transaction.** Cancelling mid-transaction is worse than not
  cancelling: it abandons a partial write. Every ``check()`` belongs immediately AFTER a
  ``commit()`` (or before any write has begun), never between two writes of one logical unit.
* **NEVER check while holding a write connection you do not close.** The whole point is
  releasing the advisory lock. A stage that raises :class:`StageCancelled` while still
  holding ``db.connect()`` has swapped a silent orphan for a loud one — the lock is still
  held until the thread unwinds. Close the connection at the boundary, then check.
* **NEVER swallow :class:`StageCancelled`.** A ``except Exception`` in a stage body that
  stamps ``fail:`` and returns normally is fine (the stage did stop); an ``except`` that
  logs and *continues the loop* re-creates the exact bug. It must reach the stage boundary.
* **A token is NOT a kill.** Between two checkpoints the stage is uninterruptible, so the
  worst-case stop latency is the longest gap between checkpoints (one HTTP call + one
  chunk of writes here). If a loop has no reachable committed boundary, it does NOT get a
  token — say so and leave it alone. Forcing a flag into such a loop corrupts data;
  the correct fix for an uncancellable stage is to stop it from STARTING when it cannot
  finish, which is a different mechanism.
* **The caller must treat "still running after the grace window" as a HARD ERROR with a
  named alarm.** Replacing a silent abandonment with a quieter abandonment fixes nothing.

Contract
--------
``_run_stage`` (the watchdog) owns one :class:`CancelToken` per stage and passes it down.
Stage bodies pass it into their loops. Loops call ``token.check("where")`` at committed
boundaries only. On timeout the watchdog calls ``token.cancel(reason)``, joins with a
bounded grace window, and — only if the thread is *still* alive — raises the named alarm.
"""
from __future__ import annotations

import threading

__all__ = ["CancelToken", "StageCancelled", "checkpoint", "note_progress"]


class StageCancelled(RuntimeError):
    """Raised at a committed boundary once a stage's :class:`CancelToken` is set.

    It is a control-flow signal, not a failure of the work already done: everything
    committed before the boundary stands, and nothing after it was started.
    """

    def __init__(self, stage: str, where: str, reason: str = "") -> None:
        self.stage = stage
        self.where = where
        self.reason = reason
        detail = f" ({reason})" if reason else ""
        super().__init__(f"stage '{stage}' cancelled at committed boundary '{where}'{detail}")


class CancelToken:
    """One-way cooperative cancel flag for a single stage.

    Thread-safe by construction (a :class:`threading.Event`); the watchdog thread sets it,
    the stage thread reads it. Never reset — a cancelled stage stays cancelled for the run.
    """

    __slots__ = ("_event", "_progress", "_reason", "stage")

    def __init__(self, stage: str = "stage") -> None:
        self.stage = stage
        self._event = threading.Event()
        self._reason = ""
        self._progress = 0

    # ── progress (HEARTBEAT_RULES rule 18) ───────────────────────────────────
    def note_progress(self, n: int) -> None:
        """Record ``n`` rows COMMITTED. 🔴 Call ONLY after ``commit()`` succeeded (and after the
        writer connection closed) — never at a "nothing written" checkpoint, never for rows
        merely fetched. The watchdog splits a cancelled stage on this: ``progress > 0`` stamps
        ``partial:`` (banked, remainder re-selected next run); ``0`` stays ``fail:`` (a stall).
        A count that includes uncommitted rows claims durability the next run will not find.
        """
        if n > 0:
            self._progress += int(n)

    @property
    def progress(self) -> int:
        """Rows committed so far this stage (0 until the first successful commit)."""
        return self._progress

    # ── watchdog side ────────────────────────────────────────────────────────
    def cancel(self, reason: str = "") -> None:
        """Ask the stage to stop at its next committed boundary. Idempotent."""
        if not self._event.is_set():
            self._reason = reason
        self._event.set()

    # ── stage side ───────────────────────────────────────────────────────────
    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def is_set(self) -> bool:  # Event-compatible alias, so a bare Event also works
        return self._event.is_set()

    def check(self, where: str) -> None:
        """🔴 Call ONLY at a committed boundary. Raises :class:`StageCancelled` if set.

        ``where`` names the boundary (e.g. ``"chunk 4/9"``) and lands in the log and the
        alarm, so a cancelled run says exactly how far it got — the thing the 08-31
        reconstruction had to infer from upsert counts.
        """
        if self._event.is_set():
            raise StageCancelled(self.stage, where, self._reason)


def checkpoint(token: "CancelToken | None", where: str) -> None:
    """``token.check(where)`` tolerant of ``None``.

    Lets a shared function (``backfill_sync``, ``auto_import``) carry checkpoints while its
    non-watchdog callers (``pipeline_run``, CLI ``main``) pass nothing and behave exactly
    as before. NEGATIVE: a ``None`` token is not "cancellation disabled by accident" — it
    is a caller with no watchdog, which is the only case where running to completion is
    correct.
    """
    if token is not None and token.is_set():
        stage = getattr(token, "stage", "stage")
        reason = getattr(token, "reason", "")
        raise StageCancelled(stage, where, reason)


def note_progress(token: "CancelToken | None", n: int) -> None:
    """``token.note_progress(n)`` tolerant of ``None`` and of a bare ``threading.Event``.

    Same shape as :func:`checkpoint`: the shared loops call it unconditionally, and a caller
    with no watchdog (``pipeline_run``, the CLI ``main``) simply has nothing to count into.
    """
    fn = getattr(token, "note_progress", None)
    if fn is not None:
        fn(n)
