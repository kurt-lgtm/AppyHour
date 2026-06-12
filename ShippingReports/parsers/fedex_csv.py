"""Parser for FedEx-direct CSV invoices (downloaded from FedEx Billing Online).

Format (header-row CSV):
  Bill to Account Number, Invoice Date, Invoice Number,
  Express or Ground Tracking ID, Transportation Charge Amount, Net Charge Amount,
  Service Type, Shipment Date, POD Delivery Date,
  Actual Weight Amount, Rated Weight Amount,
  Recipient City, Recipient State, Recipient Zip Code,
  Shipper State, Zone Code, Original Ref#2 (hub ref)

These are AHB-direct (-113-family) invoices, distinct from the RMFG -911 XLSX format.
"""
import csv
import os
from datetime import datetime
from typing import List
from .common import Shipment, identify_hub


def _parse_yyyymmdd(s: str):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y%m%d').date()
    except ValueError:
        return None


def parse_fedex_csv(filepath: str) -> List[Shipment]:
    shipments = []
    seen = set()
    base = os.path.basename(filepath)
    with open(filepath, 'r', encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            tracking = (row.get('Express or Ground Tracking ID') or '').strip()
            if not tracking or tracking in seen:
                continue
            seen.add(tracking)
            ship_date = _parse_yyyymmdd(row.get('Shipment Date'))
            delivery_date = _parse_yyyymmdd(row.get('POD Delivery Date'))
            try:
                cost = float((row.get('Net Charge Amount') or '0').replace(',', ''))
            except ValueError:
                cost = 0.0
            zip_raw = (row.get('Recipient Zip Code') or '').strip()
            zip_code = zip_raw.split('-')[0][:5]
            ref2 = (row.get('Original Ref#2') or '').strip()
            hub = identify_hub(
                ref_field=ref2,
                shipper_city='',
                shipper_state=(row.get('Shipper State') or '').strip(),
            )
            invoice_id = (row.get('Invoice Number') or '').strip()
            shipments.append(Shipment(
                tracking=tracking,
                carrier='FedEx',
                service=(row.get('Service Type') or '').strip(),
                hub=hub,
                state=(row.get('Recipient State') or '').strip().upper(),
                zip_code=zip_code,
                city=(row.get('Recipient City') or '').strip(),
                zone=(row.get('Zone Code') or '').strip(),
                cost=cost,
                ship_date=ship_date,
                delivery_date=delivery_date,
                invoice_id=invoice_id,
                source_file=filepath,
            ))
    return shipments
