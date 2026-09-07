"""Parser for UPS shipping invoice CSVs.

Supports two formats:
  1. Header-based CSV (older exports) — has column headers like 'Tracking Number', etc.
  2. UPS Detailed Billing File v2.1 (newer exports) — no headers, 250+ positional fields.
"""

import csv
import os
from datetime import date
from typing import List
from .common import Shipment, parse_date_flexible, identify_hub


def _f(v):
    """float or None — a blank or unparseable weight is ABSENT, never 0.0."""
    try:
        return float(str(v).strip()) if str(v or '').strip() else None
    except (TypeError, ValueError):
        return None


def _is_detailed_billing(filepath: str) -> bool:
    """Detect UPS Detailed Billing File (v2.1) — no header row, first field is '2.1'."""
    with open(filepath, 'r', encoding='latin-1') as fh:
        first_line = fh.readline()
    return first_line.startswith('2.1,')


def _parse_detailed_billing(filepath: str) -> List[Shipment]:
    """Parse UPS Detailed Billing File v2.1 (positional, no headers).

    Field map (0-indexed):
      13/20: Tracking Number
      16/22: Hub reference (e.g. Dallas_AHB)
      11: Ship Date (YYYY-MM-DD)
      33: Zone
      43: Line Type (FRT=freight charge, INF=info/dimensions)
      45: Service Level
      52: Billed Charge
      62: Scheduled Delivery Date
      70: Sender City
      71: Sender State
      78: Receiver City
      79: Receiver State
      80: Receiver Zip
    """
    shipments = []
    invoice_id = os.path.basename(filepath).split('_')[1] if '_' in os.path.basename(filepath) else ''
    seen_tracking = set()
    by_tracking = {}          # tracking -> the Shipment being accumulated across its FRT lines

    with open(filepath, 'r', encoding='latin-1') as fh:
        reader = csv.reader(fh)
        for row in reader:
            if len(row) < 81:
                continue

            # Only process freight lines (FRT), skip info lines (INF)
            line_type = row[43].strip() if len(row) > 43 else ''
            if line_type != 'FRT':
                continue

            tracking = row[20].strip() if row[20].strip() else row[13].strip()
            if not tracking:
                continue
            # 🔴 A UPS tracking has MULTIPLE FRT lines (base freight, fuel, residential, DAS...).
            # First-wins kept ONE of them and threw the rest away: 1Z2H94940334864194 landed at
            # $1.40 (a single accessorial) against a true $18.08 — a 92% understatement, and the
            # row still looked plausible because $1.40 is a valid number in a valid column.
            # Accumulate below; the first line seeds the descriptive fields, every FRT line adds
            # its charge.
            if tracking in seen_tracking:
                by_tracking[tracking].cost += _frt_charge(row)
                continue
            seen_tracking.add(tracking)

            hub = identify_hub(
                ref_field=row[22].strip() if row[22].strip() else row[16].strip(),
                shipper_city=row[70].strip() if len(row) > 70 else '',
                shipper_state=row[71].strip() if len(row) > 71 else '',
            )

            ship_date = parse_date_flexible(row[11].strip())
            # Field 62 is the billing due date, NOT delivery date
            delivery_date = None

            cost = _frt_charge(row)

            state = row[79].strip().upper() if len(row) > 79 else ''
            zip_raw = row[80].strip() if len(row) > 80 else ''
            zip_code = zip_raw[:5]

            # Calculate transit days if both dates available
            transit_days = None
            if ship_date and delivery_date:
                try:
                    delta = delivery_date - ship_date
                    transit_days = delta.days if delta.days >= 0 else None
                except (TypeError, AttributeError):
                    pass

            ship = Shipment(
                tracking=tracking,
                carrier='UPS',
                service=row[45].strip() if len(row) > 45 else '',
                hub=hub,
                state=state,
                zip_code=zip_code,
                city=row[78].strip() if len(row) > 78 else '',
                zone=row[33].strip() if len(row) > 33 else '',
                cost=cost,
                ship_date=ship_date,
                delivery_date=delivery_date,
                transit_days=transit_days,
                invoice_id=invoice_id,
                source_file=filepath,
            )
            by_tracking[tracking] = ship
            shipments.append(ship)

    return shipments


def _frt_charge(row) -> float:
    """Field 52 (Billed Charge) for ONE freight line. Caller sums the lines of a tracking.

    Unparseable -> 0.0 rather than a guess: a fabricated charge would be indistinguishable from a
    real one downstream, and cost feeds the per-lane expected-cost model."""
    try:
        return float(row[52].strip() or '0')
    except (ValueError, IndexError):
        return 0.0


def _is_adjustment_section(section: str) -> bool:
    """True for an 'Invoice Section' that is an after-the-fact correction, not the freight charge.
    Adjustment lines carry THIN metadata (blank dest, zone `000`, a later pickup date), so they
    contribute dollars only."""
    s = (section or '').lower()
    return 'adjustment' in s or 'correction' in s


def _parse_header_csv(filepath: str) -> List[Shipment]:
    """Parse header-based UPS invoice CSV (older format).

    🔴 ONE Shipment PER TRACKING, cost = the SUM of every invoice line bearing it — the same rule
    `_parse_detailed_billing` already applies to the 250-col dialect. This branch used to emit one
    Shipment per LINE; the store then deduped on (invoice, tracking) and kept an ARBITRARY
    survivor (cloud: the FIRST line; the local twin in
    `GelPackCalculator/shipping_invoice_db._parse_ups_headed_csv`: the LAST).
    Burn 2026-09-07: `1Z2H94940334864194` = $18.08 `Ground Residential / Outbound/Shipping API`
    + $1.40 `Adjustments & Other Charges/Shipping Charge Corrections` = **$19.48**; the cloud held
    $18.08 and local held $1.40. A credit SUBTRACTS (14.92 - 0.61 = $14.31, real row); non-cost
    fields come from the first FREIGHT line.
    """
    invoice_id = os.path.basename(filepath).split('_')[1] if '_' in os.path.basename(filepath) else ''
    by_tracking: dict = {}

    with open(filepath, 'r', encoding='latin-1') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tracking = (row.get('Tracking Number', '') or '').strip()
            if not tracking:
                continue

            charge_str = (row.get('Billed Charge', '0') or '0').strip().replace('"', '')
            incentive_str = (row.get('Incentive Credit', '0') or '0').strip().replace('"', '')
            try:
                cost = float(charge_str) + float(incentive_str)
            except ValueError:
                cost = 0.0

            is_freight = not _is_adjustment_section(row.get('Invoice Section', ''))
            state = by_tracking.get(tracking)
            if state is None:
                state = {'cost': 0.0, 'lines': 0, 'has_freight': False, 'fields': None}
                by_tracking[tracking] = state

            state['cost'] += cost
            state['lines'] += 1
            if (is_freight and not state['has_freight']) or state['lines'] == 1:
                state['fields'] = dict(
                    service=(row.get('Service Level', '') or '').strip(),
                    hub=identify_hub(
                        ref_field=row.get('Reference No.2', ''),
                        shipper_city=row.get('Sender City', ''),
                        shipper_state=row.get('Sender State', ''),
                    ),
                    state=(row.get('Receiver State', '') or '').strip().upper(),
                    zip_code=(row.get('Receiver Zip Code', '') or '').strip()[:5],
                    city=(row.get('Receiver City', '') or '').strip(),
                    zone=(row.get('Zone', '') or '').strip(),
                    ship_date=parse_date_flexible(row.get('Pickup Date', '')),
                    # 🔴 -> billed_weight, NOT weight. This dialect's `Weight` is integer
                    # chargeable weight; `weight` already holds a billed-else-actual coalesce from
                    # the 250-col dialect, and blending the two would put different facts in one
                    # column per row, invisibly (Kurt caught this 2026-08-20).
                    billed_weight=_f(row.get('Weight')),
                )
            if is_freight:
                state['has_freight'] = True

    return [Shipment(
        tracking=tracking,
        carrier='UPS',
        cost=round(state['cost'], 2),
        delivery_date=None,
        invoice_id=invoice_id,
        source_file=filepath,
        **state['fields'],
    ) for tracking, state in by_tracking.items()]


def parse_ups_csv(filepath: str) -> List[Shipment]:
    """Parse a UPS invoice CSV into Shipment records.

    Auto-detects format:
      - v2.1 Detailed Billing File (no headers, positional)
      - Header-based CSV (older exports with column names)
    """
    if _is_detailed_billing(filepath):
        return _parse_detailed_billing(filepath)
    return _parse_header_csv(filepath)
