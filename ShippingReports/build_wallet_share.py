"""Build carrier wallet-share table for FedEx pitch (Orlando).

Reads output/shipments.db (canonical cross-carrier DB — has Veho).
Groups by Monday-of-ship-week, returns last N cohorts.

Usage:
    python build_wallet_share.py [--cohorts N] [--out PATH]

Output: prints markdown table + writes _outputs/reports/FedEx-wallet-share-Orlando.docx
"""
import argparse
import os
import re
import sqlite3
from datetime import datetime, timedelta

FEDEX_DIRECT_113 = re.compile(r'AHB_5\d{6,}_|FedEx_invoice_acct113_', re.IGNORECASE)

# M1 coldchain refactor (2026-06-11): repointed from the retired output/shipments.db
# build artifact to the canonical APPDATA DB (column name there is zip_code, not zip).
DB = os.path.join(os.environ["APPDATA"], "AppyHour", "shipping.db")
OUT_DEFAULT = r"C:\Users\Work\Claude Projects\_outputs\reports\FedEx-wallet-share-Orlando.docx"


def monday(date_str: str) -> str:
    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    return (d - timedelta(days=d.weekday())).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cohorts', type=int, default=4, help='last N complete cohorts')
    ap.add_argument('--out', default=OUT_DEFAULT)
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT carrier, ship_date, cost, source_file FROM shipments "
        "WHERE ship_date IS NOT NULL AND ship_date >= date('now', '-90 days')"
    ).fetchall()

    by_cohort: dict = {}
    for carrier, ship_date, cost, src in rows:
        ck = '_SHIP_' + monday(ship_date)
        acct = ''
        if carrier == 'FedEx':
            acct = ' -113' if FEDEX_DIRECT_113.search(src or '') else ' -911'
        key = (ck, carrier + acct)
        d = by_cohort.setdefault(key, {'pkgs': 0, 'spend': 0.0})
        d['pkgs'] += 1
        d['spend'] += float(cost or 0)

    cohort_keys = sorted({k[0] for k in by_cohort}, reverse=True)[:args.cohorts]
    cohort_keys = sorted(cohort_keys)

    totals: dict = {}
    for (ck, ca), v in by_cohort.items():
        if ck not in cohort_keys:
            continue
        t = totals.setdefault(ca, {'pkgs': 0, 'spend': 0.0})
        t['pkgs'] += v['pkgs']
        t['spend'] += v['spend']

    total_pkgs = sum(t['pkgs'] for t in totals.values())
    total_spend = sum(t['spend'] for t in totals.values())

    print(f"\nCohorts: {', '.join(cohort_keys)}")
    print(f"\n{'Carrier':<22} {'Pkgs':>8} {'Pkg %':>8} {'Spend':>12} {'$ %':>8}")
    print('-' * 62)
    for ca in sorted(totals, key=lambda c: -totals[c]['pkgs']):
        t = totals[ca]
        print(f"{ca:<22} {t['pkgs']:>8} {t['pkgs']/total_pkgs*100:>7.1f}% "
              f"${t['spend']:>10,.0f} {t['spend']/total_spend*100:>7.1f}%")
    print('-' * 62)
    print(f"{'TOTAL':<22} {total_pkgs:>8}            ${total_spend:>10,.0f}")

    fedex_total = {'pkgs': 0, 'spend': 0.0}
    for ca, t in totals.items():
        if ca.startswith('FedEx'):
            fedex_total['pkgs'] += t['pkgs']
            fedex_total['spend'] += t['spend']
    print(f"\nFedEx combined: {fedex_total['pkgs']} pkgs "
          f"({fedex_total['pkgs']/total_pkgs*100:.1f}%), "
          f"${fedex_total['spend']:,.0f} ({fedex_total['spend']/total_spend*100:.1f}%)")

    return cohort_keys, totals, total_pkgs, total_spend


if __name__ == '__main__':
    main()
