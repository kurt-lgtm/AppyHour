"""M1 cut-over gate: row-parity validation, live shipping.db vs shipping.refactor.db.

Compares (read-only, both DBs):
  1. per-table row counts
  2. per-(carrier,hub) shipment counts
  3. per-(carrier,hub,zip_code) transit_days distribution checksum
     (the engine's lane-history surface — any drift here changes routing)

PASS = identical except rows the refactor intentionally ADDED (new ingest while testing).
A refactor copy with FEWER rows anywhere = FAIL (data loss).

Usage: python scripts/validate_refactor_db.py  [--live PATH] [--copy PATH]
"""
import argparse
import hashlib
import os
import sqlite3

LIVE = (os.environ.get("APPYHOUR_DB_PATH") or (r"C:\AppyHourData\shipping.db" if os.path.exists(r"C:\AppyHourData\shipping.db") else os.path.join(os.environ["APPDATA"], "AppyHour", "shipping.db")))
COPY = os.path.join(os.environ["APPDATA"], "AppyHour", "shipping.refactor.db")


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def table_counts(c):
    tabs = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: c.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0] for t in tabs}


def lane_counts(c):
    return dict(((car, hub), n) for car, hub, n in c.execute(
        "SELECT carrier, COALESCE(hub,''), COUNT(*) FROM shipments GROUP BY 1,2"))


def lane_transit_checksums(c):
    """{(carrier,hub,zip_code): md5 of sorted transit_days list} — order-independent."""
    out = {}
    cur = c.execute("""SELECT carrier, COALESCE(hub,''), COALESCE(zip_code,''),
                              GROUP_CONCAT(transit_days) FROM
                       (SELECT carrier, hub, zip_code, transit_days FROM shipments
                        WHERE transit_days IS NOT NULL ORDER BY carrier, hub, zip_code, transit_days)
                       GROUP BY 1,2,3""")
    for car, hub, zc, days in cur:
        out[(car, hub, zc)] = hashlib.md5((days or "").encode()).hexdigest()[:12]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default=LIVE)
    ap.add_argument("--copy", default=COPY)
    args = ap.parse_args()
    lc, cc = ro(args.live), ro(args.copy)
    failures, notes = [], []

    lt, ct = table_counts(lc), table_counts(cc)
    for t in sorted(set(lt) | set(ct)):
        a, b = lt.get(t), ct.get(t)
        if a is None:
            notes.append(f"table {t}: NEW in copy ({b} rows)")
        elif b is None:
            failures.append(f"table {t}: MISSING in copy (live has {a})")
        elif b < a:
            failures.append(f"table {t}: copy LOST rows ({a} -> {b})")
        elif b > a:
            notes.append(f"table {t}: copy gained rows ({a} -> {b})")

    ll, cl = lane_counts(lc), lane_counts(cc)
    lost = [k for k in ll if cl.get(k, 0) < ll[k]]
    for k in lost[:10]:
        failures.append(f"lane {k}: copy lost shipments ({ll[k]} -> {cl.get(k, 0)})")
    if len(lost) > 10:
        failures.append(f"... and {len(lost)-10} more lanes lost rows")

    lh, ch = lane_transit_checksums(lc), lane_transit_checksums(cc)
    drift = [k for k in lh if k in ch and lh[k] != ch[k]]
    gone = [k for k in lh if k not in ch]
    if gone:
        failures.append(f"{len(gone)} (carrier,hub,zip) lane histories MISSING in copy, e.g. {gone[:3]}")
    if drift:
        failures.append(f"{len(drift)} lane transit distributions CHANGED, e.g. {drift[:3]}")
    notes.append(f"lane-history surface: {len(lh)} live keys, {len(ch)} copy keys, {len(drift)} drifted")

    print("=" * 60)
    print(f"VALIDATE live={args.live}")
    print(f"     vs copy={args.copy}")
    for n in notes:
        print(f"  note: {n}")
    for f in failures:
        print(f"  FAIL: {f}")
    verdict = "PASS" if not failures else "FAIL"
    print(f"VERDICT: {verdict}")
    lc.close(); cc.close()
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
