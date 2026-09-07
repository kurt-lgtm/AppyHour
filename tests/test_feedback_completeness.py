"""Production-shape tests for the Gorgias->feedback completeness assert.

🔴 WHY THESE ARE NOT FIXTURE-SHAPED TESTS
=========================================
[[fail-closed-needs-production-shape-test]]: 3 of 4 fail-opens in this codebase
were in guards written to close the previous one, and every one of them was
green against an INJECTED shape the author imagined. So this suite grades the
assert against `tests/fixtures/feedback_completeness.sqlite`, which is a
verbatim copy of three REAL report-weeks out of production `shipping.db`:

    2026-06-08   56 rows,  5 orphaned  ( 8.9%)  <- worst CLEAN week on record
                                                   (ISO 'YYYY-MM-DD' dates)
    2026-07-27   53 rows,  0 orphaned  ( 0.0%)  <- clean week ('MM/DD/YYYY')
    2026-08-17   55 rows, 34 orphaned  (61.8%)  <- THE FAILURE

Real dates, real row counts, real per-row null pattern, both real date formats.
The only thing substituted is the identity of non-blank order numbers and
ticket URLs, which the assert never reads. Regenerate with
`tests/fixtures/build_feedback_completeness_fixture.py` (read-only against prod).

The two load-bearing proofs are `test_fires_on_the_real_wk0817` and
`test_passes_on_the_worst_real_clean_week` — a threshold that only clears the
0.0% week would be untested against the real noise floor.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from appyhour_lib.feedback_completeness import (  # noqa: E402
    MIN_ROWS,
    ORPHAN_RATE_MAX,
    check_feedback_completeness,
    check_feedback_event_freshness,
    max_event_date,
    parse_report_date,
    week_start,
    weekly_orphan_stats,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "feedback_completeness.sqlite"

# A moment inside the week AFTER each graded week, so that week counts as COMPLETED.
AFTER_WK0608 = datetime(2026, 6, 17, 12, 0)
AFTER_WK0727 = datetime(2026, 8, 5, 12, 0)
AFTER_WK0817 = datetime(2026, 8, 26, 12, 0)


@pytest.fixture(scope="module")
def fixture_db() -> str:
    assert FIXTURE.exists(), f"production-shape fixture missing: {FIXTURE}"
    return str(FIXTURE)


# --------------------------------------------------------------------------
# The two load-bearing proofs: fires on the real failure, silent on real clean
# --------------------------------------------------------------------------

def test_fires_on_the_real_wk0817(fixture_db):
    """61.8% orphaned, real rows -> must FLAG, and must name the week + the rate."""
    flags, _ok = check_feedback_completeness(fixture_db, now=AFTER_WK0817)
    assert len(flags) == 1, f"expected exactly one flag, got {flags}"
    msg = flags[0]
    assert msg.startswith("FLAG")
    assert "wk0817" in msg
    assert "34/55" in msg
    assert "61.8%" in msg


def test_passes_on_the_worst_real_clean_week(fixture_db):
    """8.9% orphaned (2026-06-08, the noisiest clean week in 24) -> must NOT flag.

    This is the false-positive guard the threshold actually has to survive.
    """
    flags, ok = check_feedback_completeness(fixture_db, now=AFTER_WK0608)
    assert flags == [], f"threshold cries wolf on a real clean week: {flags}"
    assert any("wk0608" in line and "8.9%" in line for line in ok), ok


def test_passes_on_a_clean_week(fixture_db):
    """0.0% orphaned (2026-07-27) -> no flag, and reported as ok."""
    flags, ok = check_feedback_completeness(fixture_db, now=AFTER_WK0727)
    assert flags == [], flags
    assert any("wk0727" in line and "0/53" in line for line in ok), ok


def test_threshold_sits_between_the_real_clean_max_and_the_real_failure():
    """Guards the derivation itself, not just the outcome."""
    worst_clean = 5 / 56      # 2026-06-08, measured
    failure = 34 / 55         # 2026-08-17, measured
    assert worst_clean < ORPHAN_RATE_MAX < failure


# --------------------------------------------------------------------------
# The traps that would make the assert silently measure nothing
# --------------------------------------------------------------------------

def test_both_production_date_formats_parse():
    """date_reported carries BOTH shapes in production. Dropping either one
    empties the recent window, which reads as 'no rows', not as a bug."""
    assert parse_report_date("2026-08-19") == date(2026, 8, 19)
    assert parse_report_date("08/19/2026") == date(2026, 8, 19)
    assert parse_report_date("2026-08-19T14:03:00") == date(2026, 8, 19)
    assert parse_report_date("") is None
    assert parse_report_date(None) is None
    assert parse_report_date("not-a-date") is None


def test_fixture_actually_contains_both_date_formats(fixture_db):
    """If the fixture ever loses a format, the parser test above stops meaning
    anything at the integration level. Fail loudly rather than pass hollowly."""
    con = sqlite3.connect(f"file:{fixture_db}?mode=ro", uri=True)
    vals = [r[0] for r in con.execute("SELECT date_reported FROM feedback")]
    con.close()
    assert any("-" in v[:10] for v in vals), "fixture lost the ISO-format rows"
    assert any("/" in v[:10] for v in vals), "fixture lost the MM/DD/YYYY rows"


def test_scope_excludes_rows_this_tee_did_not_write():
    """Linkless rows (bulk/manual imports) must not dilute the denominator —
    [[self-verifying-denominator]]. Two orphans out of two teed rows is 100%,
    regardless of how many linkless rows sit beside them."""
    rows = [
        ("2026-08-19", None, "https://x/1"),
        ("2026-08-19", None, "https://x/2"),
        ("2026-08-19", "#1", None),      # no link -> out of scope
        ("2026-08-19", "#2", ""),        # blank link -> out of scope
    ]
    stats = weekly_orphan_stats(rows)
    assert stats[week_start(date(2026, 8, 19))] == (2, 2)


def test_blank_string_counts_as_orphaned_not_just_null():
    """The tee writes `None` today, but a '' would join to nothing just the same."""
    rows = [("2026-08-19", "", "https://x/1"), ("2026-08-19", "   ", "https://x/2")]
    assert weekly_orphan_stats(rows)[week_start(date(2026, 8, 19))] == (2, 2)


def test_small_week_is_reported_but_never_flagged():
    """Small-n noise must not train the reader to ignore the line."""
    rows = [("2026-08-19", None, f"https://x/{i}") for i in range(MIN_ROWS - 1)]
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE feedback (date_reported TEXT, order_number TEXT, gorgias_link TEXT)")
    con.executemany("INSERT INTO feedback VALUES (?,?,?)", rows)
    con.commit()
    path = Path(os.environ.get("TEMP", ".")) / "feedback_smalln_test.sqlite"
    if path.exists():
        path.unlink()
    disk = sqlite3.connect(str(path))
    con.backup(disk)
    disk.close()
    con.close()
    flags, ok = check_feedback_completeness(str(path), now=AFTER_WK0817)
    path.unlink(missing_ok=True)
    assert flags == [], flags
    assert any("not graded" in line for line in ok), ok


def test_missing_db_flags_rather_than_returning_green():
    """Absence of evidence is not a clean week."""
    flags, ok = check_feedback_completeness(r"C:\nope\nothing-here.sqlite")
    assert flags and "db missing" in flags[0]
    assert ok == []


def test_in_progress_week_is_not_graded(fixture_db):
    """Grading the current week would flag every Monday on a half-synced week."""
    mid_wk0817 = datetime(2026, 8, 19, 12, 0)
    flags, _ok = check_feedback_completeness(fixture_db, now=mid_wk0817)
    assert flags == [], f"in-progress week must not be graded: {flags}"


# --------------------------------------------------------------------------
# Live-DB check — proves the assert still binds to the real table today
# --------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.path.exists(os.environ.get("APPYHOUR_DB_PATH") or r"C:\AppyHourData\shipping.db"),
    reason="production shipping.db not present on this machine",
)
def test_runs_against_the_live_table_without_error():
    """Schema-binding check: the real `feedback` table still has the three
    columns this assert reads. Does not assert an outcome — the live weeks move."""
    flags, ok = check_feedback_completeness(now=AFTER_WK0817)
    assert flags or ok
    assert not any("query failed" in f for f in flags), flags


# --------------------------------------------------------------------------
# EVENT-date freshness + ISO format (2026-09-07 eleven-week masked-column burn)
# --------------------------------------------------------------------------

def _event_db(rows, name):
    """rows = [(date_reported, synced_at)] -> path to a temp sqlite file."""
    path = Path(os.environ.get("TEMP", ".")) / name
    path.unlink(missing_ok=True)
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE feedback (date_reported TEXT, order_number TEXT, "
        "gorgias_link TEXT, synced_at TEXT)"
    )
    con.executemany(
        "INSERT INTO feedback VALUES (?,?,?,?)",
        [(d, "#1", f"https://x/{i}", s) for i, (d, s) in enumerate(rows)],
    )
    con.commit()
    con.close()
    return path


def test_lexical_max_masks_newer_rows_but_the_assert_does_not():
    """THE BURN, pinned. '2026-06-19' > '09/04/2026' lexically, so SQL MAX() reports
    the June row and reads eleven weeks stale. max_event_date PARSES, so it sees
    September. If this ever fails, someone reintroduced MAX(date_reported)."""
    rows = [("2026-06-19",), ("09/04/2026",)]
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE feedback (date_reported TEXT)")
    con.executemany("INSERT INTO feedback VALUES (?)", rows)
    assert con.execute("SELECT MAX(date_reported) FROM feedback").fetchone()[0] == "2026-06-19"
    con.close()
    newest, parsed, bad = max_event_date(rows)
    assert newest == date(2026, 9, 4), newest
    assert (parsed, bad) == (2, 0)


def test_event_freshness_flags_a_genuinely_frozen_table():
    path = _event_db([("2026-06-19", "2026-09-04T10:00:00")], "fb_event_stale.sqlite")
    flags, _ = check_feedback_event_freshness(str(path), now=datetime(2026, 9, 7, 12, 0))
    path.unlink(missing_ok=True)
    assert any("EVENT freshness" in f for f in flags), flags


def test_event_freshness_is_green_when_only_the_FORMAT_was_masking():
    """The live 2026-09-07 shape: US-format rows are recent EVENTS. The event
    assert must not cry stale just because MAX() was lying."""
    path = _event_db([("2026-06-19", "2026-06-19T16:05:17"),
                      ("09/04/2026", "2026-09-04T16:06:45")], "fb_event_fresh.sqlite")
    flags, ok = check_feedback_event_freshness(str(path), now=datetime(2026, 9, 7, 12, 0))
    path.unlink(missing_ok=True)
    assert not any("EVENT freshness" in f for f in flags), flags
    assert any("EVENT freshness" in line for line in ok), ok


def test_empty_and_unparseable_tables_fail_CLOSED():
    """C4: a monitor's zero branch defaults to RED, never a quiet pass."""
    for rows, name in (([], "fb_event_empty.sqlite"),
                       ([("not-a-date", "2026-09-04T10:00:00")], "fb_event_junk.sqlite")):
        path = _event_db(rows, name)
        flags, _ = check_feedback_event_freshness(str(path), now=datetime(2026, 9, 7, 12, 0))
        path.unlink(missing_ok=True)
        assert any("no parseable" in f for f in flags), (name, flags)


def test_non_iso_row_synced_after_the_cutover_is_LOUD():
    """A writer regression must not be invisible for another eleven weeks."""
    path = _event_db([("09/08/2026", "2026-09-08T10:00:00")], "fb_fmt_bad.sqlite")
    flags, _ = check_feedback_event_freshness(str(path), now=datetime(2026, 9, 9, 12, 0))
    path.unlink(missing_ok=True)
    assert any("FORMAT" in f for f in flags), flags


def test_legacy_pre_cutover_us_rows_are_NOT_flagged():
    """The 692-row legacy band is a Kurt backfill decision, not a monitor's."""
    path = _event_db([("09/04/2026", "2026-09-04T16:06:45")], "fb_fmt_legacy.sqlite")
    flags, _ = check_feedback_event_freshness(str(path), now=datetime(2026, 9, 7, 12, 0))
    path.unlink(missing_ok=True)
    assert not any("FORMAT" in f for f in flags), flags


def test_normalizer_writes_iso_and_never_guesses():
    """The writer-side fix. Sheet format in, ISO out; junk preserved verbatim."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AppyHourMCP"))
    from tools.gorgias_sheets_sync import _normalize_date_reported as n
    assert n("09/04/2026") == "2026-09-04"
    assert n("2026-09-04") == "2026-09-04"
    assert n("2026-09-04T16:06:45") == "2026-09-04"
    assert n("June-11") == "June-11"      # never year-guessed (the 2026-06-18 burn)
    assert n("") is None and n(None) is None


# ── SECOND WRITER (2026-09-07) ────────────────────────────────────────────────────────
# 🔴 The tee was fixed alone on 2026-09-07 and a sweep the same day found `store_feedback` in
# `GelPackCalculator/shipping_invoice_db.py` — reachable from Kori's feedback save and from
# `import_feedback_csv.py` — writing `date_reported` verbatim. One Kori save after the approved
# 692-row ISO backfill would have re-mixed the column and restored the lexical-`MAX` mask.
# These tests exist so a THIRD writer, or a divergence between the two, is caught here.

def _store_feedback_value(col_value):
    """Run a Kori-shaped entry dict through the GelPackCalculator writer's value extractor."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "GelPackCalculator"))
    import shipping_invoice_db as sidb
    return sidb._feedback_value({"date": col_value}, "date_reported")


def test_gelpack_writer_canonicalises_date_reported_to_iso():
    """A Kori-shaped save lands as ISO, not the sheet's %m/%d/%Y."""
    assert _store_feedback_value("06/16/2026") == "2026-06-16"


def test_gelpack_writer_leaves_iso_unchanged():
    assert _store_feedback_value("2026-06-16") == "2026-06-16"
    assert _store_feedback_value("2026-06-16T09:12:00") == "2026-06-16"


def test_gelpack_writer_stores_unparseable_verbatim():
    """Never dropped, never year-guessed — an odd string beats a lost event date."""
    assert _store_feedback_value("June-11") == "June-11"
    assert _store_feedback_value("") == ""
    assert _store_feedback_value(None) == ""


def test_both_writers_agree_on_every_input():
    """🔴 THE ANTI-DIVERGENCE TEST. Two copies of a date rule is the original bug one level up.

    If this ever fails, one writer grew its own implementation — fix by deleting it and calling
    `appyhour_lib.feedback_completeness.normalize_report_date`, never by patching both.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AppyHourMCP"))
    from tools.gorgias_sheets_sync import _normalize_date_reported as tee
    for raw in ("06/16/2026", "09/04/2026", "2026-09-04", "2026-09-04T16:06:45",
                "June-11", "Dec 6", "not a date", "", "  "):
        assert (tee(raw) or "") == _store_feedback_value(raw), raw
