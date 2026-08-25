"""Regenerate `feedback_completeness.sqlite` from production shipping.db.

READ-ONLY against production (mode=ro URI). Run only when the fixture needs to
cover a new real week; the committed fixture is otherwise immutable evidence.

What it preserves, and why each one matters:
  * the real `date_reported` STRINGS — production carries two formats
    ('2026-06-10' and '08/19/2026'); a parser that handles one silently empties
    the window, which reads as "no rows" rather than as a bug
  * the real per-row NULL/blank pattern of `order_number` — that IS the thing
    the assert measures
  * the real row counts per week — the threshold is only meaningful at real n
  * the real `gorgias_link` presence pattern — the assert's scope filter

What it substitutes: the IDENTITY of non-blank order numbers and ticket URLs
(synthetic tokens). The assert never inspects those values, so the fixture keeps
production shape without carrying customer-linked ids into the repo.

    python tests/fixtures/build_feedback_completeness_fixture.py
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import sys
from pathlib import Path

SRC = os.environ.get("APPYHOUR_DB_PATH") or r"C:\AppyHourData\shipping.db"
DST = Path(__file__).resolve().parent / "feedback_completeness.sqlite"

# Real weeks, chosen for what each one proves. Keep the comments with the dates.
WINDOWS = [
    (datetime.date(2026, 6, 8), datetime.date(2026, 6, 14)),    # worst CLEAN week (8.9%), ISO dates
    (datetime.date(2026, 7, 27), datetime.date(2026, 8, 2)),    # clean week (0.0%), US dates
    (datetime.date(2026, 8, 17), datetime.date(2026, 8, 23)),   # THE FAILURE (61.8%)
]


def _norm(raw):
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def main() -> int:
    if not os.path.exists(SRC):
        print(f"source db not found: {SRC}", file=sys.stderr)
        return 1
    uri = "file:" + SRC.replace("\\", "/") + "?mode=ro"
    src = sqlite3.connect(uri, uri=True)
    try:
        rows = src.execute(
            "SELECT date_reported, order_number, gorgias_link FROM feedback"
        ).fetchall()
    finally:
        src.close()

    keep = [r for r in rows
            if (d := _norm(r[0])) and any(a <= d <= b for a, b in WINDOWS)]
    keep.sort(key=lambda r: (_norm(r[0]), str(r[2] or "")))

    if DST.exists():
        DST.unlink()
    dst = sqlite3.connect(str(DST))
    dst.execute("CREATE TABLE feedback (date_reported TEXT, order_number TEXT, gorgias_link TEXT)")
    for i, (date_reported, order_number, link) in enumerate(keep):
        has_order = order_number is not None and str(order_number).strip() != ""
        has_link = link is not None and str(link).strip() != ""
        dst.execute(
            "INSERT INTO feedback VALUES (?,?,?)",
            (
                date_reported,
                f"#9{i:05d}" if has_order else order_number,
                f"https://example.invalid/t/{i:05d}" if has_link else link,
            ),
        )
    dst.commit()
    dst.close()

    for a, b in WINDOWS:
        sel = [r for r in keep if a <= _norm(r[0]) <= b]
        orphans = sum(1 for r in sel if r[1] is None or not str(r[1]).strip())
        print(f"  {a}..{b}: {len(sel)} rows, {orphans} orphaned "
              f"({100.0 * orphans / len(sel):.1f}%)" if sel else f"  {a}..{b}: EMPTY")
    print(f"wrote {DST} ({DST.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
