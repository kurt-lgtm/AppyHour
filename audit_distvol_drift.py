"""Audit DistVol drift: solve per-SKU CSV-vs-lookup delta via least-squares.

For each order: csv_total - our_total = sum_{sku} qty * (csv_dv[sku] - our_dv[sku])
Solve for per-SKU delta x[sku]. SKUs with large |x| -> stale/missing in lookup.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour")
from box_simulation import build_lookup, resolve_distvol, PHYSICAL_PREFIXES  # noqa: E402

# 🔴 The inputs are WEEKLY/COHORT ingest artifacts, passed per run -- no baked-in dated
# paths (silent-stale class). --csv is a box-assignment export carrying OrderID +
# TotalVolume columns (e.g. "AHB_BoxAssignment_<date>.xlsx - SAT.csv"); --cache is the
# box_simulation order cache for the SAME cohort (box_sim_cache_<SHIP_TAG>.json).
DEFAULT_OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\reports\distvol_drift_audit.tsv")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="audit_distvol_drift",
        description="Least-squares per-SKU DistVol delta: box-assignment CSV vs our lookup.")
    ap.add_argument("--csv", required=True, metavar="PATH", dest="csv_path",
                    help="box-assignment export with OrderID + TotalVolume columns "
                         "(AHB_BoxAssignment_<date> csv)")
    ap.add_argument("--cache", required=True, metavar="PATH",
                    help="box_simulation order cache json for the SAME cohort "
                         "(box_sim_cache_<SHIP_TAG>.json)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), metavar="PATH",
                    help=f"tsv report path (default: {DEFAULT_OUT})")
    a = ap.parse_args(argv)
    CSV_PATH, CACHE, OUT = Path(a.csv_path), Path(a.cache), Path(a.out)
    if not CSV_PATH.exists():
        sys.exit(f"audit_distvol_drift: --csv file not found: {CSV_PATH}")
    if not CACHE.exists():
        sys.exit(f"audit_distvol_drift: --cache file not found: {CACHE} "
                 "(run box_simulation.py <SHIP_TAG> first to build it)")

    lookup = build_lookup()
    orders = json.loads(CACHE.read_text(encoding="utf-8"))
    by_name = {o["name"].lstrip("#"): o for o in orders}

    csv_vols = {}
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            oid = (r.get("OrderID") or "").strip()
            if oid:
                csv_vols[oid] = float(r.get("TotalVolume") or 0)

    # Build SKU index from orders
    sku_set = set()
    order_rows = []
    for oid, csv_total in csv_vols.items():
        o = by_name.get(oid)
        if not o:
            continue
        sku_qty: dict[str, int] = defaultdict(int)
        our_total = 0.0
        for e in o["lineItems"]["edges"]:
            n = e["node"]
            sku = (n.get("sku") or "").strip()
            cq = n.get("currentQuantity")
            qty = int(cq if cq is not None else (n.get("quantity") or 0))
            if qty <= 0 or not sku:
                continue
            prefix = sku.split("-", 1)[0] if "-" in sku else sku
            in_lookup = sku in lookup
            if not in_lookup and prefix not in PHYSICAL_PREFIXES:
                continue
            if sku.startswith("PK-"):
                continue
            dv, _flag = resolve_distvol(sku, lookup)
            sku_qty[sku] += qty
            our_total += dv * qty
            sku_set.add(sku)
        order_rows.append((oid, sku_qty, csv_total, our_total))

    skus = sorted(sku_set)
    sku_idx = {s: i for i, s in enumerate(skus)}
    A = np.zeros((len(order_rows), len(skus)))
    b = np.zeros(len(order_rows))
    for r, (oid, sq, ct, ot) in enumerate(order_rows):
        for sku, q in sq.items():
            A[r, sku_idx[sku]] = q
        b[r] = ct - ot  # delta to attribute

    # least-squares
    x, *_ = np.linalg.lstsq(A, b, rcond=None)

    # Frequency of each SKU in mismatch orders
    freq = np.count_nonzero(A, axis=0)
    total_qty = A.sum(axis=0)

    rows = []
    for sku, i in sku_idx.items():
        delta = float(x[i])
        cur, flag = resolve_distvol(sku, lookup)
        suggested = cur + delta
        rows.append({
            "sku": sku,
            "freq": int(freq[i]),
            "total_qty": int(total_qty[i]),
            "current_dv": round(cur, 4),
            "delta": round(delta, 4),
            "suggested_dv": round(suggested, 4),
            "flagged_missing": flag,
        })
    # Sort by absolute total drift impact = |delta| * total_qty
    rows.sort(key=lambda r: -abs(r["delta"]) * r["total_qty"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sku", "freq", "total_qty", "current_dv", "delta", "suggested_dv", "flagged_missing"], delimiter="\t")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # Print top 30 by impact
    print(f"Orders analyzed: {len(order_rows)}, SKUs: {len(skus)}")
    print(f"Residual norm: {np.linalg.norm(A @ x - b):.3f} (RMS {np.sqrt(np.mean((A@x-b)**2)):.4f})")
    print(f"\nTop 30 SKUs by |delta| * total_qty (likely stale/missing):")
    print(f"{'SKU':<20}{'freq':>6}{'qty':>6}{'cur':>8}{'delta':>9}{'sugg':>9}{'miss':>6}")
    for r in rows[:30]:
        miss = "*" if r["flagged_missing"] else ""
        print(f"{r['sku']:<20}{r['freq']:>6}{r['total_qty']:>6}{r['current_dv']:>8.3f}{r['delta']:>+9.3f}{r['suggested_dv']:>9.3f}{miss:>6}")
    print(f"\nFull report: {OUT}")


if __name__ == "__main__":
    main()
