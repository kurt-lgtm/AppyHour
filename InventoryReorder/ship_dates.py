"""Unified ship-week date computation for fulfillment cycle.

Weekly cycle:
  Wk1 = next Saturday shipment + following Tuesday shipment (same production week)
  Wk2 = the Saturday after that (next week's first ship)

Both the fulfillment web app and the standalone cut order XLSX generator
import this module for consistent date labels.
"""

from datetime import date, timedelta


def compute_ship_week(reference_date: date | None = None) -> dict:
    """Compute ship dates for the current fulfillment cycle.

    Args:
        reference_date: Date to compute from (default: today).
            Saturday is treated as wk1_sat (user runs app before shipping).

    Returns:
        Dict with date objects and formatted labels:
        - wk1_sat: date  (this week's Saturday shipment)
        - wk1_tue: date  (following Tuesday shipment)
        - wk2_sat: date  (next week's Saturday shipment)
        - label_wk1: str (e.g. "Sat Mar 28 + Tue Mar 31")
        - label_wk2: str (e.g. "Sat Apr 4")
        - ship_tag_sat: str (e.g. "_SHIP_2026-03-28")
        - ship_tag_tue: str (e.g. "_SHIP_2026-03-31")
    """
    today = reference_date or date.today()
    weekday = today.weekday()  # Mon=0 ... Sun=6

    # Find the next Saturday (or today if Saturday)
    # Saturday = weekday 5
    if weekday <= 5:
        # Mon(0)-Sat(5): next Saturday is (5 - weekday) days away
        days_to_sat = (5 - weekday) % 7
        wk1_sat = today + timedelta(days=days_to_sat)
    else:
        # Sunday(6): next Saturday is 6 days away
        wk1_sat = today + timedelta(days=6)

    wk1_tue = wk1_sat + timedelta(days=3)   # Tuesday after Saturday
    wk2_sat = wk1_sat + timedelta(days=7)   # Following Saturday

    # Compact date labels: M/D format
    label_sat = f"{wk1_sat.month}/{wk1_sat.day}"
    label_tue = f"{wk1_tue.month}/{wk1_tue.day}"
    label_wk2 = f"{wk2_sat.month}/{wk2_sat.day}"

    return {
        "wk1_sat": wk1_sat,
        "wk1_tue": wk1_tue,
        "wk2_sat": wk2_sat,
        "label_wk1": f"{label_sat} + {label_tue}",
        "label_wk2": label_wk2,
        "ship_tag_sat": f"_SHIP_{wk1_sat.isoformat()}",
        "ship_tag_tue": f"_SHIP_{wk1_tue.isoformat()}",
    }


def ship_dates_json(reference_date: date | None = None) -> dict:
    """Return ship dates as JSON-serializable dict (dates as ISO strings)."""
    sd = compute_ship_week(reference_date)
    return {
        "wk1_sat": sd["wk1_sat"].isoformat(),
        "wk1_tue": sd["wk1_tue"].isoformat(),
        "wk2_sat": sd["wk2_sat"].isoformat(),
        "label_wk1": sd["label_wk1"],
        "label_wk2": sd["label_wk2"],
        "ship_tag_sat": sd["ship_tag_sat"],
        "ship_tag_tue": sd["ship_tag_tue"],
    }
