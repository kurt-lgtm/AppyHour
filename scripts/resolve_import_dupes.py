"""Canonical Matrixify import dupe resolver (replaces resolve_dupes_2026_* one-shots).

Constraints SSOT: scripts/RESOLVE_DUPES_RULES.md  (read BEFORE changing this file).
Process doc: ~/.claude/skills/matrixify-import-dupe-check/SKILL.md

Phases:
  1. DETECT  — reuse check_import_dupes.bulk_fetch_orders (live + in-sheet dupes).
  2. SPLIT   — DUPES vs CLEAN orders.
  3. PICK    — history-aware replacement per dupe (CH-/MT- only; AC- dropped):
               candidate must clear current box + full history + this import's
               adds + picks already made for the order. Dietary-restriction tag
               (NNRS/CORS/NCRS) on the order -> NEEDS-DIETARY-REVIEW, no auto-pick.
               No candidate -> MISSING (never fabricate).
  4. EMIT    — decision log always; corrected CSV only with --apply
               (versions alongside, NEVER overwrites input or a prior output).

READ-ONLY against Shopify. Output is a Matrixify CSV; nothing is written live.

Usage:
  python resolve_import_dupes.py --export <csv> --ship-tag <TAG> [--warnings <txt>] [--apply]
"""
from __future__ import annotations

import argparse
import csv
import re
import io
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

# 🔴 Windows cp1252 kills any non-ASCII print (arrows, emoji) MID-RUN — for a --apply tool that
# means a crash BETWEEN mutations, leaving the batch half-applied. Wrap stdout before anything
# prints, including argparse --help. (Live 2026-08-09: shorts_pass.py --help died on U+2192.)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))              # AppyHour/ for appyhour_lib
sys.path.insert(0, str(_HERE.parent / "utilities"))    # for check_import_dupes

OUT_DIR = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts")
DIETARY_TAGS = ("NNRS", "CORS", "NCRS")
REPLACE_PREFIXES = ("CH-", "MT-")
DROP_PREFIXES = ("AC-",)  # Kurt standing call: AC- dupes drop, keep existing

ONE_Q = ('query($q:String!){orders(first:1,query:$q){edges{node{name tags '
         'customer{email tags}}}}}')
HIST_Q = ('query($q:String!){orders(first:50,query:$q){edges{node{'
          'lineItems(first:100){edges{node{sku currentQuantity quantity}}}}}}}')


def _li_skus(node) -> set[str]:
    out = set()
    for e in node["lineItems"]["edges"]:
        n = e["node"]
        q = n.get("currentQuantity")
        q = n.get("quantity", 0) if q is None else q
        s = (n.get("sku") or "").strip()
        if s and q and q > 0:
            out.add(s)
    return out


def load_export(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        fields = rdr.fieldnames
        rows = list(rdr)
    name_col = next(c for c in fields if re.match(r"^(Name|Order)", c, re.I))
    return fields, rows, name_col


def parse_warnings(path: Path) -> dict[str, set[str]]:
    """WARNINGS.txt -> {order_digits: {dupe_sku}}."""
    dupes: dict[str, set[str]] = defaultdict(set)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r'Order #(\d+).*?SKU "([A-Z0-9-]+)"', line)
        if m:
            dupes[m.group(1)].add(m.group(2))
    return dict(dupes)


def detect_dupes(rows, name_col, live: dict[str, dict]):
    """Phase A logic (mirrors check_import_dupes): live + in-sheet dupes.

    Returns ({order: {dupe_sku}}, [missing_order_digits]).
    """
    intended: dict[str, list[dict]] = {}
    for r in rows:
        intended.setdefault(re.sub(r"\D", "", r[name_col]), []).append(r)
    dupes: dict[str, set[str]] = defaultdict(set)
    missing = []
    for digits, items in intended.items():
        if digits not in live:
            missing.append(digits)
            continue
        cur = live[digits]["skus"]
        counts = Counter((r["child_sku"] or "").strip() for r in items)
        for r in items:
            s = (r["child_sku"] or "").strip()
            if s and s in cur:
                dupes[digits].add(s)          # live dupe
        for s, c in counts.items():
            if s and c > 1:
                dupes[digits].add(s)          # in-sheet dupe
    return dict(dupes), missing


def build_pools(rows) -> dict[str, list[str]]:
    """$0 in-box candidate pools per prefix, from the sheet only (no fabrication).

    Ordered by frequency in the sheet (most-used = confirmed in rotation/stock).
    """
    freq = Counter()
    for r in rows:
        s = (r["child_sku"] or "").strip()
        if s.startswith(REPLACE_PREFIXES):
            freq[s] += 1
    pools = {}
    for p in REPLACE_PREFIXES:
        pools[p] = [s for s, _ in freq.most_common() if s.startswith(p)]
    return pools


def sheet_identity(rows) -> dict[str, tuple[str, str]]:
    """sku -> (Line: Product ID, Line: Product Handle) verbatim from the sheet."""
    ident = {}
    for r in rows:
        s = (r["child_sku"] or "").strip()
        if s and s not in ident:
            ident[s] = (r.get("Line: Product ID", ""), r.get("Line: Product Handle", ""))
    return ident


def pick_replacements(dupes, rows, name_col, pools, history_fn, dietary_fn):
    """Phase B. Returns (decisions list, {(order, dupe_sku): replacement|None}).

    history_fn(order_digits) -> set of every SKU the customer ever received
    (current box + full order history). dietary_fn(order_digits) -> bool
    (order/customer carries an NNRS/CORS/NCRS tag).
    Pure logic — inject stubs for offline tests.
    """
    decisions = []
    assign: dict[tuple[str, str], str | None] = {}
    for order in sorted(dupes):
        import_adds = {(r["child_sku"] or "").strip()
                       for r in rows if re.sub(r"\D", "", r[name_col]) == order}
        restricted = dietary_fn(order)
        chosen: set[str] = set()
        ever: set[str] | None = None
        for dsku in sorted(dupes[order]):
            if dsku.startswith(DROP_PREFIXES):
                assign[(order, dsku)] = None
                decisions.append((order, dsku, "DROP", "AC- dupe: drop row, customer keeps existing (standing call)"))
                continue
            if not dsku.startswith(REPLACE_PREFIXES):
                assign[(order, dsku)] = None
                decisions.append((order, dsku, "DROP", "non-CH/MT dupe: drop duplicate row"))
                continue
            if restricted:
                assign[(order, dsku)] = None
                decisions.append((order, dsku, "NEEDS-DIETARY-REVIEW",
                                  "order carries NNRS/CORS/NCRS tag; no auto-pick (rule 2)"))
                continue
            if ever is None:
                ever = history_fn(order)
            blocked = ever | import_adds | chosen
            sub = next((s for s in pools.get(dsku[:3], []) if s not in blocked), None)
            assign[(order, dsku)] = sub
            if sub:
                chosen.add(sub)
                decisions.append((order, dsku, sub,
                                  "clears box+history+import adds+prior picks"))
            else:
                decisions.append((order, dsku, "MISSING",
                                  "no in-sheet candidate clears history (never fabricate)"))
    return decisions, assign


def apply_swaps(rows, fields, name_col, assign, ident):
    """Regenerate rows: swap picked replacements, drop DROP/dupe rows, in-sheet dedupe."""
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for r in rows:
        grouped.setdefault(re.sub(r"\D", "", r[name_col]), []).append(r)
    out = []
    for order, grp in grouped.items():
        seen: set[str] = set()
        swapped_done: set[str] = set()
        for r in grp:
            r = dict(r)
            sku = (r["child_sku"] or "").strip()
            key = (order, sku)
            if key in assign:
                sub = assign[key]
                if sub is None or sku in swapped_done:
                    continue  # drop: AC-/unresolved dupe row (safe — customer keeps existing; surfaced in log) or extra in-sheet copy
                swapped_done.add(sku)
                pid, handle = ident.get(sub, ("", ""))
                r["child_sku"] = sub
                if "Line: Product ID" in r:
                    r["Line: Product ID"] = pid
                if "Line: Product Handle" in r:
                    r["Line: Product Handle"] = handle
                sku = sub
            if sku in seen:
                continue  # in-sheet dedupe (rule 6)
            seen.add(sku)
            out.append(r)
    return out


def versioned(path: Path) -> Path:
    """Never overwrite: return first non-existing -N variant."""
    if not path.exists():
        return path
    n = 2
    while (p := path.with_name(f"{path.stem}-{n}{path.suffix}")).exists():
        n += 1
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True, type=Path)
    ap.add_argument("--ship-tag", required=True)
    ap.add_argument("--warnings", type=Path, default=None)
    ap.add_argument("--apply", action="store_true",
                    help="write the corrected CSV (default: dry-run, log only)")
    args = ap.parse_args(argv)

    import requests  # noqa: F401  (network path only)
    from check_import_dupes import bulk_fetch_orders, _gql  # reuse, don't rebuild
    from appyhour_lib.credentials import get_shopify_auth

    fields, rows, name_col = load_export(args.export)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"export rows: {len(rows)}  tag: {args.ship_tag}")
    live = bulk_fetch_orders(args.ship_tag)
    print(f"live orders fetched: {len(live)}")

    if args.warnings:
        dupes = parse_warnings(args.warnings)
        missing = []
    else:
        dupes, missing = detect_dupes(rows, name_col, live)
    dupe_orders = set(dupes)
    clean_orders = {re.sub(r"\D", "", r[name_col]) for r in rows} - dupe_orders - set(missing)
    print(f"DUPE orders: {len(dupe_orders)}  CLEAN: {len(clean_orders)}  MISSING from tag: {len(missing)} {missing[:15]}")

    base, headers = get_shopify_auth()
    _meta_cache: dict[str, dict] = {}

    def _meta(order):
        if order not in _meta_cache:
            ed = _gql(base, headers, ONE_Q, {"q": f"name:#{order}"})["orders"]["edges"]
            node = ed[0]["node"] if ed else {}
            cust = node.get("customer") or {}
            _meta_cache[order] = {"email": cust.get("email"),
                                  "tags": list(node.get("tags") or []) + list(cust.get("tags") or [])}
        return _meta_cache[order]

    def history_fn(order):
        ever = set(live.get(order, {}).get("skus", set()))
        em = _meta(order)["email"]
        if em:
            for e in _gql(base, headers, HIST_Q, {"q": f"email:{em}"})["orders"]["edges"]:
                ever |= _li_skus(e["node"])
        return ever

    def dietary_fn(order):
        tags = " ".join(_meta(order)["tags"]).upper()
        return any(t in tags for t in DIETARY_TAGS)

    pools = build_pools(rows)
    ident = sheet_identity(rows)
    decisions, assign = pick_replacements(dupes, rows, name_col, pools, history_fn, dietary_fn)

    log_path = versioned(OUT_DIR / f"{args.export.stem}_RESOLVE-DECISIONS-{args.ship_tag}.txt")
    lines = [f"#{o}  {d} -> {sub}  ({why})" for o, d, sub, why in decisions]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for ln in lines[:60]:
        print(ln)
    print(f"decision log: {log_path}")

    unresolved = [d for d in decisions if d[2] in ("MISSING", "NEEDS-DIETARY-REVIEW")]
    if unresolved:
        print(f"UNRESOLVED (operator action needed): {len(unresolved)}")

    if not args.apply:
        print("dry-run (no CSV written). Re-run with --apply to emit the corrected import CSV.")
        return 0

    out_rows = apply_swaps(rows, fields, name_col, assign, ident)
    out_path = versioned(OUT_DIR / f"{args.export.stem}_RESOLVED-{args.ship_tag}.csv")
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"corrected CSV: {out_path}  (rows in: {len(rows)}  out: {len(out_rows)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
