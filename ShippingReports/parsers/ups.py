"""Parser for UPS shipping invoice CSVs."""

import csv
import os
from typing import List
from .common import Shipment, parse_date_flexible, identify_hub


def parse_ups_csv(filepath: str) -> List[Shipment]:
    """Parse a UPS invoice CSV into Shipment records.

    Expected file pattern: Invoice_000000C411H40NN_MMDDYY.csv
    Key columns: Reference No.2 (hub), Pickup Date, Receiver State/City/Zip Code,
                 Zone, Billed Charge, Incentive Credit, Service Level
    Note: UPS invoices have no delivery date — transit_days will be None.
    """
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
                delivery_date=None,  # UPS invoices don't include delivery date
                invoice_id=invoice_id,
                source_file=filepath,
            ))

    return shipments
