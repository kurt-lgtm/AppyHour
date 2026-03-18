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
            if not tracking or tracking in seen_tracking:
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

            try:
                cost = float(row[52].strip() or '0')
            except (ValueError, IndexError):
                cost = 0.0

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

            shipments.append(Shipment(
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
            ))

    return shipments


def _parse_header_csv(filepath: str) -> List[Shipment]:
    """Parse header-based UPS invoice CSV (older format)."""
    shipments = []
    invoice_id = os.path.basename(filepath).split('_')[1] if '_' in os.path.basename(filepath) else ''

    with open(filepath, 'r', encoding='latin-1') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tracking = (row.get('Tracking Number', '') or '').strip()
            if not tracking:
                continue

            hub = identify_hub(
                ref_field=row.get('Reference No.2', ''),
                shipper_city=row.get('Sender City', ''),
                shipper_state=row.get('Sender State', ''),
            )

            pickup = parse_date_flexible(row.get('Pickup Date', ''))

            charge_str = (row.get('Billed Charge', '0') or '0').strip().replace('"', '')
            incentive_str = (row.get('Incentive Credit', '0') or '0').strip().replace('"', '')
            try:
                cost = float(charge_str) + float(incentive_str)
            except ValueError:
                cost = 0.0

            shipments.append(Shipment(
                tracking=tracking,
                carrier='UPS',
                service=(row.get('Service Level', '') or '').strip(),
                hub=hub,
                state=(row.get('Receiver State', '') or '').strip().upper(),
                zip_code=(row.get('Receiver Zip Code', '') or '').strip()[:5],
                city=(row.get('Receiver City', '') or '').strip(),
                zone=(row.get('Zone', '') or '').strip(),
                cost=cost,
                ship_date=pickup,
                delivery_date=None,
                invoice_id=invoice_id,
                source_file=filepath,
            ))

    return shipments


def parse_ups_csv(filepath: str) -> List[Shipment]:
    """Parse a UPS invoice CSV into Shipment records.

    Auto-detects format:
      - v2.1 Detailed Billing File (no headers, positional)
      - Header-based CSV (older exports with column names)
    """
    if _is_detailed_billing(filepath):
        return _parse_detailed_billing(filepath)
    return _parse_header_csv(filepath)
