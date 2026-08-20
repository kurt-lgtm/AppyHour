"""Load 4 UPS invoices directly into canonical shipments.db with billed_weight extracted.

Parses raw UPS Billing Detail CSV (250 cols).
- col 26: billed weight (lb)
- col 28: actual weight (lb)
- col 30/34/44: row type (PKG/SHP/003 = main freight)
- col 52: charge amount
- Other cols per common.py / parsers/ups.py

Writes to shipments table, schema:
(id, invoice_id, tracking, carrier, service, hub, state, zip_code, city, zone,
 cost, weight, ship_date, delivery_date, transit_days, ship_dow, source_file,
 box_type, cohort_key, subcohort, acct, is_internal)

🔴 POSITIONAL BY NECESSITY — validate every row (2026-07-30):
Unlike FedEx, the UPS Detailed Billing v2.1 file has NO header row, so column indices are
the only addressing available; this cannot be made header-driven. That makes it exactly
the format where a ragged/shifted row writes a city into `state` and a state into
`zip_code` with nothing to complain — one such row (`Invoice_000000C411H4315_080225.csv`,
`state='BROOKLYN'`, `zip_code='NY'`) sat in the canonical DB from May until the July MySQL
migration surfaced it. MySQL's `varchar(8)` guard is gone (widened to varchar(32) so the
ETL can mirror verbatim), so the `appyhour_lib.shipment_validate` gate below is the only
thing standing between a malformed row and the DB. Rejects are dropped and COUNTED, never
written and never silent.
"""
import csv
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour")
from appyhour_lib.shipment_validate import partition, report_rejects  # noqa: E402

CSV_FILES = [
    r"C:\Users\Work\Downloads\9be2f0d4-0d80-4e76-ac74-133c620a1654\Invoice_000000C411H4186_050226.csv",
    r"C:\Users\Work\Downloads\9be2f0d4-0d80-4e76-ac74-133c620a1654\Invoice_000000C411H4196_050926.csv",
    r"C:\Users\Work\Downloads\9be2f0d4-0d80-4e76-ac74-133c620a1654\Invoice_000000C411H4206_051626.csv",
    r"C:\Users\Work\Downloads\9be2f0d4-0d80-4e76-ac74-133c620a1654\Invoice_000000C411H4216_052326.csv",
]

DB = (os.environ.get("APPYHOUR_DB_PATH") or (r"C:\AppyHourData\shipping.db" if os.path.exists(r"C:\AppyHourData\shipping.db") else os.path.expandvars(r"%APPDATA%\AppyHour\shipping.db")))


class WrongDialect(RuntimeError):
    """This file is not UPS Detailed Billing v2.1. Raised instead of returning nothing."""


def parse_invoice(filepath):
    """🔴 THIS READER HANDLES ONE DIALECT: UPS Detailed Billing v2.1 — no header, 250+ positional
    fields. It is NOT the reader for the 32-column headered export.

    The burn (2026-08-20): `if len(r) < 53: continue` used to skip every row of a headered export
    and return an EMPTY list with no error and exit code 0. Handed the wrong dialect, this loader
    reported a successful load of nothing straight into the engine's lane economics. Parsing was
    never the problem — `ShippingReports/parsers/ups.py` reads BOTH dialects — the wrong reader was
    simply wired to the file.

    A width check that does not match now RAISES. Skipping rows you do not understand is how an
    entire file becomes a silent no-op (INVOICE_INGEST_RULES §2).
    """
    rows_out = []
    seen, too_narrow = 0, 0
    with open(filepath, encoding="utf-8-sig", errors="replace") as f:
        for r in csv.reader(f):
            seen += 1
            if len(r) < 53:
                too_narrow += 1
                continue
            # Main freight rows only
            if r[30] != "PKG" or r[34] != "SHP" or r[44] != "003":
                continue
            try:
                tracking = r[13]
                billed_wt = float(r[26]) if r[26] else None
                actual_wt = float(r[28]) if r[28] else None
                service = r[45]
                state = r[79]
                zip_code = r[80][:5] if r[80] else None
                city = r[78]
                zone = r[37]
                cost = float(r[52]) if r[52] else 0.0
                ship_date = r[11]
                invoice_id = r[5]
                hub = r[16].split("_")[0] if r[16] else None
                acct = r[1]
                # Weight = billed (chargeable)
                weight = billed_wt if billed_wt and billed_wt > 0 else actual_wt
                rows_out.append({
                    "invoice_id": invoice_id,
                    "tracking": tracking,
                    "carrier": "UPS",
                    "service": service,
                    "hub": hub,
                    "state": state,
                    "zip_code": zip_code,
                    "city": city,
                    "zone": zone,
                    "cost": cost,
                    "weight": weight,
                    "ship_date": ship_date,
                    "source_file": Path(filepath).name,
                    "acct": acct,
                })
            except (ValueError, IndexError):
                continue
    # 🔴 Rows written is the only success signal. A file whose every line was too narrow is the
    # WRONG DIALECT, not an empty invoice — say so loudly instead of returning [] (§2).
    if seen and too_narrow == seen:
        raise WrongDialect(
            f"{Path(filepath).name}: all {seen} lines are narrower than 53 fields, so this is NOT "
            f"UPS Detailed Billing v2.1. It is most likely the 32-column headered export — read it "
            f"with ShippingReports/parsers/ups.py::parse_ups_csv, which auto-detects both dialects. "
            f"Refusing to report a successful load of zero rows.")
    if seen and not rows_out:
        raise WrongDialect(
            f"{Path(filepath).name}: {seen} lines parsed but ZERO freight rows matched "
            f"(PKG/SHP/003). Refusing to report a successful empty load — check the dialect.")
    return rows_out


def main():
    all_rows = []
    for f in CSV_FILES:
        rows = parse_invoice(f)
        print(f"{Path(f).name}: {len(rows)} main-freight rows")
        all_rows.extend(rows)
    print(f"\nTotal: {len(all_rows)} rows")

    # Connect to canonical DB
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # Dedup within the parsed batch (one tracking can have multiple charge lines)
    seen = {}
    for r in all_rows:
        t = r["tracking"]
        if t not in seen:
            seen[t] = r
        else:
            # Keep row with non-null weight + larger cost
            ex = seen[t]
            if (r["weight"] and not ex["weight"]) or (r["cost"] > ex["cost"]):
                seen[t] = r
    batch = list(seen.values())
    print(f"After in-batch dedup: {len(batch)} unique trackings")

    # 🔴 Validation gate — a shifted positional row never reaches the DB.
    batch, rejected = partition(batch)
    print(report_rejects(rejected, label="UPS invoice "))

    # Check existing tracking IDs to skip duplicates already in DB
    cur.execute("SELECT tracking FROM shipments WHERE carrier='UPS'")
    existing = {r[0] for r in cur.fetchall()}
    new_rows = [r for r in batch if r["tracking"] not in existing]
    print(f"New (not in DB): {len(new_rows)}, already in DB: {len(batch) - len(new_rows)}")

    if not new_rows:
        print("Nothing to insert.")
        return

    # Insert — match schema (18 cols actually, but core 14 we have)
    insert_sql = """
    INSERT INTO shipments
    (invoice_id, tracking, carrier, service, hub, state, zip_code, city, zone,
     cost, weight, ship_date, source_file, acct, is_internal)
    VALUES (:invoice_id, :tracking, :carrier, :service, :hub, :state, :zip_code,
            :city, :zone, :cost, :weight, :ship_date, :source_file, :acct, 0)
    """
    cur.executemany(insert_sql, new_rows)
    conn.commit()
    print(f"Inserted {cur.rowcount} rows into shipments.")
    conn.close()


if __name__ == "__main__":
    main()
