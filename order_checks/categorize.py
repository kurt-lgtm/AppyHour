"""Categorise Check-1 failures A-G. Port of Dan's export_outputs.py (RUN_2026-08-25).

A bare count of failures is not actionable: it mixes "the rule row is wrong" with
"this box is wrong". The matched-control method separates them -- compare an order
against IN-SCOPE orders sharing the SAME box parents and the SAME EX-/CEX- slot lines.

  A  over by exactly the customer's PAID a-la-carte child lines. The rule set has no
     concept of a paid child, so the engine counts them as curation.
  D  short because an order edit zeroed lines (currentQuantity 0).
  E  too few matched peers to call -- fewer than CTL_MIN share this signature.
  G  rule-set gap: matched peers ship the same count, so the ROW is wrong, not the box.
  F  REAL EXCEPTION.

🔴 Controls use FREE children only and exclude any order carrying a paid child, so an
a-la-carte line cannot inflate either side of the comparison.
"""
from __future__ import annotations
import collections
import statistics

CHILD = ("AC-", "MT-", "CH-", "TR-")
CTL_MIN = 5


def _lines(node):
    return [e["node"] for e in node["lineItems"]["edges"]]


def _amt(li):
    return float((li.get("discountedUnitPriceSet") or {}).get("shopMoney", {}).get("amount") or 0)


def paid_children(node):
    return sum(li["currentQuantity"] for li in _lines(node)
               if (li.get("currentQuantity") or 0) > 0
               and (li["sku"] or "").startswith(CHILD) and _amt(li) > 0)


def zeroed(node):
    return sum(1 for li in _lines(node)
               if (li.get("quantity") or 0) > 0 and (li.get("currentQuantity") or 0) == 0)


def zeroed_children(node):
    """Zeroed CHILD units only -- the ones that could explain a shortfall."""
    return sum(li.get("quantity") or 0 for li in _lines(node)
               if (li.get("quantity") or 0) > 0 and (li.get("currentQuantity") or 0) == 0
               and (li["sku"] or "").startswith(CHILD))


def _feats(node):
    """-> ((box parents, slot signature), free_children, paid_children)."""
    box, slots, free, paid = set(), collections.Counter(), 0, 0
    for li in _lines(node):
        s = (li["sku"] or "").strip()
        q = li.get("currentQuantity") or 0
        if q <= 0 or not s:
            continue
        amt = _amt(li)
        if s.startswith(("AHB-", "BL-")):
            box.add(s)
        elif s.startswith(("EX-", "CEX-", "PR-")):
            slots[s] += q
        if s.startswith(CHILD):
            paid += q if amt > 0 else 0
            free += q if amt == 0 else 0
    return (tuple(sorted(box)), tuple(sorted(slots.items()))), free, paid


def categorize(report, orders):
    """-> list of dicts, one per c1_fail row, with category + control evidence."""
    scope = set(report.get("scope", []))
    F = {o: _feats(orders[o]) for o in scope if o in orders}
    ctl = collections.defaultdict(list)
    for _, (key, free, paid) in F.items():
        if paid == 0:                      # a paid a-la-carte line cannot inflate a control
            ctl[key].append(free)
    med = {k: statistics.median(v) for k, v in ctl.items() if len(v) >= CTL_MIN}

    rows = []
    for x in sorted(report.get("c1_fail", []), key=lambda z: z["order"]):
        oid = x["order"]
        node = orders.get(oid)
        if not node:
            continue
        exp = x.get("expected_total")
        delta = (x["total"] - exp) if exp else ""
        pc, z = paid_children(node), zeroed(node)
        zc = zeroed_children(node)
        key, free, _ = F[oid]
        m = med.get(key)
        n_ctl = len(ctl.get(key, []))
        excess = "" if m is None else free - m

        if isinstance(delta, int) and delta > 0 and pc and delta == pc:
            cat = "A. paid a-la-carte add-ons"
        # 🔴 D must ACCOUNT for the shortfall, not merely coexist with it. Without the
        # magnitude test a single zeroed line explained any size of short: #145433 is
        # -2 with ONE zeroed line and is a genuine exception (Kurt 2026-08-28 - it is
        # owed CH-SOT and AC-MFJ), but D swallowed it.
        elif isinstance(delta, int) and delta < 0 and zc >= -delta:
            cat = "D. lines zeroed by an order edit"
        elif m is None:
            cat = "E. too few matched peers to call"
        elif excess == 0:
            cat = "G. rule-set gap - matched peers ship the same count"
        else:
            cat = "F. REAL EXCEPTION"

        rows.append({"order": oid, "category": cat, "delta": delta,
                     "children": x["total"], "expected": exp,
                     "parents": ",".join(x["parents"]),
                     "child_mix": " ".join(f"{k}{v}" for k, v in sorted(x["child"].items())),
                     "paid_children": pc, "zeroed_lines": z, "zeroed_children": zc,
                     "problem": "; ".join(x["problems"]),
                     "discount_codes": ",".join(x["disc"] or []),
                     "matched_peers": n_ctl,
                     "peer_median_free_children": m if m is not None else "",
                     "excess_vs_peers": excess})
    return rows
