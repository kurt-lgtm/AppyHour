"""Build SQLite shipments DB from output/shipments.json.

Usage:
    python build_db.py [--input output/shipments.json] [--output output/shipments.db]
"""

import argparse
import json
import os
import sqlite3
import sys


INTERNAL_ZIPS = ('01801',)  # Woburn HQ — internal samples, not customer shipments

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    tracking       TEXT NOT NULL,
    carrier        TEXT NOT NULL,
    service        TEXT,
    hub            TEXT,
    state          TEXT,
    zip            TEXT,
    city           TEXT,
    zone           TEXT,
    cost           REAL,
    ship_date      TEXT,
    delivery_date  TEXT,
    transit_days   INTEGER,
    ship_dow       TEXT,
    invoice_id     TEXT,
    source_file    TEXT,
    order_id       TEXT,
    order_name     TEXT,
    ship_tag       TEXT,
    weight         REAL,         -- billed/chargeable weight (lb)
    actual_weight  REAL,         -- scale weight if reported separately
    dim_l          REAL,         -- outer length (in)
    dim_w          REAL,         -- outer width (in)
    dim_h          REAL,         -- outer height (in)
    dim_factor     REAL,         -- carrier dim divisor (OnTrac 225, FedEx 139/194, UPS 139)
    PRIMARY KEY (carrier, tracking)
);
CREATE INDEX IF NOT EXISTS idx_ship_date     ON shipments(ship_date);
CREATE INDEX IF NOT EXISTS idx_hub           ON shipments(hub);
CREATE INDEX IF NOT EXISTS idx_state         ON shipments(state);
CREATE INDEX IF NOT EXISTS idx_zip           ON shipments(zip);
CREATE INDEX IF NOT EXISTS idx_carrier_date  ON shipments(carrier, ship_date);
CREATE INDEX IF NOT EXISTS idx_order_id      ON shipments(order_id);
CREATE INDEX IF NOT EXISTS idx_order_name    ON shipments(order_name);
CREATE INDEX IF NOT EXISTS idx_ship_tag      ON shipments(ship_tag);
CREATE INDEX IF NOT EXISTS idx_tracking      ON shipments(tracking);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', default='output/shipments.json')
    ap.add_argument('--output', default='output/shipments.db')
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"Input not found: {args.input}")
        sys.exit(1)

    with open(args.input) as f:
        data = json.load(f)

    shipments = data.get('shipments', [])
    # Filter internal/HQ shipments (Woburn 01801 = headquarters samples, not customer)
    before = len(shipments)
    shipments = [s for s in shipments if not (s.get('zip') or '').startswith(INTERNAL_ZIPS)]
    if before != len(shipments):
        print(f"Filtered {before - len(shipments)} internal shipments (zips: {INTERNAL_ZIPS})")
    # Repair Veho 4-6-26 cohort: corrupt xlsx lost Tendered Timestamp; backfill ship_date
    # from filename (Created Timestamps in repaired file all show April 10, 2026).
    veho_4_06_patched = 0
    for s in shipments:
        if s.get('carrier') == 'Veho' and 'AHB_00299' in (s.get('source_file') or '') and not s.get('ship_date'):
            s['ship_date'] = '2026-04-11'  # Sat pickup, matches Veho pre-April pattern (3/14,3/21,3/28,4/04 all Saturdays)
            veho_4_06_patched += 1
    if veho_4_06_patched:
        print(f"Patched ship_date for {veho_4_06_patched} Veho 4-6 rows (corrupt-file recovery)")
    if os.path.exists(args.output):
        os.remove(args.output)

    conn = sqlite3.connect(args.output)
    conn.executescript(SCHEMA)

    rows = [
        (
            s.get('tracking'), s.get('carrier'), s.get('service'),
            s.get('hub'), s.get('state'), s.get('zip'),
            s.get('city'), s.get('zone'), s.get('cost'),
            s.get('ship_date'), s.get('delivery_date'),
            s.get('transit_days'), s.get('ship_dow'),
            s.get('invoice_id'), s.get('source_file'),
            s.get('order_id'), s.get('order_name'), s.get('ship_tag'),
            s.get('weight'), s.get('actual_weight'),
            s.get('dim_l'), s.get('dim_w'), s.get('dim_h'),
            s.get('dim_factor'),
        )
        for s in shipments
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO shipments VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('generated', ?)", (data.get('generated'),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('source', ?)", (os.path.abspath(args.input),))
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    by_carrier = conn.execute(
        "SELECT carrier, COUNT(*) FROM shipments GROUP BY carrier ORDER BY 2 DESC"
    ).fetchall()
    conn.close()

    print(f"Wrote {args.output}: {count} shipments")
    for c, n in by_carrier:
        print(f"  {c}: {n}")


if __name__ == '__main__':
    main()
