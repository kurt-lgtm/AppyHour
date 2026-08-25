"""RULE SET loader. 🔴 Read ORDER_CHECKS_RULES.md before changing anything here."""
from __future__ import annotations
import re, os, functools

# EX-PS "Party Size Upgrade" — absent from the RULE SET xlsx. Kurt 2026-08-25.
PARTY = {"CH-": 2, "MT-": 2, "AC-": 2}
# Priced null-SKU packs whose item count is not in the variant title.
PACK_ITEMS = {"curator's choice - extra meat, cheese & accompaniment": 3}
# Paid boards -> components. A REMOVED board forfeits its components' allowance (#176565).
BOARD = {"BL-USA":  ("AC-KETT", "CH-FAG", "AC-BLUCAR", "MT-PARM"),
         "BL-4USA": ("AC-KETT", "CH-FAG", "AC-BLUCAR", "MT-PARM")}
CHILD = ("AC-", "MT-", "CH-", "TR-")
# CEX-/EX- parents -> the child type each contributes.
SLOT_TYPE = {"EX-EM": "MT-", "EX-EC": "CH-", "EX-EA": "AC-",
             "CEX-EM": "MT-", "CEX-EC": "CH-", "CEX-EA": "AC-", "CEX-CR": "AC-"}


@functools.lru_cache(maxsize=4)
def load_rule_set(path: str) -> dict:
    """Parse the RULE SET + SKU ADDS BY DISC CODE tabs. LIKELY is deliberately ignored."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {"any": {}, "typed": {}, "disc": {}}
    ws = wb["RULE SET"]
    rows = list(ws.iter_rows(values_only=True))
    # 🔴 Header row is NOT always row 0 - Dan ships the file both ways (the 08-18 backup
    # has a blank leading row). Find it, never assume.
    h_i = next(i for i, r in enumerate(rows)
               if str((r[0] or "")).strip().lower() == "prefix")
    hdr = [str(h or "").strip() for h in rows[h_i]]
    for r in rows[h_i + 1:]:
        pre = str(r[0] or "").strip()
        if not pre or pre.startswith("*"):
            continue
        typed = {}
        for i, h in enumerate(hdr[1:], start=1):
            if h in CHILD and isinstance(r[i], (int, float)):
                typed[h] = int(r[i])
            if h == "ANY" and isinstance(r[i], (int, float)):
                out["any"][pre] = int(r[i])
        if typed:
            out["typed"][pre] = typed
    if "SKU ADDS BY DISC CODE" in wb.sheetnames:
        for r in wb["SKU ADDS BY DISC CODE"].iter_rows(values_only=True):
            code = str(r[0] or "").strip().lower()
            if not code or code in ("discount code", "none", "sku addition"):
                continue
            adds = {k: int(v) for k, v in zip(("MT-", "CH-", "AC-"), r[1:4])
                    if isinstance(v, (int, float))}
            if adds:
                out["disc"][code] = adds
    return out


def box_expect(prefix: str, rs: dict):
    """(any_total, typed_dict, deferred). Longest matching prefix wins."""
    if prefix.startswith("AHB-X") or prefix.startswith("BL-"):
        return None, {}, True                       # docx: added separately
    for key in sorted(rs["any"], key=len, reverse=True):
        if prefix.startswith(key):
            return rs["any"][key], {}, False
    for key in sorted(rs["typed"], key=len, reverse=True):
        if prefix.startswith(key):
            return None, dict(rs["typed"][key]), False
    return None, {}, True


def resolve_box(line):
    """Rule-set prefix for a box line, or None.

    🔴 The box parent may have a NULL SKU (226 orders in one cohort). Resolve from
    variant_title, or it reads as NO_BOX.
    """
    s = (line.get("sku") or "").strip().upper()
    if s.startswith(("AHB-", "BL-")):
        return s
    if not s and (line.get("name") or "").lower().startswith(("appyhour box", "appyhour cheese box")):
        v = (line.get("variant_title") or "").lower()
        if v.startswith("medium"):
            return "AHB-MED"
        if v.startswith("large"):
            return "AHB-LGE"
    return None


def paid_pack_items(line) -> int:
    """Items a priced null-SKU add-on pack contributes (0 if not one)."""
    if (line.get("sku") or "").strip() or net(line) <= 0:
        return 0
    name = (line.get("name") or "").lower()
    if name.startswith(("appyhour box", "appyhour cheese box")):
        return 0
    q = line["current_quantity"]
    m = (re.search(r"(\d+)\s*items?", line.get("variant_title") or "", re.I)
         or re.search(r"(\d+)\s*items?", line.get("name") or "", re.I))
    if m:
        return int(m.group(1)) * q
    for k, c in PACK_ITEMS.items():
        if k in name:
            return c * q
    return 0


def net(line) -> float:
    """What the customer PAID for this line: price - LINE-LEVEL discount.

    🔴 NOT pre_tax_price — 0.00 on orders with no discount at all (#176576).
    🔴 NOT discount_allocations — those are ORDER-level codes; #176576's "AppyHour Credit"
       $103 spreads over the box + a paid CH-MAFT the customer did pay for.
    total_discount is the box-builder signal: #174407 CEX-EA price 5.50, discount 5.50 -> 0.
    """
    q = line["current_quantity"]
    return round(float(line["price"]) * q - float(line.get("total_discount") or 0), 2)
