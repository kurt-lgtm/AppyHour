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
    "EVENT_STALE_DAYS",
    "ISO_CUTOVER",
    "parse_report_date",
    "week_start",
    "weekly_orphan_stats",
    "max_event_date",
    "check_feedback_event_freshness",
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


# ── EVENT-date freshness (2026-09-07) ───────────────────────────────────────
# 🔴 WHY THIS EXISTS, and why it is NOT the sweep's `feedback.synced_at` row.
# For ELEVEN WEEKS (2026-06-19 -> 2026-09-04) `MAX(date_reported)` read 2026-06-19 while the tee
# wrote every week and `MAX(synced_at)` read 2026-09-04. Nothing was frozen: the writer emitted
# the sheet's '%m/%d/%Y' string, and `MAX()` over TEXT is LEXICAL — '2026-06-19' > '09/04/2026'
# because '2' > '0'. The newest ISO row MASKED 692 newer rows, 223 of them `Arrived Warm`.
#
# Three separate lessons are encoded below, and each one is load-bearing:
#
# 1. 🔴 NEVER `MAX(date_reported)` IN SQL. Grade the max PARSED date. A lexical max over a
#    mixed-format TEXT column is not a maximum; it is whichever format sorts highest. This is
#    the entire eleven-week failure and it is one function call away from returning.
# 2. 🔴 The freshness question is about the EVENT, not the INGEST. `synced_at` answers "did the
#    task run"; only `date_reported` answers "is a new issue being recorded". Substituting the
#    ingest stamp is what let a masked column read green (CLAUDE.md "Data discipline":
#    metadata is not an event date). The sweep keeps BOTH rows — they fail independently.
# 3. 🔴 FAIL CLOSED on zero/unparseable (ENGINEERING_GOTCHAS C4). No rows, no parseable rows, or
#    a read error is UNKNOWN and flags. "Nothing to check yet" is never inferred from absence.
#
# Cadence: the scheduled owner is `\AppyHour\GorgiasUpdate` — WEEKLY, Wed 09:00,
# `StartWhenAvailable`. Per HEARTBEAT_RULES rule 4 a weekly writer gets ~10 days, never 7: a
# catch-up run after a slept-through slot legally lands >7d after the last one. 12 = 10 + slack
# for a ticket arriving late in a quiet week; it still fires ~4 weeks before a quarter of blind
# warm data accumulates.
EVENT_STALE_DAYS = 12

# 🔴 The tee writes ISO from this date (the `_normalize_date_reported` fix in
# `AppyHourMCP/tools/gorgias_sheets_sync.py`). Rows synced BEFORE it are the 692-row legacy
# '%m/%d/%Y' band plus the pre-2026-06-19 ISO history; they are NOT graded, because repairing
# them is a backfill and a backfill is Kurt's decision, not a monitor's. Rows synced ON OR AFTER
# it MUST be ISO — that is what makes a silent re-regression of the writer LOUD instead of
# invisible for another eleven weeks. Do not advance this date to silence the flag: a flag here
# means the writer stopped canonicalising and `MAX`/`ORDER BY` are lying again.
ISO_CUTOVER = date(2026, 9, 7)


def max_event_date(rows) -> tuple[date | None, int, int]:
    """(newest parsed date_reported, parsed_count, unparseable_count).

    🔴 Computed by PARSING every value, never by SQL `MAX()` — see the header.
    `rows` is an iterable of 1-tuples/sequences whose first element is date_reported.
    """
    newest: date | None = None
    parsed = 0
    bad = 0
    for r in rows:
        raw = r[0] if isinstance(r, tuple | list) else r
        if raw is None or not str(raw).strip():
            continue
        d = parse_report_date(raw)
        if d is None:
            bad += 1
            continue
        parsed += 1
        if newest is None or d > newest:
            newest = d
    return newest, parsed, bad


def check_feedback_event_freshness(db_path: str | None = None, now: datetime | None = None):
    """Assert a NEW ISSUE has been recorded recently, and that the tee writes ISO.

    Returns (flags, ok). Read-only by construction (mode=ro URI).
    """
    now = now or datetime.now()
    today = now.date()
    path = db_path or _default_db_path()
    flags: list[str] = []
    ok: list[str] = []

    if not os.path.exists(path):
        return [f"FLAG feedback event freshness: db missing at {path} — UNKNOWN"], []

    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        try:
            rows = con.execute("SELECT date_reported FROM feedback").fetchall()
            # 🔴 The format check needs `synced_at` to know which rows are POST-fix. Reduced
            # test fixtures carry only the three completeness columns. A missing column is a
            # SCHEMA fact, not a writer failure, so it must not masquerade as either a flag or
            # a verified green — it is reported as explicitly NOT VERIFIED below. Production
            # has the column; if it ever vanished there, the completeness read fails loudly.
            has_synced = any(
                r[1] == "synced_at" for r in con.execute("PRAGMA table_info(feedback)").fetchall()
            )
            post = con.execute(
                "SELECT date_reported FROM feedback WHERE substr(synced_at,1,10) >= ?",
                (ISO_CUTOVER.isoformat(),),
            ).fetchall() if has_synced else None
        finally:
            con.close()
    except sqlite3.Error as e:
        return [f"FLAG feedback event freshness: query failed ({e}) — UNKNOWN"], []

    newest, parsed, bad = max_event_date(rows)

    # (1) EVENT recency. Fails closed on an empty/unparseable table.
    if newest is None:
        flags.append(
            f"FLAG feedback EVENT freshness: no parseable `date_reported` in {len(rows):,} rows "
            f"({bad:,} unparseable) — UNKNOWN, not clean. A zero here is a claim, never a quiet week."
        )
    else:
        age = (today - newest).days
        if age > EVENT_STALE_DAYS:
            flags.append(
                f"FLAG feedback EVENT freshness: newest date_reported {newest:%Y-%m-%d} is {age}d "
                f"old, limit {EVENT_STALE_DAYS}d ({parsed:,} parsed rows). 🔴 `synced_at` recency "
                f"CANNOT see this — 2026-06-19..2026-09-04 the tee ran weekly and this column sat "
                f"masked. Zero warm rows is NOT zero warm boxes. Check the Gorgias tee "
                f"(AppyHourMCP/tools/gorgias_sheets_sync.py) and its scheduled owner "
                f"\\AppyHour\\GorgiasUpdate."
            )
        else:
            ok.append(
                f"ok feedback EVENT freshness: newest date_reported {newest:%Y-%m-%d} "
                f"({age}d, limit {EVENT_STALE_DAYS}d, {parsed:,} parsed)"
            )

    # (2) The tee must write ISO from ISO_CUTOVER on. A non-ISO row synced after the fix means
    # the normaliser regressed and every MAX/ORDER BY over this column is lying again.
    if post is None:
        return flags, ok + ["-- feedback date_reported format: no `synced_at` column — NOT verified"]
    non_iso = [r[0] for r in post if r[0] and not _is_iso(r[0])]
    if non_iso:
        sample = ", ".join(sorted({str(v) for v in non_iso})[:3])
        flags.append(
            f"FLAG feedback date_reported FORMAT: {len(non_iso):,} of {len(post):,} rows synced on/after "
            f"{ISO_CUTOVER:%Y-%m-%d} are not ISO YYYY-MM-DD (e.g. {sample}). The tee's "
            f"`_normalize_date_reported` has regressed — `MAX(date_reported)` is a LEXICAL max and "
            f"will silently mask every newer row (the 2026-06-19 eleven-week burn)."
        )
    elif post:
        ok.append(f"ok feedback date_reported format: {len(post):,} post-cutover rows all ISO")
    else:
        # No post-cutover rows yet is legitimate only right after the fix ships; say so out loud
        # rather than passing silently, so it cannot read as a verified green.
        ok.append(
            f"-- feedback date_reported format: no rows synced on/after {ISO_CUTOVER:%Y-%m-%d} yet "
            f"— format NOT yet verified in production"
        )

    return flags, ok


def _is_iso(raw: object) -> bool:
    s = str(raw).strip()
    if len(s) != 10:
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


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

    # 🔴 EVENT-date freshness rides the SAME wiring (freshness_sweep already calls this function
    # and already fails closed on an exception here). It is a SEPARATE failure from the orphan
    # rate and from the sweep's `synced_at` row: recency-of-ingest, completeness-of-field, and
    # recency-of-EVENT all failed independently in this table's history, and all three must flag
    # independently. Do not collapse them.
    ef_flags, ef_ok = check_feedback_event_freshness(path, now=now)
    flags.extend(ef_flags)
    ok.extend(ef_ok)

    if not os.path.exists(path):
        # Keep ef_flags: dropping them would lose a flag we already raised.
        return flags + [f"FLAG feedback completeness: db missing at {path}"], ok

    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "SELECT date_reported, order_number, gorgias_link FROM feedback"
        ).fetchall()
    except sqlite3.Error as e:
        return flags + [f"FLAG feedback completeness: query failed ({e})"], ok
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
