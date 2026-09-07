"""UPS invoice CSV -> `Shipment` records — a THIN adapter over the ONE canonical UPS reader.

🔴 2026-09-07 (BUG_LOG, TWO-READERS): this file used to be a full second UPS parser (headed +
Detailed Billing v2.1), and the cloud ingest dispatched `Invoice_0*` files here while the local
ingest ran `shipping_invoice_db.parse_ups_csv_bytes` — two readers, two costs for the same
tracking. The bodies now live in `appyhour_lib/invoice_parsers.py` (constraints SSOT:
`ShipRouting/INVOICE_INGEST_RULES.md` §5.2/§5.3). This module only maps the canonical dict rows
onto the legacy `Shipment` dataclass for the ShippingReports consumers. Never add parsing here.
"""

import os
import sys
from typing import List

from .common import Shipment, parse_date_flexible

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from appyhour_lib.invoice_parsers import parse_ups_csv_bytes  # noqa: E402


def _invoice_id_from_name(filepath: str) -> str:
    base = os.path.basename(filepath)
    return base.split('_')[1] if '_' in base else ''


def parse_ups_csv(filepath: str) -> List[Shipment]:
    """Parse a UPS invoice CSV (either dialect, auto-detected by shape) into Shipment records —
    ONE per tracking, charge lines summed. Identity = the invoice number in the filename."""
    invoice_id = _invoice_id_from_name(filepath)
    with open(filepath, 'rb') as fh:
        rows = parse_ups_csv_bytes(fh.read(), invoice_id)
    out = []
    for r in rows:
        out.append(Shipment(
            tracking=r["tracking"],
            carrier='UPS',
            service=r.get("service") or '',
            hub=r.get("hub") or '',
            state=r.get("state") or '',
            zip_code=r.get("zip_code") or '',
            city=r.get("city") or '',
            zone=r.get("zone") or '',
            cost=r.get("cost") or 0.0,
            ship_date=parse_date_flexible(r.get("ship_date")),
            delivery_date=None,
            invoice_id=r.get("invoice_id") or invoice_id,
            source_file=filepath,
            weight=r.get("weight"),
            billed_weight=r.get("billed_weight"),
        ))
    return out
