"""Incremental-ingest watermarks — see HEARTBEAT_RULES.md rule 17 (constraints SSOT).

A watermark is the answer to "what is the OLDEST thing this feed may still be missing?".
A leg that has one fetches `[watermark - overlap, now]` instead of a fixed window, so its
cost tracks CHANGE rather than the size of the active dataset.

🔴 WHY THIS FILE EXISTS (2026-09-02). `sync_logon`'s `fulfillments` stage hit its 600s
watchdog on EVERY run since 2026-08-01 and had become a daily page. Leg 1
(`backfill_sync.sync_fulfillments`) re-fetched a **fixed 30-day window every run** — the
whole active subscriber base — to gain ~2,500 changed rows. Measured in isolation against a
scratch DB (canonical opened `connect_ro` only): **696.3s / 12,573 rows on 2026-08-31**, and
**142.4s / 12,748 rows on 2026-09-02**.

🔴 THOSE TWO NUMBERS ARE THE SAME CODE ON THE SAME WINDOW, 4.9× APART — do not quote either
as *the* cost of this leg. The variable is Shopify's page latency, which we do not control,
multiplied by a page count that only grows with the subscriber base. On the slow day leg 1
alone blew the ceiling; on the fast day it fit and leg 2 blew it. A leg whose runtime is a
function of the dataset rather than the change will outgrow whatever ceiling it is given,
which is why the answer is the window and never `STAGE_TIMEOUT_S` — and the ceiling is also
what stops an abandoned stage from becoming an untracked writer (rule 14).

🔴 THE FAILURE MODE THAT MATTERS IS NOT THE TIMEOUT — IT IS A WATERMARK THAT LIES.
A timeout re-fetches the window next run and loses nothing. A watermark advanced past rows
that were never fetched loses them **silently and forever**: nothing errors, no count looks
wrong, and the gap is only discovered when someone asks why an order has no tracking. So:

* **Advance ONLY on a committed batch.** Never at process start, never on a timeout, never
  on a cancel, and never for a window whose pagination did not complete. `sync_fulfillments`
  tracks per-chunk fetch success and stops advancing at the FIRST incomplete chunk — a
  watermark is a low-water mark, so a later chunk must never jump over an earlier hole.
* **Advance is MONOTONIC** (:func:`advance` keeps the newer of the two). A caller that
  computed a stale value cannot walk the mark backwards and silently re-open a gap in the
  other direction either.
* **Overlap on READ, not on write.** The stored value is the exact upper bound of a window
  that WAS fetched; the safety margin is subtracted by the reader
  (`backfill_sync.WATERMARK_OVERLAP_HOURS`). Baking the margin into the stored value would
  compound it every run and slowly walk the mark backwards.

🔴 WHY `C:\\AppyHourData` AND NOT `%APPDATA%` — this is the third file to make this move and
the reason has not changed. MSIX virtualizes `%APPDATA%`, so a real-context writer (the
`appyhour_sync_on_logon` schtask) and a packaged reader (an agent, the MCP servers) get TWO
physical files with disjoint histories. `heartbeats.json` moved 2026-08-31;
`sync_heartbeat.json` moved 2026-09-01 after the fork cost a false "ingest stale: 6.8d" page
on a day every leg had run. A forked WATERMARK is worse than a forked heartbeat: a heartbeat
fork produces a false alarm, a watermark fork produces **missing data** — one context banks
progress the other never sees, so whichever side runs next re-derives its window from a
value that does not describe what was actually fetched. `C:\\AppyHourData` is outside the
virtualization scope: one physical file, written in either context, read cleanly from both.

🔴 TIMESTAMPS HERE ARE AWARE-UTC (`...Z`), unlike `sync_heartbeat.json` (naive local). They
are not a clock reading — they are a value copied out of the SOURCE SYSTEM's domain (Shopify
`updated_at`, which is UTC) and fed straight back to it as `updated_at_min`. Do NOT
"harmonise" the two files: converting these to local time would shift every window by the
UTC offset, and a window shifted BACKWARD is just slow while a window shifted FORWARD skips
rows — the silent-loss failure above.

NEGATIVE — this file is NOT a heartbeat and must never be graded as one. It records how far
a feed got, not when it last ran; `automation_health` keeps grading `sync_heartbeat.json`.
A watermark that stops advancing because the feed is genuinely quiet is healthy.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["CANONICAL", "ENV_OVERRIDE", "advance", "clear", "parse", "path", "read", "read_all"]

CANONICAL = Path(r"C:\AppyHourData\sync_watermarks.json")

# Test/benchmark escape hatch ONLY. 🔴 A benchmark that writes the REAL watermark while
# writing its rows to a SCRATCH db would advance the mark past rows canonical never
# received — the exact silent loss this module is built to prevent. Every isolated run
# MUST set this. No production caller sets it; there is deliberately no fallback chain.
ENV_OVERRIDE = "AH_SYNC_WATERMARK_PATH"


def path() -> Path:
    """The watermark file this process reads and writes."""
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    return Path(override) if override else CANONICAL


def parse(ts: object) -> datetime | None:
    """`'2026-09-02T13:45:00Z'` -> aware UTC datetime. Unparseable/absent -> ``None``.

    ``None`` means "no usable watermark", which every caller must treat as COLD START
    (fall back to the fixed window) — never as "start from zero" and never as "nothing to
    fetch". A corrupt watermark degrading to a full window is slow; degrading to an empty
    window is data loss.
    """
    if ts is None:
        return None
    text = str(ts).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    """Aware datetime -> the `...Z` form Shopify accepts as `updated_at_min`."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_all() -> dict:
    """Every watermark. Returns ``{}`` when the file is absent, unreadable, or not an object.

    The SWALLOW is deliberate: an unreadable watermark must degrade to a cold start (a full
    fixed window), never abort the ingest. Losing the file costs one slow run, and that is
    the designed-for outcome.
    """
    p = path()
    try:
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 — see docstring: degrade to cold start, never abort
        return {}
    return data if isinstance(data, dict) else {}


def read(key: str) -> str | None:
    """The stored watermark for ``key``, or ``None`` (cold start)."""
    val = read_all().get(key)
    return str(val) if isinstance(val, str) and val.strip() else None


def _write_all(data: dict) -> None:
    """Atomically replace the file (temp in the same dir + ``os.replace``).

    A torn write here is read back as "no watermark" -> a cold-start full window. That is
    the safe direction, but a plain ``write_text`` leaves the torn file on disk for the
    next reader too, so the replace is not optional.
    """
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".syncwm")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2, sort_keys=True)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            with contextlib.suppress(OSError):
                os.remove(tmp)


def advance(key: str, upto: datetime | str) -> str | None:
    """Move ``key``'s watermark forward to ``upto``. Returns the value now stored.

    🔴 CALL THIS ONLY AFTER THE BATCH COVERING ``upto`` HAS COMMITTED **and** its write
    connection has closed — the same boundary rule as `appyhour_lib.cancel.checkpoint`, for
    a sharper reason: a checkpoint fired mid-transaction abandons a partial write that the
    next run redoes, while a watermark advanced mid-transaction tells the next run there is
    nothing to redo.

    🔴 MONOTONIC. An ``upto`` older than what is stored is IGNORED (the stored value is
    returned unchanged). Walking a watermark backwards is not "re-fetching to be safe" — it
    is a second writer's stale view silently re-opening a window the first one closed, and
    with an upsert on the far end nobody ever sees it happen.

    An unparseable ``upto`` is a no-op: better a window that stays wide than a mark nobody
    can interpret.
    """
    new = upto if isinstance(upto, datetime) else parse(upto)
    if new is None:
        return read(key)
    if new.tzinfo is None:
        new = new.replace(tzinfo=timezone.utc)
    data = read_all()
    cur = parse(data.get(key))
    if cur is not None and cur >= new:
        return _fmt(cur)
    data[key] = _fmt(new)
    _write_all(data)
    return data[key]


def clear(key: str) -> None:
    """Drop ``key``, forcing the next run back to its cold-start window.

    The sanctioned repair when a watermark is suspected of having advanced past unfetched
    rows: there is no "rewind by N hours" API on purpose — a hole of unknown depth is not
    repaired by a guess, it is repaired by re-deriving the full fixed window.
    """
    data = read_all()
    if key in data:
        del data[key]
        _write_all(data)
