"""Sheet (pick list of record) <-> Shopify comparison.

🔴 The sheet must be built from currentQuantity. Two real defects found this way, both
sheet-side: #175526 carried the live 10 trays AND the 10 removed originals; #174939
omitted AC-KETT x2, a paid BL-4USA board component.
"""
from __future__ import annotations
import collections
import os
import io
import csv
from .checks import in_scope, live, sku, tags
from .rules import CHILD

MFG_PREFIX = "AHB (S_REG):"


def load_sheet(path: str, tab: str | None = None):
    """-> {order_id: dict(name, items, guides, tags, zip, per_column)}"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[tab] if tab else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    h_i = next(i for i, r in enumerate(rows)
               if any(str(c or "").strip() == "OrderID" for c in r))
    hdr = rows[h_i]
    I = {h: i for i, h in enumerate(hdr) if h}
    mfg = [i for i, h in enumerate(hdr) if h and str(h).startswith(MFG_PREFIX)]
    guide = [i for i in mfg if "Tasting Guide" in str(hdr[i])]
    item = [i for i in mfg if i not in guide]
    out = {}
    for r in rows[h_i + 1:]:
        oid = r[I["OrderID"]]
        if oid is None:
            continue
        out[str(oid).strip().lstrip("#")] = {
            "name": r[I.get("Name", 0)],
            "items": sum(int(r[i]) for i in item if isinstance(r[i], (int, float))),
            "guides": sum(int(r[i]) for i in guide if isinstance(r[i], (int, float))),
            "tags": r[I["Tags"]] or "" if "Tags" in I else "",
            "zip": str(r[I["Zip"]] or "") if "Zip" in I else "",
            "columns": {str(hdr[i]).replace(MFG_PREFIX + " ", ""): int(r[i])
                        for i in item if isinstance(r[i], (int, float)) and r[i]},
        }
    return out


MFG_AUTHORITY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "mfg_names_authoritative.csv")


def _mfg_authority(path: str = MFG_AUTHORITY):
    """{SKU: 'AHB (S_REG): <MFG name>'} from the meal-type export. Headerless, 2 cols.

    🔴 This file is RMFG's, never ours to rename ([[mfg-names-are-rmfgs-never-rename]]).
    A missing file is loud: an empty authority silently re-opens the fuzzy-match path.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"MFG name authority missing: {path}")
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        out = {r[0].strip(): r[1].strip() for r in csv.reader(fh) if len(r) > 1 and r[0].strip()}
    if not out:
        raise ValueError(f"MFG name authority parsed 0 rows: {path}")
    return out


def resolve_columns(sheet: dict, orders: dict):
    """Map each sheet MFG column name -> SKU, using the orders as the authority.

    🔴 Column headers are MFG names, not SKUs. Comparing sheet columns to Shopify SKUs
    without this returns a diff on virtually every row (807 of 808 before the port).
    Alias source order: ALIAS_OVERRIDE, then exact title match against live line items,
    then difflib at 0.72. Anything left over is reported, never silently dropped.
    """
    import difflib
    from .rules import ALIAS_OVERRIDE, clean_title
    # 🔴 The MFG-name authority is consulted BEFORE difflib. Both 'Maple Frais
    # Fromage' and 'Sottocenere with Truffles' were in mfg_names_authoritative.csv the
    # whole time and still landed in `unmatched` on the 09-08 run, because nothing here
    # read the file -- a column that resolves in the authority must never fall through to
    # fuzzy matching or an "unmatched" report ([[never-fabricate]]: look it up).
    # 🔴 Reverse the authority by CLEANED name, and NEVER pick a winner on a
    # collision. mfg_names_authoritative.csv really does carry two SKUs whose names differ
    # only by a trailing period -- CH-BRZ 'Prairie Breeze' and CH-PRBZ 'Prairie Breeze.' --
    # and clean_title() folds them together. A last-write-wins dict silently chose CH-PRBZ
    # and turned 26 correct orders into per-SKU c2 diffs. An ambiguous name resolves to
    # NOTHING here; it falls through to the live line items, which know the real SKU.
    auth, auth_dupe = {}, set()
    for sk, nm in _mfg_authority().items():
        k = clean_title(nm)
        if k in auth and auth[k] != sk:
            auth_dupe.add(k)
        auth[k] = sk
    for k in auth_dupe:
        auth.pop(k, None)
    alias = {}
    for o in orders.values():
        for e in o["lineItems"]["edges"]:
            n = e["node"]
            if n["sku"]:
                alias.setdefault(clean_title(n["title"]), n["sku"])
    names = {n for s in sheet.values() for n in (s.get("columns") or {})}
    col_sku, unmatched = {}, []
    for name in names:
        k = clean_title(name)
        if k in ALIAS_OVERRIDE:
            col_sku[name] = ALIAS_OVERRIDE[k]
        elif k in alias:
            col_sku[name] = alias[k]
        else:
            # 🔴 The authority is consulted only AFTER the live line items, never
            # before: the order's own title->SKU pair is what actually ships, and the
            # authority is the fallback for a column no order in THIS cohort carries.
            # Putting it first re-decided 26 already-correct rows.
            c = difflib.get_close_matches(k, list(alias), n=1, cutoff=0.72)
            if c:
                col_sku[name] = alias[c[0]]
            elif k in auth:
                col_sku[name] = auth[k]
            else:
                unmatched.append(name)
    for s in sheet.values():
        s["columns_sku"] = {col_sku[n]: q for n, q in (s.get("columns") or {}).items()
                            if n in col_sku}
    return col_sku, sorted(unmatched)


def compare(sheet: dict, shop: dict):
    """-> list of dicts for orders whose sheet total != live Shopify child count.

    🔴 Gift Redemption is EXCLUDED, not reported (Kurt 2026-09-08). Shopify is not
    editable for a gift (gateway recharge_credits), so a sheet-vs-Shopify delta on one is
    never actionable -- surfacing it is noise that buries the deltas we CAN fix. The sheet
    is the gift order's only record; the divergence is expected, not a defect.
    """
    out = []
    for oid, s in sheet.items():
        o = shop.get(oid)
        if not o:
            continue
        if not in_scope(o)[0]:
            continue
        n = sum(x["current_quantity"] for x in live(o) if sku(x).startswith(CHILD))
        if n == s["items"]:
            continue
        removed = collections.Counter()
        for x in o["line_items"]:
            if sku(x).startswith(CHILD) and x["current_quantity"] == 0:
                removed[sku(x)] += x["quantity"]
        out.append({"order": "#" + oid, "customer": s["name"], "sheet": s["items"],
                    "shopify": n, "delta": n - s["items"],
                    "removed_units": sum(removed.values()),
                    "removed": " ".join(f"{k}x{v}" for k, v in removed.most_common())})
    return out


def missing_guide(sheet: dict, shop: dict):
    out = []
    for oid, s in sheet.items():
        o = shop.get(oid)
        if o and any(t.strip().lower() == "gift redemption" for t in tags(o)):
            continue
        if s["guides"] != 1:
            out.append({"order": "#" + oid, "customer": s["name"], "guides": s["guides"]})
    return out


def tag_mismatch(sheet: dict, shop: dict, rmfg: str, ship: str):
    """6a - every sheet order must carry BOTH this run's tags.

    -> (mismatches, not_pulled). 🔴 Keep them SEPARATE: an order older than --since is a
    WINDOW artifact, not a tag defect. Reporting them together turned 0 real problems into
    "32 tag mismatches" on the 8_24 run.
    """
    out, unpulled = [], []
    for oid in sheet:
        o = shop.get(oid)
        if not o:
            unpulled.append({"order": "#" + oid, "customer": sheet[oid]["name"]})
            continue
        t = tags(o)
        if rmfg not in t or ship not in t:
            out.append({"order": "#" + oid, "issue": "missing run tag",
                        "run_tags": ",".join(x for x in t
                                             if x.startswith(("RMFG_", "_SHIP_")) or x == rmfg)})
    return out, unpulled


def not_on_sheet(sheet: dict, shop: dict, rmfg: str):
    """6c - Shopify order tagged for the run but absent from the pick list (the serious one)."""
    return [{"order": o["name"], "tags": o.get("tags", "")[:80]}
            for oid, o in shop.items()
            if rmfg in tags(o) and oid not in sheet and not o.get("cancelled_at")]
