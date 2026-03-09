"""Ingest all invoice files from the data directory into a unified shipment database.

Usage:
    python ingest.py [--invoice-dir PATH] [--output PATH]

Scans for OnTrac CSVs, UPS CSVs, and FedEx XLSX files.
Outputs a single JSON file with all parsed shipments for downstream analysis.
"""

import argparse
import glob
import json
import os
import sys
from datetime import date, datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from parsers.ontrac import parse_ontrac_csv
from parsers.ups import parse_ups_csv
from parsers.fedex import parse_fedex_xlsx
from parsers.common import Shipment


def find_invoice_files(invoice_dir: str) -> dict:
    """Auto-detect invoice files by carrier type."""
    files = {
        'ontrac': sorted(glob.glob(os.path.join(invoice_dir, '*OnTrac*Shipping*Breakdown*.csv'))),
        'ups': sorted(glob.glob(os.path.join(invoice_dir, 'Invoice_*.csv'))),
        'fedex': sorted(glob.glob(os.path.join(invoice_dir, '*FedEx*Shipping*Breakdown*.XLSX'))),
    }
    return files


def shipment_to_dict(s: Shipment) -> dict:
    """Convert Shipment dataclass to JSON-serializable dict."""
    return {
        'tracking': s.tracking,
        'carrier': s.carrier,
        'service': s.service,
        'hub': s.hub,
        'state': s.state,
        'zip': s.zip_code,
        'city': s.city,
        'zone': s.zone,
        'cost': s.cost,
        'ship_date': s.ship_date.isoformat() if s.ship_date else None,
        'delivery_date': s.delivery_date.isoformat() if s.delivery_date else None,
        'transit_days': s.transit_days,
        'ship_dow': s.ship_dow,
        'invoice_id': s.invoice_id,
        'source_file': os.path.basename(s.source_file),
    }


def main():
    parser = argparse.ArgumentParser(description='Ingest shipping invoices')
    parser.add_argument('--invoice-dir', default='../GelPackCalculator/Invoices',
                        help='Directory containing invoice files')
    parser.add_argument('--output', default='output/shipments.json',
                        help='Output JSON file path')
    args = parser.parse_args()

    invoice_dir = os.path.abspath(args.invoice_dir)
    if not os.path.isdir(invoice_dir):
        print(f"Invoice directory not found: {invoice_dir}")
        sys.exit(1)

    files = find_invoice_files(invoice_dir)

    all_shipments = []
    stats = {'ontrac': 0, 'ups': 0, 'fedex': 0, 'files': 0}

    # OnTrac
    for f in files['ontrac']:
        shipments = parse_ontrac_csv(f)
        all_shipments.extend(shipments)
        stats['ontrac'] += len(shipments)
        stats['files'] += 1
        print(f"  OnTrac: {os.path.basename(f)} -> {len(shipments)} shipments")

    # UPS
    for f in files['ups']:
        shipments = parse_ups_csv(f)
        all_shipments.extend(shipments)
        stats['ups'] += len(shipments)
        stats['files'] += 1
        print(f"  UPS:    {os.path.basename(f)} -> {len(shipments)} shipments")

    # FedEx
    for f in files['fedex']:
        try:
            shipments = parse_fedex_xlsx(f)
            all_shipments.extend(shipments)
            stats['fedex'] += len(shipments)
            stats['files'] += 1
            print(f"  FedEx:  {os.path.basename(f)} -> {len(shipments)} shipments")
        except ImportError as e:
            print(f"  FedEx:  {os.path.basename(f)} -> SKIPPED ({e})")

    # Write output
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    output_data = {
        'generated': datetime.now().isoformat(),
        'stats': stats,
        'shipments': [shipment_to_dict(s) for s in all_shipments],
    }
    with open(args.output, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\nIngested {len(all_shipments)} shipments from {stats['files']} files")
    print(f"  OnTrac: {stats['ontrac']}, UPS: {stats['ups']}, FedEx: {stats['fedex']}")
    print(f"Output: {args.output}")


if __name__ == '__main__':
    main()
