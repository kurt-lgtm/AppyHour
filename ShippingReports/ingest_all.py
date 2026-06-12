"""Solid one-command shipping ingest.

User drops FedEx-113 + UPS files into GelPackCalculator/Invoices/inbox/.
This script handles everything else.

Usage:
    python ingest_all.py [--skip-imap] [--skip-shopify] [--skip-parcelpanel] [--cohorts 8]
"""
import argparse
import glob
import os
import shutil
import subprocess
import sqlite3
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
GPC = os.path.abspath(os.path.join(ROOT, '..', 'GelPackCalculator'))
INBOX = os.path.join(GPC, 'Invoices', 'inbox')
INVOICES = os.path.join(GPC, 'Invoices')
FEDEX_INV = os.path.join(GPC, 'fedex_invoices')
DB = os.path.join(ROOT, 'output', 'shipments.db')
PY = sys.executable


def step(name):
    print(f"\n{'='*60}\n  {name}\n{'='*60}")


def run(cmd, cwd=None, check=True):
    """Run subprocess, stream output."""
    print(f"$ {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str))
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: {cmd}")


def move_inbox():
    step("1/9 Move user-dropped files from inbox/ → Invoices/")
    os.makedirs(INBOX, exist_ok=True)
    moved = 0
    for src in glob.glob(os.path.join(INBOX, '*')):
        if not os.path.isfile(src):
            continue
        name = os.path.basename(src)
        dst = os.path.join(INVOICES, name)
        if os.path.exists(dst):
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            base, ext = os.path.splitext(name)
            dst = os.path.join(INVOICES, f"{base}.{ts}{ext}")
        shutil.move(src, dst)
        print(f"  moved: {name}")
        moved += 1
    print(f"  {moved} file(s) moved")


def imap_pull():
    step("2/9 IMAP pull (Veho, OnTrac, FedEx-911)")
    for script in ['download_veho_imap.py', 'download_ontrac_imap.py', 'download_fedex_imap.py']:
        try:
            run([PY, os.path.join(GPC, script)], cwd=GPC)
        except SystemExit as e:
            print(f"  WARN: {script} failed — continuing. {e}")


def repair_xlsx():
    step("3/9 Repair corrupt xlsx (Excel COM)")
    repair_script = os.path.join(ROOT, 'repair_xlsx.py')
    if os.path.exists(repair_script):
        run([PY, repair_script])
    else:
        print("  no repair_xlsx.py — skipping")


def parse_files():
    step("4/9 Parse Invoices/ + fedex_invoices/")
    run([PY, 'ingest.py', '--invoice-dir', INVOICES, '--output', 'output/ship_invoices.json'], cwd=ROOT)
    run([PY, 'ingest.py', '--invoice-dir', FEDEX_INV, '--output', 'output/ship_fedex_inv.json'], cwd=ROOT)


def merge_build():
    step("5/9 Merge JSON + build DB")
    run([PY, 'merge_jsons.py', 'output/shipments.json', 'output/ship_invoices.json', 'output/ship_fedex_inv.json'], cwd=ROOT)
    run([PY, 'build_db.py'], cwd=ROOT)


def backfill_cohort():
    step("6/9 Backfill cohort_key + acct columns")
    backfill_script = os.path.join(ROOT, 'backfill_cohort.py')
    if os.path.exists(backfill_script):
        run([PY, backfill_script])
    else:
        print("  no backfill_cohort.py — skipping (will compute at query time)")


def shopify_sync(cohorts: int):
    step(f"7/9 Shopify sync last {cohorts} _SHIP cohorts")
    sync = os.path.join(GPC, 'sync_shopify_orders.py')
    if os.path.exists(sync):
        # ensure shopify_orders table exists in canonical DB
        ensure_shopify_table()
        try:
            run([PY, sync, '--db', DB, '--cohorts', str(cohorts)], cwd=GPC, check=False)
        except SystemExit as e:
            print(f"  WARN: {e}")
    else:
        print(f"  no sync_shopify_orders.py at {sync}")


def ensure_shopify_table():
    """Create shopify_orders table in canonical DB if missing."""
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS shopify_orders (
        order_id TEXT PRIMARY KEY,
        order_name TEXT,
        ship_tag TEXT,
        fulfillment_status TEXT,
        financial_status TEXT,
        cancelled_at TEXT,
        created_at TEXT,
        customer_email TEXT,
        ship_state TEXT,
        ship_zip TEXT,
        ship_city TEXT,
        total_price REAL,
        subtotal_price REAL,
        line_items_count INTEGER,
        tags_csv TEXT,
        raw_routing_tags TEXT,
        is_internal INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_so_ship_tag ON shopify_orders(ship_tag);
    CREATE INDEX IF NOT EXISTS idx_so_status ON shopify_orders(fulfillment_status);
    """)
    conn.commit()
    conn.close()


def parcelpanel_pull():
    step("8/9 ParcelPanel API pull (last 60d)")
    pp = os.path.join(ROOT, 'pull_parcelpanel.py')
    if os.path.exists(pp):
        run([PY, pp, '--days', '60'], cwd=ROOT, check=False)
    else:
        print(f"  no pull_parcelpanel.py — TODO: implement using PARCEL_PANEL_API_KEY from .env")


def verify():
    step("9/9 Verify (coverage + dup check)")
    conn = sqlite3.connect(DB)
    print("\n-- per-carrier coverage --")
    for r in conn.execute("SELECT carrier, COUNT(*), MIN(ship_date), MAX(ship_date) FROM shipments GROUP BY carrier"):
        print(f"  {r[0]:<8} {r[1]:>6}  {r[2]} -> {r[3]}")
    print("\n-- last 6 cohorts --")
    rows = conn.execute("""
        SELECT
            DATE(ship_date, 'weekday 0', '-6 days') AS cohort_mon,
            carrier, COUNT(*) AS n
        FROM shipments
        WHERE ship_date >= date('now', '-60 days')
        GROUP BY cohort_mon, carrier
        ORDER BY cohort_mon
    """).fetchall()
    by_c = {}
    for ck, ca, n in rows:
        by_c.setdefault(ck, {})[ca] = n
    for ck in sorted(by_c)[-6:]:
        d = ' '.join(f"{k}:{v}" for k, v in sorted(by_c[ck].items()))
        print(f"  {ck}  {d}")
    print("\n-- dup check --")
    dups = conn.execute("SELECT COUNT(*) FROM (SELECT carrier, tracking, COUNT(*) n FROM shipments GROUP BY carrier, tracking HAVING n > 1)").fetchone()[0]
    print(f"  {dups} duplicate (carrier, tracking) pairs (should be 0)")
    print("\n-- shopify_orders --")
    try:
        for r in conn.execute("SELECT ship_tag, COUNT(*) FROM shopify_orders GROUP BY ship_tag ORDER BY ship_tag DESC LIMIT 6"):
            print(f"  {r[0]}: {r[1]}")
    except Exception as e:
        print(f"  ERR: {e}")
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-imap', action='store_true')
    ap.add_argument('--skip-shopify', action='store_true')
    ap.add_argument('--skip-parcelpanel', action='store_true')
    ap.add_argument('--skip-repair', action='store_true')
    ap.add_argument('--cohorts', type=int, default=8)
    args = ap.parse_args()

    t0 = time.time()
    move_inbox()
    if not args.skip_imap:
        imap_pull()
    if not args.skip_repair:
        repair_xlsx()
    parse_files()
    merge_build()
    backfill_cohort()
    if not args.skip_shopify:
        shopify_sync(args.cohorts)
    if not args.skip_parcelpanel:
        parcelpanel_pull()
    verify()
    print(f"\nDONE in {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
