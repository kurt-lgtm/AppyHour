"""Common data structures and utilities for all parsers."""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


@dataclass
class Shipment:
    """Unified shipment record across all carrier invoice formats."""
    tracking: str
    carrier: str               # OnTrac, UPS, FedEx
    service: str               # Ground Residential, FedEx 2Day, Home Delivery, etc.
    hub: str                   # Dallas, Nashville, Anaheim, Indianapolis
    state: str                 # 2-letter destination state
    zip_code: str              # 5-digit destination zip
    city: str                  # destination city
    zone: str                  # carrier zone
    cost: float                # net charge
    ship_date: Optional[date] = None
    delivery_date: Optional[date] = None
    transit_days: Optional[int] = None
    ship_dow: str = ""         # Monday, Tuesday, etc.
    invoice_id: str = ""       # invoice number for traceability
    source_file: str = ""      # original file path

    def __post_init__(self):
        if self.ship_date and self.delivery_date and self.transit_days is None:
            self.transit_days = (self.delivery_date - self.ship_date).days
        if self.ship_date and not self.ship_dow:
            self.ship_dow = self.ship_date.strftime('%A')


@dataclass
class CustomerIssue:
    """Unified customer issue record (from Gorgias, CX exports, etc.)."""
    order_id: str
    issue_type: str            # lost_in_transit, misdelivered, damaged, etc.
    state: str
    zip_code: str = ""
    city: str = ""
    carrier: str = ""
    tracking: str = ""
    date_reported: Optional[date] = None
    resolution: str = ""       # reship, refund, credit
    cost_impact: float = 0.0   # estimated cost of issue
    source_file: str = ""


def parse_date_flexible(val) -> Optional[date]:
    """Parse dates from various invoice formats."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    for fmt in ('%Y%m%d', '%m/%d/%Y', '%m/%d/%Y %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def identify_hub(ref_field: str = "", shipper_city: str = "", shipper_state: str = "") -> str:
    """Identify fulfillment hub from invoice reference or shipper fields."""
    # Check reference field first (OnTrac, UPS)
    ref_lower = ref_field.lower()
    if 'nashville' in ref_lower:
        return 'Nashville'
    if 'dallas' in ref_lower:
        return 'Dallas'
    if 'anaheim' in ref_lower:
        return 'Anaheim'

    # Check shipper city/state (FedEx)
    city_upper = shipper_city.upper().strip()
    state_upper = shipper_state.upper().strip()
    if city_upper == 'GARLAND' or (state_upper == 'TX' and city_upper != 'WOBURN'):
        return 'Dallas'
    if city_upper in ('NASHVILLE', 'ANTIOCH') or (state_upper == 'TN'):
        return 'Nashville'
    if city_upper == 'ANAHEIM' or (state_upper == 'CA'):
        return 'Anaheim'
    if city_upper == 'INDIANAPOLIS' or (state_upper == 'IN'):
        return 'Indianapolis'
    if city_upper == 'WOBURN' or (state_upper == 'MA'):
        return 'HQ_IGNORE'

    return 'Unknown'
