"""Curation resolution — ported from inventory_demand_report.resolve_curation."""
from __future__ import annotations

from .config import KNOWN_CURATIONS, MONTHLY_PATTERNS


def resolve_curation(sku: str | None) -> str | None:
    """Return curation code, "MONTHLY", or None.

    Hyphen-anchored match prevents substring false-positives (e.g. MS in NMS).
    Longer curations win when nested (iterate by length desc).
    """
    if not sku:
        return None
    sku = sku.strip().upper()
    if sku in MONTHLY_PATTERNS:
        return "MONTHLY"
    if "-MCUST-NMS" in sku:
        return "NMS"
    if "-MCUST-MS" in sku or "-CUR-MS" in sku:
        return "MS"
    for cur in sorted(KNOWN_CURATIONS, key=len, reverse=True):
        if sku.endswith("-" + cur) or ("-" + cur + "-") in sku:
            return cur
    return None


def is_large_box(sku: str | None) -> bool:
    s = (sku or "").strip().upper()
    return s.startswith("AHB-L") or s.startswith("AHB-LCUST")
