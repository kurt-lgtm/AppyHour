"""Normalize feedback.date_reported to ISO YYYY-MM-DD (one-time backfill).

Root cause: feedback.date_reported accumulated 5 formats across the live
Gorgias sync (MM/DD/YYYY) and a handful of ad-hoc backfill scripts that
wrote year-less 'Month-D' text (e.g. 'June-11'). Year-less dates are
un-sortable and, when a normalizer guesses the current year, 2025 tickets
masquerade as 2026 and pollute current-week reports.

Fix: rewrite every non-ISO date_reported to ISO YYYY-MM-DD.

Year inference for year-less formats ('June-11', 'Dec 6'): a ticket cannot
be reported AFTER it was synced, so pick the most recent year that keeps
date_reported <= synced_at. 'June-11' synced 2026-05-14 -> 2025-06-11;
'June-10' synced 2026-06-11 -> 2026-06-10.

Safe by default: prints a dry-run plan. Pass --apply to commit. On --apply
it first snapshots the table to feedback_backup_<UTCstamp> so the change is
reversible, then VERIFIES that zero non-ISO rows remain before committing —
a rewrite that silently skips a shape leaves the column mixed, which is the
exact state this script exists to end.

🔴 CONNECTION DISCIPLINE (2026-09-07). The dry-run reads through
`appyhour_lib.db.connect_ro()` and the apply writes through `connect()` —
NEVER raw `sqlite3.connect()`. The raw opener was here until 2026-09-07 and
bypassed BOTH the single-writer advisory lock and the canonical-path guard;
that combination (a surplus write handle racing the live MCP servers'
checkpointer) is the direct cause of all three shipping.db WAL corruptions.
A dry-run must not take a write lock at all, which is why the phases use
different openers. If `connect()` raises `DBWriterBusy`, a sync is mid-flight:
wait and re-run — do NOT set AH_WRITE_LOCK_DISABLE to get past it.

Usage:
    python scripts/incident-fixes/normalize_feedback_dates.py            # dry-run
    python scripts/incident-fixes/normalize_feedback_dates.py --apply    # commit
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime

_AH = r"C:/Users/Work/Claude Projects/AppyHour"
if _AH not in sys.path:
    sys.path.insert(0, _AH)
from appyhour_lib.db import DBWriterBusy, connect, connect_ro  # noqa: E402
from appyhour_lib.paths import db_path  # noqa: E402

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Year-less month-name formats (need synced_at to infer the year)
_YEARLESS_FMTS = ("%b-%d", "%B-%d", "%b %d", "%B %d")
# Formats that already carry a year
_YEARED_FMTS = ("%m/%d/%Y", "%b-%d-%Y", "%B-%d-%Y", "%m/%d/%y")


def _parse_synced_year_floor(synced_at: str | None) -> datetime | None:
    """Parse synced_at into a datetime upper bound (None if unparseable)."""
    if not synced_at:
        return None
    s = synced_at.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def normalize(date_reported: str | None, synced_at: str | None) -> tuple[str | None, str]:
    """Return (iso_or_None, status). status in {iso, already, yearless, yeared, null, unpar;}."""
    if date_reported is None or not date_reported.strip():
        return None, "null"
    raw = date_reported.strip()
    if ISO_RE.match(raw):
        return raw, "already"

    # Formats that already carry a year — parse directly.
    for fmt in _YEARED_FMTS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d"), "yeared"
        except ValueError:
            continue

    # Year-less month-name formats — infer year from synced_at.
    floor = _parse_synced_year_floor(synced_at)
    for fmt in _YEARLESS_FMTS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if floor is None:
            # No synced_at to anchor — assume the synced year is unknowable;
            # fall back to floor's absence -> leave year as 1900 sentinel skip.
            return None, "unpar"
        year = floor.year
        # A ticket can't be reported after it was synced. If month/day in the
        # synced year lands after synced_at, it must be the prior year.
        try:
            cand = dt.replace(year=year)
        except ValueError:
            cand = dt.replace(year=year, day=28)  # leap-day guard
        if cand > floor:
            cand = cand.replace(year=year - 1)
        return cand.strftime("%Y-%m-%d"), "yearless"

    return None, "unpar"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    args = ap.parse_args()

    path = str(db_path())
    # Phase 1 — READ ONLY. connect_ro() cannot take a write lock or trigger a
    # checkpoint, so a dry-run is structurally incapable of racing the syncs.
    con = connect_ro(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, date_reported, synced_at FROM feedback"
        ).fetchall()
    finally:
        con.close()

    stats: Counter[str] = Counter()
    updates: list[tuple[str, int]] = []
    unparsed: list[tuple[int, str]] = []
    for r in rows:
        iso, status = normalize(r["date_reported"], r["synced_at"])
        stats[status] += 1
        if status in ("yearless", "yeared") and iso:
            if iso != (r["date_reported"] or ""):
                updates.append((iso, r["id"]))
        if status == "unpar":
            unparsed.append((r["id"], r["date_reported"]))

    print(f"DB: {path}")
    print(f"total rows: {len(rows)}")
    for k in ("already", "yeared", "yearless", "null", "unpar"):
        print(f"  {k:9s}: {stats[k]}")
    print(f"rows to rewrite: {len(updates)}")
    if unparsed:
        print(f"UNPARSEABLE ({len(unparsed)}) — left untouched:")
        for fid, dr in unparsed[:15]:
            print(f"    id={fid} date_reported={dr!r}")

    # Sample of proposed rewrites for eyeball verification
    print("sample rewrites:")
    for iso, fid in updates[:12]:
        orig = next(r["date_reported"] for r in rows if r["id"] == fid)
        sy = next(r["synced_at"] for r in rows if r["id"] == fid)
        print(f"    id={fid}: {orig!r} (synced {sy}) -> {iso}")

    if not args.apply:
        print("\nDRY-RUN. Re-run with --apply to commit.")
        return 0

    if not updates:
        print("\nNothing to rewrite — column is already fully ISO. No write attempted.")
        return 0

    # Phase 2 — WRITE. connect() takes the advisory single-writer lock and
    # enforces the canonical path; it raises DBWriterBusy rather than racing.
    try:
        con = connect(path)
    except DBWriterBusy as e:
        print(f"\nDEFERRED — a writer holds the lock: {e}")
        print("Nothing was changed. Wait for the sync to finish and re-run.")
        return 2

    try:
        # Snapshot before mutating — reversible. Full row copy, so the rollback
        # restores the exact prior string for every touched id.
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup = f"feedback_backup_{stamp}"
        con.execute(f"CREATE TABLE {backup} AS SELECT * FROM feedback")
        snap = con.execute(f"SELECT COUNT(*) FROM {backup}").fetchone()[0]
        con.executemany(
            "UPDATE feedback SET date_reported = ? WHERE id = ?", updates
        )

        # 🔴 VERIFY BEFORE COMMIT. A backfill that reports success while leaving
        # rows in the old shape recreates the mixed column and the lexical-MAX
        # mask. Rows verified in the SAME transaction that made the change.
        live = con.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        left = con.execute(
            "SELECT COUNT(*) FROM feedback WHERE date_reported IS NOT NULL "
            "AND TRIM(date_reported) <> '' "
            "AND NOT (LENGTH(date_reported) = 10 AND date_reported GLOB "
            "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')"
        ).fetchone()[0]
        if left or live != snap:
            con.rollback()
            print(
                f"\nROLLED BACK — verification failed: {left} non-ISO rows remain, "
                f"row count {live} vs snapshot {snap}. Nothing was changed."
            )
            return 1

        con.commit()
        print(f"\nAPPLIED {len(updates)} updates. Backup table: {backup}")
        print(f"VERIFIED: 0 non-ISO rows remain; {live} rows (snapshot {snap}).")
        print(
            "ROLLBACK (paste as one line if needed):\n"
            f"  UPDATE feedback SET date_reported = (SELECT b.date_reported FROM {backup} b "
            f"WHERE b.id = feedback.id) WHERE id IN (SELECT id FROM {backup});"
        )
    finally:
        con.close()  # releases the advisory lock
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
