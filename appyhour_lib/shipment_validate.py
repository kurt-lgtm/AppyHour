"""Row-level sanity gate for carrier-invoice ingest into `shipments`.

🔴 WHY THIS EXISTS (negatives-first — 2026-07-30 incident):
`_outputs/scripts/load_fedex_invoices.py` and `load_ups_invoices_with_weight.py` mapped
FedEx/UPS invoice CSV columns **positionally** (`r[37]`, `r[79]`, ...) against a layout
guess. The real FedEx Billing Online detail extract is 210 columns with
`Recipient City`=37, `Recipient State`=38, `Recipient Zip Code`=39, `Zone Code`=64 — so
every value from `city` rightward landed one column left of where the script thought:
`state='GREEN VALLEY'`, `zip_code='AZ'`, `zone='US'`, and the real ZIP was lost.
1,370 rows across four May invoices were written that way. sqlite is typeless, so nothing
complained; it was only caught in July when MySQL's `varchar(8)` on `shipments.state`
rejected the oversized values during the managed-DB migration. That column has since been
widened to `varchar(32)` so the MySQL mirror can stay verbatim — **MySQL will no longer
catch a recurrence. This module is the replacement safety net.**

Rules:
  - NEVER map invoice columns positionally. Map by header name (`csv.DictReader`).
  - Every writer into `shipments` runs each row through `check_row()` / `assert_row()`
    BEFORE the INSERT/UPDATE, and drops (or hard-fails on) rejects rather than writing them.
  - A silently-dropped row is still a failure: log the reject count and the first few
    reasons. Silence is the thing that let this sit for two months.
"""
from __future__ import annotations

import re

_STATE_RE = re.compile(r"^[A-Z]{2}$")
_ZIP_RE = re.compile(r"^\d{5}$")


def check_row(row: dict) -> list[str]:
    """Return a list of validation problems for one shipment dict. Empty == OK.

    Only fields that are *present and non-empty* are checked, except `tracking`,
    which is always required — a shipment row without one is unusable.
    Blank/None `state`/`zip_code` are allowed (some carrier formats omit them and a
    later enrich pass fills them); a *wrong-shaped* value is not.
    """
    problems: list[str] = []

    if not (row.get("tracking") or "").strip():
        problems.append("tracking is empty")

    state = (row.get("state") or "").strip()
    if state and not _STATE_RE.match(state.upper()):
        problems.append(f"state={state!r} is not a 2-letter code (column offset?)")

    zip_code = (row.get("zip_code") or "").strip()
    if zip_code and not _ZIP_RE.match(zip_code):
        problems.append(f"zip_code={zip_code!r} is not 5 digits (column offset?)")

    return problems


def assert_row(row: dict) -> None:
    """Raise ValueError if the row is not writable. For paths that must fail loud."""
    problems = check_row(row)
    if problems:
        raise ValueError(
            f"shipment row rejected (tracking={row.get('tracking')!r}): " + "; ".join(problems)
        )


def partition(rows: list[dict]) -> tuple[list[dict], list[tuple[dict, list[str]]]]:
    """Split rows into (writable, rejected-with-reasons)."""
    good: list[dict] = []
    bad: list[tuple[dict, list[str]]] = []
    for r in rows:
        problems = check_row(r)
        (bad.append((r, problems)) if problems else good.append(r))
    return good, bad


def report_rejects(bad: list[tuple[dict, list[str]]], *, label: str = "", limit: int = 5) -> str:
    """One-line-per-reject summary. Print it — never drop rows silently."""
    if not bad:
        return f"{label}validation: 0 rejected"
    lines = [f"{label}validation: 🔴 {len(bad)} row(s) REJECTED (not written)"]
    for r, problems in bad[:limit]:
        lines.append(f"    tracking={r.get('tracking')!r}: {'; '.join(problems)}")
    if len(bad) > limit:
        lines.append(f"    ... and {len(bad) - limit} more")
    return "\n".join(lines)
