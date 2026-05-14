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

# Sentinel staff/test names seen in FedEx Recipient Name field — extend as observed.
INTERNAL_RECIPIENT_NAMES: frozenset[str] = frozenset({
    "PAM",         # observed in FedEx Priority Overnight to GARLAND
    "APPYHOUR",
    "ELEVATE FOODS",
})


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

    # Rule 2: recipient name match (staff/AppyHour-as-receiver)
    if rn:
        for pat in INTERNAL_RECIPIENT_NAMES:
            if pat in rn:
                return True

    # Rule 3: round-trip — same zip out and back (only if both present)
    if sz and rz and sz == rz:
        return True

    return False
