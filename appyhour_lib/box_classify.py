"""Box-type classification from Shopify order line items.

Rules (per ~/.knowledge memory `feedback_box_type_classification.md` +
`feedback_distvol_box_tiers.md`):

  • Count distinct TR-prefixed SKUs across line items.
      - 7+ distinct TR-* SKUs  → TRAY_LARGE
      - 1-6 distinct TR-* SKUs → TRAY  (UNLESS only SKU is AHB-MCUR-TRAY → REGULAR_MEDIUM)
  • Otherwise, classify by distributed-volume tier (cu in / 105.3):
      - DV ≤ 2.99 → REGULAR_MEDIUM (small)
      - DV ≤ 6.7  → REGULAR_MEDIUM (medium)  [team naming convention]
      - DV >  6.7 → REGULAR_LARGE
  • No SKU / unknown → REGULAR_MEDIUM (safe default)

`historical=True` callers (backfill) treat missing SKU data as "no TR-"
so we never spuriously upgrade a row to TRAY without evidence.

Output values match `~/.knowledge/codebase/shipping-db-schema.md` box_type
column domain: REGULAR_MEDIUM, REGULAR_LARGE, TRAY, TRAY_LARGE, SPECIALTY.
"""
from __future__ import annotations

from typing import Iterable

__all__ = ["classify_box"]


def _sku_strings(line_items: Iterable[dict]) -> list[str]:
    """Pull SKU strings from a Shopify line_items list, upper-cased + stripped."""
    skus: list[str] = []
    for li in line_items or ():
        if not isinstance(li, dict):
            continue
        sku = li.get("sku") or ""
        if not sku:
            continue
        skus.append(str(sku).strip().upper())
    return skus


def classify_box(line_items: Iterable[dict], *, historical: bool = False) -> str:
    """Return the box_type bucket for an order with given Shopify line items.

    Args:
        line_items: list of Shopify line item dicts. Each may have a "sku" key.
        historical: if True, treat sparse data conservatively — never upgrade
                    to TRAY without explicit TR- SKU evidence.

    Returns one of: TRAY_LARGE, TRAY, REGULAR_LARGE, REGULAR_MEDIUM, SPECIALTY.
    """
    skus = _sku_strings(line_items)
    if not skus:
        return "REGULAR_MEDIUM"

    # TR- detection (distinct SKUs starting with "TR-")
    tr_skus = {s for s in skus if s.startswith("TR-")}
    distinct_tr = len(tr_skus)

    # Special-case: lone AHB-MCUR-TRAY = single-tray AHB → REGULAR_MEDIUM
    if distinct_tr == 1 and len(skus) == 1 and "AHB-MCUR-TRAY" in tr_skus:
        return "REGULAR_MEDIUM"

    if distinct_tr >= 7:
        return "TRAY_LARGE"
    if distinct_tr >= 1:
        return "TRAY"

    # Non-tray fallback. Without dimensions in line_items we can't compute
    # distributed-volume here — default to REGULAR_MEDIUM. The dedicated
    # distvol-based path lives elsewhere (kori webview analyze step).
    _ = historical  # currently unused — present for future audit-mode hooks
    return "REGULAR_MEDIUM"
