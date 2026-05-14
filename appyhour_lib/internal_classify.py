"""Internal-shipment classification for shipments table.

An "internal" shipment is one AppyHour sent to itself or a staff member —
test, return, sample, or employee delivery. Distinct from the legacy
HQ_IGNORE concept (which was Woburn-specific pre-Feb-2026 customer filter).

Internal shipments should be excluded from per-box averages, cohort
denominators, and customer-failure analysis.

Rule (in priority order):
  1. Recipient zip in known AppyHour-hub adjacent area
  2. Recipient name matches known staff/internal pattern
  3. Sender zip == Recipient zip (round-trip within same building)

Hub-zip mapping (per kori settings + observed sender zips):
  • TX (Dallas/Garland): 75040, 75041, 75042, 75043
  • TN (Nashville):       37210, 37214, 37013
  • CA (Anaheim):         90001, 90002
  • IN (Indianapolis):    46204, 46205, 46241
  • MA (Woburn, ex-HQ):   01801, 01803  ← decommissioned Feb 2026

If you add a new hub or a routinely-shipped-to staff location, extend
INTERNAL_ZIPS below.
"""
from __future__ import annotations

INTERNAL_ZIPS: frozenset[str] = frozenset({
    # TX — Garland / Dallas hub area (75042 = AppyHour sender, 75040 = neighbor)
    "75040", "75041", "75042", "75043",
    # TN — Nashville hub
    "37210", "37214", "37013",
    # CA — Anaheim hub
    "90001", "90002",
    # IN — Indianapolis hub
    "46204", "46205", "46241",
    # MA — Woburn (decommissioned Feb 2026, but pre-Feb test shipments + post-Feb staff sends still occur)
    "01801", "01803",
})

INTERNAL_CITIES_FALLBACK: frozenset[str] = frozenset({
    "GARLAND", "NASHVILLE", "ANAHEIM", "INDIANAPOLIS", "WOBURN",
})

# Company-name patterns are safe to ship in source — generic, no individual PII.
INTERNAL_RECIPIENT_NAMES: frozenset[str] = frozenset({
    "APPYHOUR",
    "ELEVATE FOODS",
})

# Staff/individual names live in a gitignored config file (not source) so we
# don't commit individual PII to git history. The file is plain JSON; one
# uppercase name per array entry. Falls back to empty set if file missing.
#
# Location: %APPDATA%\AppyHour\internal_recipients.json
#   {"names": ["FIRST", "LAST", ...]}
#
# Edit that file to extend the staff list. Never put names in this module.
def _load_staff_names() -> frozenset[str]:
    import json
    import os
    from pathlib import Path
    path = Path(os.environ.get("APPDATA", str(Path.home()))) / "AppyHour" / "internal_recipients.json"
    if not path.exists():
        return frozenset()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(str(n).strip().upper() for n in data.get("names", []) if n)
    except Exception:
        return frozenset()


_STAFF_NAMES: frozenset[str] = _load_staff_names()


def is_internal(*,
                recipient_zip: str | None = None,
                recipient_city: str | None = None,
                recipient_state: str | None = None,
                recipient_name: str | None = None,
                sender_zip: str | None = None) -> bool:
    """Classify a shipment as internal/test based on recipient + optional sender.

    Returns True if any rule matches. Conservative — only flags clear matches.
    """
    rz = (recipient_zip or "").strip()[:5]
    rc = (recipient_city or "").strip().upper()
    rs = (recipient_state or "").strip().upper()
    rn = (recipient_name or "").strip().upper()
    sz = (sender_zip or "").strip()[:5]

    # Rule 1: recipient zip in known hub area
    if rz and rz in INTERNAL_ZIPS:
        return True

    # Rule 1b: Woburn MA city fallback (pre-Feb-2026 shipments may lack zip)
    if rc == "WOBURN" and rs == "MA":
        return True

    # Rule 2a: recipient name = company/brand pattern (safe to ship)
    if rn:
        for pat in INTERNAL_RECIPIENT_NAMES:
            if pat in rn:
                return True

    # Rule 2b: recipient name = known staff member from gitignored config
    if rn and _STAFF_NAMES:
        for staff in _STAFF_NAMES:
            if staff in rn:
                return True

    # Rule 3: round-trip — same zip out and back (only if both present)
    if sz and rz and sz == rz:
        return True

    return False
