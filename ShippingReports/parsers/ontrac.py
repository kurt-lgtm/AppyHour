"""Parser for OnTrac/LaserShip shipping invoice CSVs."""

import csv
import os
from typing import List
from .common import Shipment, parse_date_flexible, identify_hub


def parse_ontrac_csv(filepath: str) -> List[Shipment]:
    """Parse an OnTrac shipping breakdown CSV into Shipment records.

    Expected file pattern: AHB_NNNNN_OnTrac Shipping Breakdown_AHB_M-D-YY.csv
    Key columns: Reference1 (hub), First Scan Date Time, Proof of Delivery DateTime,
                 Destination State/City/Postalcode, Zone, Total Charges
    """
    shipments = []
    invoice_id = os.path.basename(filepath).split('_')[1] if '_' in os.path.basename(filepath) else ''

    with open(filepath, 'r', encoding='latin-1') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            hub = identify_hub(ref_field=row.get('Reference1', ''))
            scan_date = parse_date_flexible(row.get('First Scan Date Time', ''))
            pod_date = parse_date_flexible(row.get('Proof of Delivery DateTime', ''))

            cost_str = row.get('Total Charges', '0') or '0'
            try:
                cost = float(cost_str)
            except ValueError:
                cost = 0.0

            shipments.append(Shipment(
                tracking=row.get('Tracking Number', '').strip(),
                carrier='OnTrac',
                service=row.get('Service Code', 'RD').strip() or 'RD',
                hub=hub,
                state=row.get('Destination State', '').strip().upper(),
                zip_code=row.get('Destination Postalcode', '').strip()[:5],
                city=row.get('Destination City', '').strip(),
                zone=row.get('Zone', '').strip(),
                cost=cost,
                ship_date=scan_date,
                delivery_date=pod_date,
                invoice_id=invoice_id,
                source_file=filepath,
            ))

    return shipments
