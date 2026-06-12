"""Verify AHB box assignment CSV vs DistVol logic.

Usage: python verify_box_csv.py <csv_path> <ship_tag>
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour")
import box_simulation as bs  # noqa: E402
from box_simulation import (  # noqa: E402
    build_lookup, fetch_all_orders, simulate, SMALL_MAX, LARGE_MAX,
)
# Force tag-only fetch (no fulfillment filter) so already-fulfilled orders included
_orig_fetch = fetch_all_orders
def fetch_all_orders(base, hdr, tag):  # noqa: E402
    # Trick: prefix RMFG_ inside fn skips status filter
    class _T(str):
        def startswith(self, p):
            if p == "RMFG_":
                return True
            return str.startswith(self, p)
    return _orig_fetch(base, hdr, _T(tag))
sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP")
from utils import get_shopify_auth  # noqa: E402

OUT_DIR = Path(r"C:\Users\Work\Claude Projects\_outputs\reports")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_csv(path: Path) -> dict[str, dict]:
    rows = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            oid = (r.get("OrderID") or "").strip()
            if not oid:
                continue
            rows[oid] = {
                "name": r.get("Name", "").strip(),
                "csv_vol": float(r.get("TotalVolume") or 0),
                "csv_box": (r.get("Boxes") or "").strip(),
            }
    return rows


def csv_box_kind(box: str) -> str:
    b = box.lower()
    if "small" in b:
        return "Small Box"
    if "medium" in b:
        return "Medium Box"
    if "large" in b or "tray" in b:
        return "Large Box"
    return box


def main() -> None:
    csv_path = Path(sys.argv[1])
    tag = sys.argv[2]
    csv_rows = load_csv(csv_path)
    print(f"CSV: {len(csv_rows)} orders")

    cache = Path(rf"C:\Users\Work\box_sim_cache_{tag}.json")
    lookup = build_lookup()
    if cache.exists():
        orders = json.loads(cache.read_text(encoding="utf-8"))
        print(f"cache hit: {len(orders)} orders")
    else:
        base, hdr = get_shopify_auth()
        orders = fetch_all_orders(base, hdr, tag)
        cache.write_text(json.dumps(orders), encoding="utf-8")
        print(f"fetched {len(orders)} orders -> cached")

    sim = simulate(orders, lookup)
    by_name = {r["Order"].lstrip("#"): r for r in sim}

    matched = 0
    missing_in_shopify = []
    missing_in_csv = []
    box_mismatches = []
    vol_mismatches = []  # |delta| > 0.05
    over_cap = []

    for oid, c in csv_rows.items():
        s = by_name.get(oid)
        if not s:
            missing_in_shopify.append(oid)
            continue
        matched += 1
        if csv_box_kind(c["csv_box"]) != s["Box Assignment"]:
            box_mismatches.append((oid, c["csv_box"], s["Box Assignment"], c["csv_vol"], s["Total DistVol"]))
        delta = round(s["Total DistVol"] - c["csv_vol"], 3)
        if abs(delta) >= 0.02:  # < .02 = rounding noise
            vol_mismatches.append((oid, c["csv_vol"], s["Total DistVol"], delta))
        if s["Over Capacity?"]:
            over_cap.append((oid, s["Total DistVol"]))

    csv_oids = set(csv_rows.keys())
    for r in sim:
        nm = r["Order"].lstrip("#")
        if nm not in csv_oids:
            missing_in_csv.append(nm)

    out = OUT_DIR / f"box_verify_{tag}.txt"
    lines = []
    lines.append(f"=== Box Assignment Verification — {tag} ===")
    lines.append(f"CSV orders: {len(csv_rows)}")
    lines.append(f"Shopify orders (tag): {len(sim)}")
    lines.append(f"Matched: {matched}")
    lines.append(f"In CSV not in Shopify: {len(missing_in_shopify)}")
    lines.append(f"In Shopify not in CSV: {len(missing_in_csv)}")
    lines.append(f"Box mismatches: {len(box_mismatches)}")
    lines.append(f"Volume mismatches (>|0.05|): {len(vol_mismatches)}")
    lines.append(f"Over-capacity (DistVol > {LARGE_MAX}): {len(over_cap)}")
    lines.append("")
    lines.append("--- Box mismatches (oid, csv_box, our_box, csv_vol, our_dv) ---")
    for row in box_mismatches[:200]:
        lines.append("\t".join(str(x) for x in row))
    if len(box_mismatches) > 200:
        lines.append(f"... +{len(box_mismatches)-200} more")
    lines.append("")
    lines.append("--- Over-capacity (oid, dv) ---")
    for row in over_cap:
        lines.append("\t".join(str(x) for x in row))
    lines.append("")
    lines.append("--- Volume deltas top 50 (oid, csv, ours, delta) ---")
    for row in sorted(vol_mismatches, key=lambda x: -abs(x[3]))[:50]:
        lines.append("\t".join(str(x) for x in row))
    lines.append("")
    if missing_in_shopify:
        lines.append(f"--- Missing in Shopify (first 50 of {len(missing_in_shopify)}) ---")
        lines.append(", ".join(missing_in_shopify[:50]))
    if missing_in_csv:
        lines.append(f"--- Missing in CSV (first 50 of {len(missing_in_csv)}) ---")
        lines.append(", ".join(missing_in_csv[:50]))

    flagged_skus = Counter()
    for r in sim:
        if r["Flagged SKUs"]:
            for sku in r["Flagged SKUs"].split(", "):
                flagged_skus[sku] += 1
    if flagged_skus:
        lines.append("")
        lines.append("--- Flagged SKUs (not in DistVol lookup, used prefix default) ---")
        for sku, cnt in flagged_skus.most_common(30):
            lines.append(f"{sku}\t{cnt}")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {out}")
    print(f"Matched {matched}/{len(csv_rows)} | box mismatches {len(box_mismatches)} | vol mismatches {len(vol_mismatches)} | over-cap {len(over_cap)}")


if __name__ == "__main__":
    main()
