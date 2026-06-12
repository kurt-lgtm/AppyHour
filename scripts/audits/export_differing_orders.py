"""Export orders where our schema differs from CSV (box mismatch OR vol delta > 0.05).

Output: TSV with order id, customer, csv box+vol, our box+vol, delta, line items.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour")
from box_simulation import build_lookup, simulate  # noqa: E402

CSV_PATH = Path(r"C:\Users\Work\Downloads\AHB_BoxAssignment_4-27-26.xlsx - SAT.csv")
CACHE = Path(r"C:\Users\Work\box_sim_cache__SHIP_2026-04-27.json")
OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\reports\differing_orders__SHIP_2026-04-27.tsv")
VOL_TOL = 0.05


def csv_box_kind(box: str) -> str:
    b = box.lower()
    if "small" in b: return "Small Box"
    if "medium" in b: return "Medium Box"
    if "large" in b or "tray" in b: return "Large Box"
    return box


def main():
    csv_rows = {}
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            oid = (r.get("OrderID") or "").strip()
            if oid:
                csv_rows[oid] = {
                    "name": r.get("Name", "").strip(),
                    "csv_vol": float(r.get("TotalVolume") or 0),
                    "csv_box": (r.get("Boxes") or "").strip(),
                }

    orders = json.loads(CACHE.read_text(encoding="utf-8"))
    lookup = build_lookup()
    sim = simulate(orders, lookup)
    by_name = {r["Order"].lstrip("#"): r for r in sim}
    raw_by_name = {o["name"].lstrip("#"): o for o in orders}

    differing = []
    for oid, c in csv_rows.items():
        s = by_name.get(oid)
        if not s:
            continue
        box_diff = csv_box_kind(c["csv_box"]) != s["Box Assignment"]
        delta = round(s["Total DistVol"] - c["csv_vol"], 3)
        vol_diff = abs(delta) > VOL_TOL
        if not (box_diff or vol_diff):
            continue
        # line items
        raw = raw_by_name.get(oid)
        items = []
        if raw:
            for e in raw["lineItems"]["edges"]:
                n = e["node"]
                sku = (n.get("sku") or "").strip()
                cq = n.get("currentQuantity")
                q = int(cq if cq is not None else (n.get("quantity") or 0))
                if q > 0 and sku:
                    items.append(f"{sku}x{q}")
        differing.append({
            "order_id": oid,
            "customer": c["name"],
            "csv_box": c["csv_box"],
            "our_box": s["Box Assignment"],
            "csv_vol": c["csv_vol"],
            "our_dv": s["Total DistVol"],
            "delta": delta,
            "box_mismatch": "Y" if box_diff else "",
            "vol_mismatch": "Y" if vol_diff else "",
            "line_items": " | ".join(items),
        })

    differing.sort(key=lambda r: (r["box_mismatch"] != "Y", -abs(r["delta"])))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(differing[0].keys()), delimiter="\t")
        w.writeheader()
        for r in differing:
            w.writerow(r)

    n_box = sum(1 for r in differing if r["box_mismatch"])
    n_vol = sum(1 for r in differing if r["vol_mismatch"])
    print(f"Differing orders: {len(differing)} (box={n_box}, vol-only={len(differing)-n_box})")
    print(f"Output: {OUT}")
    print(f"\nFirst 15 (box mismatches first, then biggest vol deltas):")
    print(f"{'OID':<8}{'CSV Box':<22}{'Ours':<14}{'CSV':>6}{'Ours':>7}{'delta':>7}  flags")
    for r in differing[:15]:
        flags = ("BOX " if r["box_mismatch"] else "    ") + ("VOL" if r["vol_mismatch"] else "")
        print(f"{r['order_id']:<8}{r['csv_box']:<22}{r['our_box']:<14}{r['csv_vol']:>6.2f}{r['our_dv']:>7.3f}{r['delta']:>+7.3f}  {flags}")


if __name__ == "__main__":
    main()
