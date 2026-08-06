"""Load FedEx invoice detail CSVs (FedEx Billing Online extract) into canonical shipments.db.

🔴 GOTCHA THIS FILE EXISTS TO NOT REPEAT (2026-07-30):
This script used to map columns POSITIONALLY off a hand-written index list
(`r[37]` = "Recipient State", `r[63]` = "Zone Code", ...). The real extract is 210
columns wide and carries a `Service Packaging` column at index 27, so every index at
or past 27 in that list was one short. Result: `state` got the CITY, `zip_code` got the
STATE, `zone` got the COUNTRY, and the real ZIP was thrown away — 1,368 FedEx rows
across the four May invoices below, plus a sibling bug in
`load_ups_invoices_with_weight.py`. sqlite is typeless so it accepted all of it silently;
it surfaced only in July when MySQL's `varchar(8)` on `shipments.state` rejected the
oversized values during the managed-DB migration.

  - NEVER index invoice columns by position. FedEx reorders/inserts columns between
    extract versions without renaming anything. Map by header name (`csv.DictReader`).
  - Every row goes through `appyhour_lib.shipment_validate` before the INSERT; a row whose
    `state` is not 2 letters or whose `zip_code` is not 5 digits is REJECTED, not written,
    and the reject count is printed. MySQL no longer catches this (column widened to
    varchar(32) so the ETL can mirror verbatim) — this gate is the only net left.

Filter: only rows with a tracking ID (skips pickup/fuel-only billing events).
Dedup by tracking within the batch; skip trackings already in DB.
"""
import csv
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour")
from appyhour_lib.shipment_validate import partition, report_rejects  # noqa: E402

CSV_FILES = [
    r"C:\Users\Work\Downloads\2026-05-18 10-32 Auto FedExInv 203738113.CSV",
    r"C:\Users\Work\Downloads\2026-05-19 10-59 Auto FedExInv 203738113.CSV",
    r"C:\Users\Work\Downloads\2026-05-25 10-55 Auto FedExInv 203738113.CSV",
    r"C:\Users\Work\Downloads\2026-05-25 11-58 Auto FedExInv 203738113.CSV",
]

DB = (os.environ.get("APPYHOUR_DB_PATH") or (r"C:\AppyHourData\shipping.db" if os.path.exists(r"C:\AppyHourData\shipping.db") else os.path.expandvars(r"%APPDATA%\AppyHour\shipping.db")))

# Header names, not indices. Every one of these is verified present before a file is parsed.
REQUIRED_HEADERS = (
    "Invoice Number",
    "Express or Ground Tracking ID",
    "Net Charge Amount",
    "Service Type",
    "Shipment Date",
    "Actual Weight Amount",
    "Rated Weight Amount",
    "Recipient City",
    "Recipient State",
    "Recipient Zip Code",
    "Zone Code",
)


def _f(x):
    try:
        return float(x) if x and x.strip() else None
    except (ValueError, AttributeError):
        return None


def _date(s):
    s = (s or "").strip()
    if len(s) != 8:
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _s(row, key):
    return (row.get(key) or "").strip()


def parse_invoice(filepath):
    """Header-driven parse. Raises KeyError if the extract lacks an expected column,
    rather than silently reading whatever happens to sit at some index."""
    rows = []
    with open(filepath, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        missing = [h for h in REQUIRED_HEADERS if h not in (reader.fieldnames or [])]
        if missing:
            raise KeyError(f"{Path(filepath).name}: missing expected column(s): {missing}")
        for r in reader:
            tracking = _s(r, "Express or Ground Tracking ID")
            if not tracking:
                continue
            actual_wt = _f(r.get("Actual Weight Amount"))
            rated_wt = _f(r.get("Rated Weight Amount"))
            # Zip arrives as 85614-1432-65 (zip5-plus4-suffix) — keep zip5 only.
            zip5 = _s(r, "Recipient Zip Code").split("-")[0][:5]
            rows.append({
                "invoice_id": _s(r, "Invoice Number"),
                "tracking": tracking,
                "carrier": "FedEx",
                "service": _s(r, "Service Type") or _s(r, "Ground Service"),
                "hub": None,
                "state": _s(r, "Recipient State").upper() or None,
                "zip_code": zip5 or None,
                "city": _s(r, "Recipient City") or None,
                "zone": _s(r, "Zone Code") or None,
                "cost": _f(r.get("Net Charge Amount")) or 0.0,
                "weight": rated_wt or actual_wt,
                "actual_weight": actual_wt,
                "rated_weight": rated_wt,
                "dim_l": _f(r.get("Dim Length")),
                "dim_w": _f(r.get("Dim Width")),
                "dim_h": _f(r.get("Dim Height")),
                "dim_div": _f(r.get("Dim Divisor")),
                "ship_date": _date(r.get("Shipment Date")),
                "source_file": Path(filepath).name,
                "acct": _s(r, "Bill to Account Number") or "203738113",
            })
    return rows


def main():
    all_rows = []
    for f in CSV_FILES:
        rows = parse_invoice(f)
        print(f"{Path(f).name}: {len(rows)} shipment rows")
        all_rows.extend(rows)
    print(f"\nTotal: {len(all_rows)} shipment rows across files")

    # Dedup within batch by tracking — keep row with non-zero cost + non-null weight
    seen = {}
    for r in all_rows:
        t = r["tracking"]
        if t not in seen:
            seen[t] = r
        else:
            ex = seen[t]
            if (r["cost"] > ex["cost"]) or (r["weight"] and not ex["weight"]):
                seen[t] = r
    batch = list(seen.values())
    print(f"After in-batch dedup: {len(batch)} unique trackings")

    # 🔴 Validation gate — reject malformed rows instead of writing them.
    batch, rejected = partition(batch)
    print(report_rejects(rejected, label="FedEx invoice "))

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT tracking FROM shipments WHERE carrier='FedEx'")
    existing = {r[0] for r in cur.fetchall()}
    new_rows = [r for r in batch if r["tracking"] not in existing]
    print(f"New: {len(new_rows)}, already in DB: {len(batch) - len(new_rows)}")

    # Spot check before insert
    if new_rows:
        sample = new_rows[0]
        print(f"\nSample new row: tracking={sample['tracking']}, "
              f"city={sample['city']}, state={sample['state']}, zip={sample['zip_code']}, "
              f"zone={sample['zone']}, cost=${sample['cost']}, actual_wt={sample['actual_weight']}, "
              f"rated_wt={sample['rated_weight']}, dim={sample['dim_l']}x{sample['dim_w']}x{sample['dim_h']}/{sample['dim_div']}")

    if new_rows:
        insert_sql = """
        INSERT INTO shipments
        (invoice_id, tracking, carrier, service, hub, state, zip_code, city, zone,
         cost, weight, ship_date, source_file, acct, is_internal)
        VALUES (:invoice_id, :tracking, :carrier, :service, :hub, :state, :zip_code,
                :city, :zone, :cost, :weight, :ship_date, :source_file, :acct, 0)
        """
        cur.executemany(insert_sql, new_rows)
        conn.commit()
        print(f"\nInserted {cur.rowcount} rows.")

    # Dim-billing audit: how many rated_weight > actual_weight?
    dim_billed = sum(1 for r in batch if r["rated_weight"] and r["actual_weight"] and r["rated_weight"] > r["actual_weight"])
    same_wt = sum(1 for r in batch if r["rated_weight"] and r["actual_weight"] and r["rated_weight"] == r["actual_weight"])
    rated_less = sum(1 for r in batch if r["rated_weight"] and r["actual_weight"] and r["rated_weight"] < r["actual_weight"])
    has_dim = sum(1 for r in batch if r["dim_l"] and r["dim_w"] and r["dim_h"])
    print("\n=== Dim-billing audit (batch) ===")
    print(f"Rows with rated > actual (dim-billed): {dim_billed}")
    print(f"Rows with rated == actual:             {same_wt}")
    print(f"Rows with rated < actual (rare):       {rated_less}")
    print(f"Rows with dim data populated:          {has_dim}")

    conn.close()


if __name__ == "__main__":
    main()
