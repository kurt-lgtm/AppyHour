"""UPS headed-CSV parser: one row per TRACKING, cost = SUM of its invoice lines.

Every value pinned here is VERBATIM from a real carrier invoice under
`AppyHour/GelPackCalculator/Invoices` (2026-09-07 parity audit,
`_outputs/reports/2026-09-07-invoice-cost-parser-parity.md`). No synthetic dollars —
the defect was a real $490.76 understatement across 20 trackings and the regression
guard is only worth what its inputs are.
"""

from __future__ import annotations

import sys
from pathlib import Path

GELPACK_DIR = str(Path(__file__).resolve().parents[1] / "GelPackCalculator")
if GELPACK_DIR not in sys.path:
    sys.path.insert(0, GELPACK_DIR)

import shipping_invoice_db as sidb  # noqa: E402

HEADER = (
    "Account Number,Invoice Number,Invoice Date,Tracking Number,Pickup Record,Reference No.1,"
    "Reference No.2,Reference No.3,Weight,Zone,Service Level,Pickup Date,Sender Name,"
    "Sender Company Name,Sender Street,Sender City,Sender State,Sender Zip Code,Receiver Name,"
    "Receiver Company Name,Receiver Street,Receiver City,Receiver State,Receiver Zip Code,"
    "Receiver Country or Territory,Third Party,Billed Charge,Incentive Credit,Invoice Section,"
    "Invoice Type,Invoice Due Date"
)


def _row(tracking, service, zone, pickup, city, state, zip_code, charge, section,
         ref2="Dallas_AHB", sender_city="GARLAND", sender_state="TX", incentive="0"):
    return (
        f"C411H4,0000002H9494236,6/6/2026,{tracking},,145119,{ref2},,13,{zone},{service},{pickup},"
        f",AppyHour,2001 Platinum Street,{sender_city},{sender_state},75042,NAME,,STREET,"
        f"{city},{state},{zip_code},US,,{charge},{incentive},{section},Export,6/15/2026"
    )


def _parse(*rows):
    text = "\n".join([HEADER, *rows]) + "\n"
    return {s["tracking"]: s for s in sidb.parse_ups_csv_bytes(text.encode("latin-1"), "TEST")}


# ── the motivating burn ──────────────────────────────────────────────────────
# AHB_00356_UPS Shipping Breakdown_AHB_6-1-26.csv — both lines verbatim.
# Local stored $1.40 (last line wins), cloud stored $18.08 (first line wins). Truth $19.48.
def test_freight_plus_correction_sums_to_19_48():
    out = _parse(
        _row("1Z2H94940334864194", "Ground Residential", "4", "5/31/2026",
             "GLORIETA", "NM", "875357187", "18.08", "Outbound/Shipping API",
             ref2="AHB", sender_city="GARLAND"),
        _row("1Z2H94940334864194", "Ground", "4", "5/31/2026",
             "GLORIETA", "NM", "875357187", "1.4",
             "Adjustments & Other Charges/Shipping Charge Corrections",
             ref2="AHB", sender_city="GARLAND"),
    )
    assert len(out) == 1, "one row per tracking, not one per invoice line"
    s = out["1Z2H94940334864194"]
    assert s["cost"] == 19.48
    assert s["charge_lines"] == 2
    # metadata from the FREIGHT line, not the thin adjustment line
    assert s["service"] == "Ground Residential"


# ── three more real multi-line trackings, Invoice_000000C411H4286_071126.csv ──
def test_second_day_air_70_89_plus_11_88():
    out = _parse(
        _row("1ZC411H40210472165", "2nd Day Air Residential", "207", "07/06/2026",
             "KEY WEST", "FL", "330407136", "70.89", "Outbound/Shipping API"),
        _row("1ZC411H40210472165", "2nd Day Air", "207", "07/06/2026",
             "KEY WEST", "FL", "330407136", "11.88",
             "Adjustments & Other Charges/Shipping Charge Corrections"),
    )
    assert out["1ZC411H40210472165"]["cost"] == 82.77


def test_second_day_air_67_58_plus_8_48():
    out = _parse(
        _row("1ZC411H40226513279", "2nd Day Air Residential", "206", "07/06/2026",
             "LAFAYETTE HILL", "PA", "194442223", "67.58", "Outbound/Shipping API"),
        _row("1ZC411H40226513279", "2nd Day Air", "206", "07/06/2026",
             "LAFAYETTE HILL", "PA", "194442223", "8.48",
             "Adjustments & Other Charges/Shipping Charge Corrections"),
    )
    assert out["1ZC411H40226513279"]["cost"] == 76.06


def test_address_correction_line_has_blank_metadata_and_does_not_win():
    """The address-correction line carries NO city/state/zip and a different pickup date.
    Real rows: 36.05 Ground Residential (WILLIAMS AZ 86046, 07/06) + 5.55 Address Correction
    (blank dest, 07/10). Truth $41.60 with the freight line's destination intact."""
    out = _parse(
        _row("1ZC411H40318992015", "Ground Residential", "005", "07/06/2026",
             "WILLIAMS", "AZ", "86046", "36.05", "Outbound/Shipping API"),
        _row("1ZC411H40318992015", "Ground", "000", "07/10/2026",
             "", "", "", "5.55", "Adjustments & Other Charges/Address Corrections",
             sender_city="", sender_state=""),
    )
    s = out["1ZC411H40318992015"]
    assert s["cost"] == 41.60
    assert (s["city"], s["state"], s["zip_code"]) == ("WILLIAMS", "AZ", "86046")
    assert s["ship_date"] == "2026-07-06"
    assert s["zone"] == "005"


# ── credit / negative adjustment: real, from Invoice_000000C411H4186_050226.csv ──
def test_negative_adjustment_subtracts():
    out = _parse(
        _row("1ZC411H40311605473", "Ground Residential", "004", "04/27/2026",
             "LAS VEGAS", "NM", "877014986", "14.92", "Outbound/Shipping API"),
        _row("1ZC411H40311605473", "Commercial", "004", "04/27/2026",
             "", "", "", "-0.61",
             "Adjustments & Other Charges/Residential/Commercial Adjustments",
             sender_city="", sender_state=""),
    )
    s = out["1ZC411H40311605473"]
    assert s["cost"] == 14.31, "a credit must SUBTRACT, never be dropped or abs()'d"
    assert s["service"] == "Ground Residential"


# ── regression guard: single-line trackings must be byte-for-byte unchanged ──
def test_single_line_tracking_unchanged():
    """First data row of AHB_00356, verbatim. The overwhelming majority of trackings are
    single-line; the aggregation must not perturb them."""
    out = _parse(
        _row("1Z2H94940300064942", "Ground Residential", "4", "5/31/2026",
             "ODESSA", "TX", "797628415", "11.58", "Outbound/Shipping API", ref2="AHB"),
    )
    s = out["1Z2H94940300064942"]
    assert s["cost"] == 11.58
    assert s["charge_lines"] == 1
    assert s["carrier"] == "UPS"
    assert s["service"] == "Ground Residential"
    assert s["state"] == "TX"
    assert s["zip_code"] == "79762"
    assert s["ship_date"] == "2026-05-31"
    assert s["invoice_id"] == "TEST"


def test_all_adjustment_tracking_keeps_its_only_lines_metadata():
    """A tracking whose ONLY line is an adjustment still parses (falls back to line 1)."""
    out = _parse(
        _row("1ZC411H40999999999", "Ground", "004", "04/27/2026",
             "LAS VEGAS", "NM", "877014986", "3.25",
             "Adjustments & Other Charges/Shipping Charge Corrections"),
    )
    s = out["1ZC411H40999999999"]
    assert s["cost"] == 3.25
    assert s["service"] == "Ground"


def test_shippingreports_headed_parser_agrees(tmp_path):
    """`ShippingReports/parsers/ups.py::_parse_header_csv` is the CLOUD's reader for the same
    dialect and carried the same one-row-per-line defect (it kept the FIRST line where local kept
    the LAST). Both readers must now produce the invoice's sum."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ShippingReports.parsers.ups import parse_ups_csv

    rows = [
        _row("1Z2H94940334864194", "Ground Residential", "4", "5/31/2026",
             "GLORIETA", "NM", "875357187", "18.08", "Outbound/Shipping API", ref2="AHB"),
        _row("1Z2H94940334864194", "Ground", "4", "5/31/2026",
             "GLORIETA", "NM", "875357187", "1.4",
             "Adjustments & Other Charges/Shipping Charge Corrections", ref2="AHB"),
        _row("1Z2H94940300064942", "Ground Residential", "4", "5/31/2026",
             "ODESSA", "TX", "797628415", "11.58", "Outbound/Shipping API", ref2="AHB"),
    ]
    p = tmp_path / "AHB_00356_UPS Shipping Breakdown_AHB_6-1-26.csv"
    p.write_text("\n".join([HEADER, *rows]) + "\n", encoding="latin-1")

    out = {s.tracking: s for s in parse_ups_csv(str(p))}
    assert len(out) == 2
    assert out["1Z2H94940334864194"].cost == 19.48
    assert out["1Z2H94940334864194"].service == "Ground Residential"
    assert out["1Z2H94940300064942"].cost == 11.58


def test_headerless_billing_dialect_still_aggregates():
    """`_parse_ups_billing_data` was already correct — the headed fix must not change it."""
    cols = ["" for _ in range(85)]
    cols[11], cols[13], cols[16] = "04/27/2026", "1ZC411H40301244122", "Dallas_AHB"
    cols[26], cols[33], cols[45] = "13", "003", "Ground Residential"
    cols[78], cols[79], cols[80] = "LAS VEGAS", "NM", "877014986"
    frt = list(cols); frt[52] = "14.13"
    acc = list(cols); acc[45] = "Delivery Area Surcharge - Extended"; acc[52] = "3.45"
    text = ",".join(frt) + "\n" + ",".join(acc) + "\n"
    out = sidb.parse_ups_csv_bytes(text.encode("latin-1"), "TEST")
    assert len(out) == 1
    assert round(out[0]["cost"], 2) == 17.58
