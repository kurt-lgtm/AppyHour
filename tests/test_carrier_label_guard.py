"""Carrier-label guard — a tracking number's shape may not contradict its carrier label.

Pins the real 2026-09-07 case: `AHB_00350_UPS Shipping Breakdown_AHB_5-25-26.csv` reached the
Gmail ingest, whose carrier rule was `.csv` -> OnTrac by file EXTENSION, and 61 `1Z...` UPS
parcels were written with carrier='OnTrac'. $828.10 published on the OnTrac rows of Carrier Mix.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "GelPackCalculator"))
import shipping_invoice_db as sidb  # noqa: E402


# ── the filename token is the authority, not the extension ──────────────────

@pytest.mark.parametrize("filename,expected", [
    # 🔴 the burn: a UPS breakdown that arrives as .csv
    ("AHB_00350_UPS Shipping Breakdown_AHB_5-25-26.csv", "UPS"),
    ("AHB_00356_UPS Shipping Breakdown_AHB_6-1-26.csv", "UPS"),
    ("AHB_00266_OnTrac Shipping Breakdown_AHB_3-2-26.csv", "OnTrac"),
    ("AHB_00248_FedEx Shipping Breakdown_AHB_2-9-26.XLSX", "FedEx"),
    # LaserShip is OnTrac under its old name (canon.normalize_carrier folds it)
    ("AHB_00301_LaserShip Shipping Breakdown_AHB_4-6-26.csv", "OnTrac"),
    # no carrier token -> historical extension fallback
    ("AHB_00999_Shipping Breakdown_AHB_9-1-26.csv", "OnTrac"),
    ("AHB_00999_Shipping Breakdown_AHB_9-1-26.xlsx", "FedEx"),
    # not an invoice breakdown at all
    ("statement.pdf", None),
    ("random_notes.csv", None),
])
def test_carrier_from_filename(filename, expected):
    assert sidb.carrier_from_filename(filename) == expected


# ── tracking shapes, derived from the shipments table (never assumed) ────────

@pytest.mark.parametrize("tracking,expected", [
    ("1Z2H94940204373273", "UPS"),      # one of the 61 mislabelled rows
    ("1Z2H94940334864194", "UPS"),
    ("1LSD1S0000CDVK123", None),        # wrong length -> no opinion, never a guess
    ("1LSD1S0000CDVK", None),
    ("1LS123456789012", "ONTRAC"),
    ("123456789012", "FEDEX"),
    ("770123456789", "FEDEX"),
    ("ABCDEF12345678", None),           # Veho 14-char alnum -> deliberately no rule
    ("TRCK NMBR NOT PROVIDED", None),
    ("", None),
    (None, None),
])
def test_tracking_carrier_class(tracking, expected):
    assert sidb.tracking_carrier_class(tracking) == expected


# ── the guard refuses; it never auto-corrects ───────────────────────────────

def _row(tracking, carrier):
    return {"tracking": tracking, "carrier": carrier, "cost": 1.0, "ship_date": "2026-05-25"}


def test_the_real_burn_is_refused():
    rows = [_row("1Z2H94940204373273", "OnTrac"), _row("1Z2H94940300972407", "OnTrac")]
    with pytest.raises(sidb.CarrierTrackingMismatch) as e:
        sidb.assert_carrier_labels(rows, "AHB_00350_UPS Shipping Breakdown_AHB_5-25-26.csv")
    msg = str(e.value)
    assert "REFUSED 2 of 2 rows" in msg
    assert "AHB_00350" in msg
    assert "'OnTrac'" in msg and "UPS" in msg


def test_guard_does_not_mutate_the_rows():
    """🔴 Refuse-and-report: an auto-corrected row would hide the ingest bug that produced it."""
    rows = [_row("1Z2H94940204373273", "OnTrac")]
    with pytest.raises(sidb.CarrierTrackingMismatch):
        sidb.assert_carrier_labels(rows, "x.csv")
    assert rows[0]["carrier"] == "OnTrac"


def test_lasership_labelled_ontrac_row_is_not_a_defect():
    """canon.normalize_carrier folds LaserShip into OnTrac — both spellings pass."""
    rows = [_row("1LS123456789012", "LaserShip"), _row("1LS123456789013", "OnTrac")]
    assert sidb.check_carrier_labels(rows) == []
    sidb.assert_carrier_labels(rows, "ontrac.csv")


def test_unknown_shapes_and_correct_labels_pass():
    rows = [
        _row("1Z2H94940204373273", "UPS"),
        _row("123456789012", "FedEx"),
        _row("ABCDEF12345678", "Veho"),          # no rule for Veho -> no opinion
        _row("TRCK NMBR NOT PROVIDED", "UPS"),   # real row in shipments
    ]
    assert sidb.check_carrier_labels(rows) == []


def test_store_shipments_writes_nothing_when_a_row_contradicts():
    """The refusal is a pre-flight: a contradicting file must write NO rows, not some."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE shipments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, tracking TEXT UNIQUE,
        carrier TEXT, service TEXT, hub TEXT, state TEXT, zip_code TEXT, city TEXT,
        zone TEXT, cost REAL, weight REAL, ship_date TEXT, delivery_date TEXT,
        transit_days INTEGER, ship_dow TEXT, source_file TEXT, box_type TEXT, acct TEXT)""")
    conn.execute("""CREATE TABLE shipment_dims (
        tracking TEXT PRIMARY KEY, actual_weight REAL, dim_l REAL, dim_w REAL,
        dim_h REAL, dim_factor REAL)""")

    rows = [_row("1Z2H94940300972407", "UPS"),      # good row, listed FIRST
            _row("1Z2H94940204373273", "OnTrac")]   # the contradiction
    with pytest.raises(sidb.CarrierTrackingMismatch):
        sidb.store_shipments(conn, rows, "AHB_00350_UPS Shipping Breakdown_AHB_5-25-26.csv")
    assert conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0] == 0


def test_clean_batch_still_stores():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE shipments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, tracking TEXT UNIQUE,
        carrier TEXT, service TEXT, hub TEXT, state TEXT, zip_code TEXT, city TEXT,
        zone TEXT, cost REAL, weight REAL, ship_date TEXT, delivery_date TEXT,
        transit_days INTEGER, ship_dow TEXT, source_file TEXT, box_type TEXT, acct TEXT)""")
    conn.execute("""CREATE TABLE shipment_dims (
        tracking TEXT PRIMARY KEY, actual_weight REAL, dim_l REAL, dim_w REAL,
        dim_h REAL, dim_factor REAL)""")
    sidb.store_shipments(conn, [_row("1Z2H94940300972407", "UPS")], "ups.csv")
    assert conn.execute("SELECT carrier FROM shipments").fetchone()[0] == "UPS"
