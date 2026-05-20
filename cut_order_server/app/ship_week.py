"""Ship-week math + tag computation.

Rules (from cut_order_rules.md):
- Ship weeks start Monday.
- Monday run: WK1 includes BOTH this-Mon and next-Mon ship tags.
- Tue+ run: WK1 includes ONLY next-Mon ship tag.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ShipWeek:
    wk1_start: date  # Monday
    wk1_end: date    # Sunday
    tags: tuple[str, ...]  # one or two _SHIP_<iso-mon> tags

    @property
    def wk1_end_label(self) -> str:
        return self.wk1_end.isoformat()


def compute_ship_week(today: date | None = None) -> ShipWeek:
    today = today or date.today()
    days_to_mon = (0 - today.weekday()) % 7
    next_mon = today + timedelta(days=days_to_mon if days_to_mon != 0 else 7)

    tags = [f"_SHIP_{next_mon.isoformat()}"]
    if today.weekday() == 0:  # Monday: include this Mon's tag too
        this_mon = today
        tags.insert(0, f"_SHIP_{this_mon.isoformat()}")
        wk1_start = this_mon
    else:
        wk1_start = next_mon

    wk1_end = wk1_start + timedelta(days=6)
    return ShipWeek(wk1_start=wk1_start, wk1_end=wk1_end, tags=tuple(tags))
