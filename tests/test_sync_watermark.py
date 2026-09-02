"""Incremental watermark for the Shopify fulfillments leg (2026-09-02, HEARTBEAT_RULES rule 17).

🔴 WHAT THIS PROVES. The timeout these tests exist to remove is the *cheap* failure — a
re-fetched window loses nothing. The failure they must rule out is the one the fix
introduces if it is even slightly wrong: **a watermark advanced past rows that were never
fetched**, which loses them silently and forever. So the assertions are, in order of
importance:
  1. a chunk whose pagination FAILED does not advance the mark — and neither does any LATER
     chunk, because a watermark is a low-water mark and jumping a hole is invisible;
  2. a missing / corrupt / unparseable mark degrades to the COLD-START fixed window, never
     to an empty one;
  3. the mark only ever moves forward;
  4. the chunk split covers the window with no gap at the seam.

🔴 No test here opens `C:\\AppyHourData\\shipping.db`. The watermark file is redirected to a
pytest `tmp_path` via `AH_SYNC_WATERMARK_PATH`, and the DB is a stub object — the leg's
writes go through `db.store_fulfillments`, which is monkeypatched.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from appyhour_lib import sync_watermark

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "GelPackCalculator") not in sys.path:
    sys.path.insert(0, str(REPO / "GelPackCalculator"))

backfill_sync = pytest.importorskip("backfill_sync")

KEY = "shopify_fulfillments"


@pytest.fixture(autouse=True)
def _isolated_watermark(tmp_path, monkeypatch):
    """Every test writes its own watermark file — never the canonical one."""
    monkeypatch.setenv(sync_watermark.ENV_OVERRIDE, str(tmp_path / "wm.json"))
    return tmp_path / "wm.json"


# ── file semantics ────────────────────────────────────────────────────────────

def test_round_trip_and_utc_form(_isolated_watermark):
    ts = datetime(2026, 9, 2, 13, 45, tzinfo=timezone.utc)
    assert sync_watermark.advance(KEY, ts) == "2026-09-02T13:45:00Z"
    assert sync_watermark.read(KEY) == "2026-09-02T13:45:00Z"
    assert sync_watermark.parse(sync_watermark.read(KEY)) == ts
    assert _isolated_watermark.exists()


def test_advance_is_monotonic():
    """🔴 A stale caller must not walk the mark backwards — with an upsert on the far end,
    a silently re-opened window is invisible."""
    newer = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    older = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    sync_watermark.advance(KEY, newer)
    assert sync_watermark.advance(KEY, older) == "2026-09-02T12:00:00Z"
    assert sync_watermark.read(KEY) == "2026-09-02T12:00:00Z"


def test_missing_and_corrupt_read_as_no_watermark(_isolated_watermark):
    assert sync_watermark.read(KEY) is None
    _isolated_watermark.write_text("{not json", encoding="utf-8")
    assert sync_watermark.read_all() == {}
    assert sync_watermark.read(KEY) is None
    _isolated_watermark.write_text('{"shopify_fulfillments": "not-a-date"}', encoding="utf-8")
    assert sync_watermark.parse(sync_watermark.read(KEY)) is None


def test_clear_forces_cold_start():
    sync_watermark.advance(KEY, datetime(2026, 9, 2, tzinfo=timezone.utc))
    sync_watermark.clear(KEY)
    assert sync_watermark.read(KEY) is None


# ── window resolution ─────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)


def test_cold_start_falls_back_to_the_fixed_window():
    """No mark → exactly today's behaviour. NEVER an empty window."""
    start, why = backfill_sync._resolve_window_start("2026-08-03", KEY, NOW)
    assert start == datetime(2026, 8, 3, tzinfo=timezone.utc)
    assert "cold start" in why
    assert start < NOW


def test_no_key_ignores_any_stored_mark():
    """`pipeline_run` / the CLI pass no key and must keep the fixed window."""
    sync_watermark.advance(KEY, datetime(2026, 9, 2, 14, tzinfo=timezone.utc))
    start, why = backfill_sync._resolve_window_start("2026-08-03", None, NOW)
    assert start == datetime(2026, 8, 3, tzinfo=timezone.utc)
    assert "cold start" in why


def test_warm_start_subtracts_the_overlap():
    sync_watermark.advance(KEY, datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc))
    start, why = backfill_sync._resolve_window_start("2026-08-03", KEY, NOW)
    assert start == datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc) - timedelta(
        hours=backfill_sync.WATERMARK_OVERLAP_HOURS)
    assert "watermark" in why


def test_future_mark_is_clamped_to_a_non_empty_window():
    """Clock skew must not produce end <= start — that is a silently skipped run."""
    sync_watermark.advance(KEY, NOW + timedelta(days=3))
    start, _ = backfill_sync._resolve_window_start("2026-08-03", KEY, NOW)
    assert start < NOW
    assert backfill_sync._month_chunks(start, NOW)


def test_month_chunks_tile_the_window_with_no_seam_gap():
    start = datetime(2026, 7, 15, 6, 30, tzinfo=timezone.utc)
    chunks = backfill_sync._month_chunks(start, NOW)
    assert chunks[0][0] == start
    assert chunks[-1][1] == NOW
    for (_, hi), (lo2, _) in zip(chunks, chunks[1:], strict=False):
        assert hi == lo2                      # no gap, no overlap at the seam
    assert [c[0].month for c in chunks] == [7, 8, 9]
    assert backfill_sync._month_chunks(NOW, NOW) == []


# ── the one that matters: never advance over an unfetched window ──────────────

class _FakeClient:
    def _url(self, endpoint):
        return f"https://example.invalid/{endpoint}"

    def _headers(self):
        return {}

    def _ensure_token(self):
        pass


class _Resp:
    def __init__(self, status=200, orders=None):
        self.status_code = status
        self._orders = orders or []
        self.headers = {}

    def json(self):
        return {"orders": self._orders}


class _StubConn:
    """Stands in for a write connection: `_writer(conn, None)` yields it as-is."""

    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _order(n: int) -> dict:
    return {
        "id": n, "order_number": n, "created_at": "2026-08-01T00:00:00Z",
        "tags": "", "shipping_address": {"name": "X", "city": "C", "province_code": "TX",
                                         "zip": "75001"},
        "fulfillments": [{"tracking_number": f"TRK{n}", "tracking_company": "FedEx",
                          "created_at": "2026-08-01T00:00:00Z"}],
    }


@pytest.fixture
def _no_sleep(monkeypatch):
    monkeypatch.setattr(backfill_sync.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(backfill_sync.db, "store_fulfillments", lambda *_a, **_k: None)


def test_clean_run_advances_to_the_window_end(monkeypatch, _no_sleep):
    monkeypatch.setattr(backfill_sync.requests, "get",
                        lambda *_a, **_k: _Resp(200, [_order(1)]))
    total = backfill_sync.sync_fulfillments(
        _StubConn(), _FakeClient(), since="2026-09-01", watermark_key=KEY)
    assert total == 1
    mark = sync_watermark.parse(sync_watermark.read(KEY))
    assert mark is not None
    # The mark is the window's upper bound (≈ now), not the newest row's timestamp.
    assert abs((datetime.now(timezone.utc) - mark).total_seconds()) < 120


def test_a_failed_page_holds_the_watermark(monkeypatch, _no_sleep):
    """🔴 THE regression this fix must never introduce.

    A non-200 makes the pagination loop stop as if the window were complete (it sets
    ``url = None``). Harmless while the whole window is re-fetched every run; permanent
    silent loss the moment a mark is advanced over it.
    """
    monkeypatch.setattr(backfill_sync.requests, "get", lambda *_a, **_k: _Resp(429))
    backfill_sync.sync_fulfillments(
        _StubConn(), _FakeClient(), since="2026-09-01", watermark_key=KEY)
    assert sync_watermark.read(KEY) is None      # never advanced off cold start


def test_a_hole_stops_every_later_chunk_from_advancing(monkeypatch, _no_sleep):
    """A watermark is a LOW-water mark: chunk 3 must not jump over chunk 1's hole."""
    seen = {"n": 0}

    def _get(*_a, **_k):
        seen["n"] += 1
        return _Resp(500) if seen["n"] == 1 else _Resp(200, [_order(seen["n"])])

    monkeypatch.setattr(backfill_sync.requests, "get", _get)
    # July → now spans 3 month chunks; the FIRST one fails.
    since = (datetime.now(timezone.utc) - timedelta(days=70)).date().isoformat()
    backfill_sync.sync_fulfillments(
        _StubConn(), _FakeClient(), since=since, watermark_key=KEY)
    assert seen["n"] > 1                        # later chunks really did run
    assert sync_watermark.read(KEY) is None      # ...and none of them advanced the mark


def test_exhausted_retries_also_hold_the_watermark(monkeypatch, _no_sleep):
    def _boom(*_a, **_k):
        raise ConnectionError("dns")

    monkeypatch.setattr(backfill_sync.requests, "get", _boom)
    backfill_sync.sync_fulfillments(
        _StubConn(), _FakeClient(), since="2026-09-01", watermark_key=KEY)
    assert sync_watermark.read(KEY) is None


def test_no_key_never_writes_a_watermark(monkeypatch, _no_sleep):
    monkeypatch.setattr(backfill_sync.requests, "get",
                        lambda *_a, **_k: _Resp(200, [_order(1)]))
    backfill_sync.sync_fulfillments(_StubConn(), _FakeClient(), since="2026-09-01")
    assert sync_watermark.read_all() == {}
