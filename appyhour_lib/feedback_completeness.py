"""Field-level completeness assert for the Gorgias -> shipping.db `feedback` tee.

🔴 WHY THIS EXISTS (read the failure first)
===========================================
2026-08-17 week: the Gorgias sync kept running, kept writing rows, and kept
looking healthy — while 34 of 55 rows (61.8%) landed with a BLANK
`order_number`. Baseline for the 24 prior weeks was 0.0-8.9% (mean 1.4%).
Slack named 26 wk0817 orders with shipping issues; the DB could only join 16.
Every ticket-rate metric computed off `feedback` for that week was a FLOOR,
not a measurement, and nothing said so.

The recency check already in `freshness_sweep.py` (`feedback.synced_at`, 14d)
was GREEN throughout. That is the point: **a writer that runs on time and
writes rows with a blank field is WORSE than one that stops**, because
row-count/recency freshness cannot see it. Dead-cadence instance #4 in this
system (after ontrac_master, mfg_translations, shopify_orders, fulfillments)
and the first one where the cadence never actually died.

Root cause of that instance: Gorgias's `GET /tickets` LIST payload stopped
embedding `customer.integrations`, which was the sync's primary (and, it
turned out, ONLY working) order-number source — the documented
Shopify-by-email fallback had been dead code since it was written. See
`AppyHourMCP/tools/gorgias_sheets_sync.py`.

WHAT THIS ASSERTS — and what it deliberately does NOT
=====================================================
- Asserts: the ORPHAN RATE (share of Gorgias-teed rows with no order_number)
  of each recently-COMPLETED report week.
- Does NOT assert recency. That row already exists in the sweep and stays;
  the two failures are independent and must flag independently.
- Scope is `gorgias_link IS NOT NULL AND <> ''` — i.e. rows this tee wrote.
  Bulk/manual imports have no link and must not dilute the denominator
  ([[self-verifying-denominator]]: the measured thing must not get to pick
  the population it is measured against).
- Grain is `date_reported` (the business event date), never `synced_at`
  (an ingest timestamp — CLAUDE.md "Data discipline": metadata is not an
  event date, and one backfill run restamps every row's synced_at).

THRESHOLD DERIVATION (measured, not guessed)
============================================
24 complete report-weeks with n>=15 rows, 2026-03-02 .. 2026-08-10:
    max 8.9% (wk 2026-06-08), mean 1.4%, median 0.0%.
The failing week measured 61.8%.
`ORPHAN_RATE_MAX = 0.15` sits above the worst clean week observed (8.9%) with
margin, and roughly 5 sd above the mean at a typical n~55 — so a clean week
does not cry wolf, while any degradation losing more than ~1 row in 7 trips
it. Raising this threshold to silence a flag is the wrong move: the flag
means the join population shrank, and every rate computed off it is a floor.

GOTCHAS
=======
- `date_reported` carries TWO formats in production ('2026-08-19' and
  '08/19/2026'). Parse both or the whole recent window silently vanishes from
  the denominator — which reads as "no rows", not as "parse failed".
- Only COMPLETED weeks are graded. The in-progress week is legitimately
  half-synced; grading it would flag every Monday.
- A week under `MIN_ROWS` is reported informationally, never flagged — small-n
  noise would train Kurt to ignore the line.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta

__all__ = [
    "ORPHAN_RATE_MAX",
    "MIN_ROWS",
    "parse_report_date",
    "week_start",
    "weekly_orphan_stats",
    "check_feedback_completeness",
]

# See THRESHOLD DERIVATION above. Change only with a fresh measurement in hand.
ORPHAN_RATE_MAX = 0.15
MIN_ROWS = 15
# How many completed report-weeks back to grade. 2 = last complete week plus
# the one before, so a degradation is still caught if a sweep is missed.
WEEKS_GRADED = 2


def parse_report_date(raw: object) -> date | None:
    """Parse a `feedback.date_reported` value. Handles BOTH production formats.

    Returns None for anything unparseable — callers must count those, never
    silently drop them (a parse failure that shrinks the denominator looks
    exactly like a clean week).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def week_start(d: date) -> date:
    """Monday of the report-week containing `d`."""
    return d - timedelta(days=d.weekday())


def weekly_orphan_stats(rows) -> dict[date, tuple[int, int]]:
    """{week_start: (total_rows, orphan_rows)} over Gorgias-teed rows.

    `rows` is an iterable of (date_reported, order_number, gorgias_link).
    """
    out: dict[date, list[int]] = {}
    for date_reported, order_number, gorgias_link in rows:
        if gorgias_link is None or not str(gorgias_link).strip():
            continue  # not a row this tee wrote — see scope note in the docstring
        d = parse_report_date(date_reported)
        if d is None:
            continue
        wk = week_start(d)
        bucket = out.setdefault(wk, [0, 0])
        bucket[0] += 1
        if order_number is None or not str(order_number).strip():
            bucket[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def _default_db_path() -> str:
    env = os.environ.get("APPYHOUR_DB_PATH", "").strip()
    if env:
        return env
    return r"C:\AppyHourData\shipping.db"


def check_feedback_completeness(db_path: str | None = None, now: datetime | None = None):
    """Grade the last WEEKS_GRADED completed report-weeks. Returns (flags, ok).

    Read-only by construction (mode=ro URI) — this assert must never be able to
    write shipping.db (MSIX/WAL corruption memory).
    """
    now = now or datetime.now()
    path = db_path or _default_db_path()
    flags: list[str] = []
    ok: list[str] = []

    if not os.path.exists(path):
        return [f"FLAG feedback completeness: db missing at {path}"], []

    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "SELECT date_reported, order_number, gorgias_link FROM feedback"
        ).fetchall()
    except sqlite3.Error as e:
        return [f"FLAG feedback completeness: query failed ({e})"], []
    finally:
        con.close()

    stats = weekly_orphan_stats(rows)
    current_week = week_start(now.date())
    graded = [current_week - timedelta(weeks=i) for i in range(1, WEEKS_GRADED + 1)]

    for wk in graded:
        total, orphans = stats.get(wk, (0, 0))
        label = f"wk{wk:%m%d}"
        if total == 0:
            ok.append(f"-- feedback completeness {label}: no Gorgias-teed rows")
            continue
        rate = orphans / total
        if total < MIN_ROWS:
            ok.append(
                f"-- feedback completeness {label}: {orphans}/{total} orphaned "
                f"({rate:.0%}) — n<{MIN_ROWS}, not graded"
            )
            continue
        if rate > ORPHAN_RATE_MAX:
            flags.append(
                f"FLAG feedback ORDER-NUMBER completeness {label}: {orphans}/{total} rows "
                f"({rate:.1%}) have no order_number, limit {ORPHAN_RATE_MAX:.0%} "
                f"(24-week baseline 0.0-8.9%, mean 1.4%) — the Gorgias tee is RUNNING but "
                f"writing unjoinable rows; every ticket-rate off `feedback` for this week is a "
                f"FLOOR, not a measurement. Check the order-number extraction in "
                f"AppyHourMCP/tools/gorgias_sheets_sync.py (2026-08-17: Gorgias dropped "
                f"customer.integrations from the LIST payload)"
            )
        else:
            ok.append(
                f"ok feedback completeness {label}: {orphans}/{total} orphaned ({rate:.1%})"
            )
    return flags, ok
