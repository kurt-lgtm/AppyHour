"""For differing orders, compare CSV ItemCnt vs our fulfillable item count."""
from __future__ import annotations
import csv, json, sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour")
from box_simulation import build_lookup, simulate, PHYSICAL_PREFIXES, resolve_distvol  # noqa: E402

CSV_PATH = Path(r"C:\Users\Work\Downloads\AHB_BoxAssignment_4-27-26.xlsx - SAT.csv")
CACHE = Path(r"C:\Users\Work\box_sim_cache__SHIP_2026-04-27.json")
OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\reports\item_count_drift__SHIP_2026-04-27.tsv")

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
                    "csv_items": int(r.get("ItemCnt") or 0),
                    "csv_vol": float(r.get("TotalVolume") or 0),
                    "csv_box": (r.get("Boxes") or "").strip(),
                }
    orders = json.loads(CACHE.read_text(encoding="utf-8"))
    lookup = build_lookup()
    sim = simulate(orders, lookup)
    by_name = {r["Order"].lstrip("#"): r for r in sim}
    raw_by_name = {o["name"].lstrip("#"): o for o in orders}

    # raw fulfillable count = sum of currentQuantity for SKUs we'd count (excludes PK-, admin)
    def raw_count(o):
        total_qty = 0
        all_qty = 0  # before filters
        for e in o["lineItems"]["edges"]:
            n = e["node"]
            sku = (n.get("sku") or "").strip()
            cq = n.get("currentQuantity")
            q = int(cq if cq is not None else (n.get("quantity") or 0))
            if q <= 0:
                continue
            all_qty += q
            if not sku:
                continue
            prefix = sku.split("-",1)[0] if "-" in sku else sku
            in_lookup = sku in lookup
            if not in_lookup and prefix not in PHYSICAL_PREFIXES:
                continue
            if sku.startswith("PK-"):
                continue
            total_qty += q
        return total_qty, all_qty

    def has_tray(o):
        for e in o["lineItems"]["edges"]:
            n = e["node"]
            sku = (n.get("sku") or "").strip()
            cq = n.get("currentQuantity")
            q = int(cq if cq is not None else (n.get("quantity") or 0))
            if q > 0 and sku == "AHB-MCUST-TRAY":
                return True
        return False

    rows = []
    skipped_tray = 0
    for oid, c in csv_rows.items():
        s = by_name.get(oid)
        raw = raw_by_name.get(oid)
        if not s or not raw:
            continue
        if has_tray(raw):
            skipped_tray += 1
            continue
        ours_items, all_items = raw_count(raw)
        box_diff = csv_box_kind(c["csv_box"]) != s["Box Assignment"]
        vol_delta = round(s["Total DistVol"] - c["csv_vol"], 3)
        item_delta = ours_items - c["csv_items"]
        if not (box_diff or abs(vol_delta) > 0.05):
            continue
        rows.append({
            "oid": oid,
            "csv_box": c["csv_box"],
            "our_box": s["Box Assignment"],
            "csv_items": c["csv_items"],
            "our_items": ours_items,
            "all_lineitems_qty": all_items,
            "item_delta": item_delta,
            "csv_vol": c["csv_vol"],
            "our_dv": s["Total DistVol"],
            "vol_delta": vol_delta,
            "box_mismatch": "Y" if box_diff else "",
        })
    rows.sort(key=lambda r: (-abs(r["item_delta"]), -abs(r["vol_delta"])))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    n = len(rows)
    item_match = sum(1 for r in rows if r["item_delta"] == 0)
    item_off = sum(1 for r in rows if r["item_delta"] != 0)
    print(f"Skipped (AHB-MCUST-TRAY): {skipped_tray}")
    print(f"Differing orders: {n}")
    print(f"  Item count matches CSV: {item_match}")
    print(f"  Item count differs:     {item_off}")
    from collections import Counter
    by_delta = Counter(r["item_delta"] for r in rows)
    print(f"\nItem delta distribution (ours - csv):")
    for d in sorted(by_delta):
        print(f"  {d:+d}: {by_delta[d]} orders")
    print(f"\nTop 20 by |item_delta| then |vol_delta|:")
    print(f"{'OID':<8}{'csv_i':>6}{'our_i':>6}{'all':>6}{'i_d':>5}{'csv_v':>7}{'our_v':>7}{'v_d':>7}  box")
    for r in rows[:25]:
        flag = "BOX" if r["box_mismatch"] else ""
        print(f"{r['oid']:<8}{r['csv_items']:>6}{r['our_items']:>6}{r['all_lineitems_qty']:>6}{r['item_delta']:>+5}{r['csv_vol']:>7.2f}{r['our_dv']:>7.3f}{r['vol_delta']:>+7.3f}  {flag}")
    # Does CSV ItemCnt == our all_lineitems_qty (i.e. CSV doesn't filter PK-/admin)?
    csv_eq_all = sum(1 for r in rows if r["csv_items"] == r["all_lineitems_qty"])
    csv_eq_filtered = sum(1 for r in rows if r["csv_items"] == r["our_items"])
    print(f"\nCSV items == our all_lineitems_qty (unfiltered): {csv_eq_all} / {len(rows)}")
    print(f"CSV items == our filtered count:                  {csv_eq_filtered} / {len(rows)}")
    print(f"\nReport: {OUT}")

if __name__ == "__main__":
    main()
