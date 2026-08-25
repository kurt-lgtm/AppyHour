"""Count checks against RULE SET. 🔴 Read ORDER_CHECKS_RULES.md before changing anything."""
from __future__ import annotations
import collections
from .rules import (CHILD, SLOT_TYPE, PARTY, BOARD, net, resolve_box, box_expect,
                    paid_pack_items)

EXCLUDED_TAGS = ("gift redemption", "pr box")


def sku(x):     return (x.get("sku") or "").strip().upper()
def live(o):    return [x for x in o["line_items"] if x["current_quantity"] > 0]
def tags(o):    return [t.strip() for t in (o.get("tags") or "").split(",")]


def in_scope(o):
    """Reship / Gift Redemption / PR box / cancelled are out of SKU-count checks."""
    ts = [t.lower() for t in tags(o)]
    if any(t.startswith("reship") for t in ts):   return False, "reship"
    for t in EXCLUDED_TAGS:
        if t in ts:                               return False, t
    if o.get("cancelled_at"):                     return False, "cancelled"
    return True, ""


def child_counts(o):
    per_type, per_sku = collections.Counter(), collections.Counter()
    for x in live(o):
        s = sku(x)
        for p in CHILD:
            if s.startswith(p):
                per_type[p] += x["current_quantity"]
                per_sku[s]  += x["current_quantity"]
    return per_type, per_sku


def evaluate(o, rs):
    """-> dict(verdict=OK|SHORT|OVER|UNBUILT|NO_BOX|DEFERRED, ...)."""
    li = live(o)
    boxes = [b for b in (resolve_box(x) for x in li) if b]
    if not boxes:
        return {"verdict": "NO_BOX"}

    any_tot, typed, mode = 0, collections.Counter(), None
    for x in li:                                   # 🔴 x parent QUANTITY (#176563)
        b = resolve_box(x)
        if not b:
            continue
        q = x["current_quantity"]
        a, t, deferred = box_expect(b, rs)
        if deferred:
            return {"verdict": "DEFERRED", "box": ",".join(boxes)}
        if a is not None:
            any_tot += a * q; mode = "ANY"
        if t:
            mode = "TYPED"
            for k, v in t.items():
                typed[k] += v * q
    if mode is None:
        return {"verdict": "DEFERRED", "box": ",".join(boxes)}

    # a REMOVED priced board forfeits its components' allowance (#176565)
    dead = set()
    for x in o["line_items"]:
        if x["current_quantity"] == 0 and net(x) > 0:
            dead.update(BOARD.get(sku(x), ()))

    allowance, parents = 0, []
    for x in li:
        s, q, paid = sku(x), x["current_quantity"], net(x)
        if s.startswith("PR-CJAM"):
            any_tot += 2 * q; typed["CH-"] += q; typed["AC-"] += q; parents.append(s)
        elif s == "EX-PS":
            for k, n in PARTY.items():
                typed[k] += n * q
            any_tot += sum(PARTY.values()) * q; parents.append("EX-PS")
        elif s in SLOT_TYPE:
            parents.append(f"{s}{'($)' if paid > 0 else '[slot]'}")
            if paid > 0:                           # priced parent = ADD; $0 = nested slot
                any_tot += q; typed[SLOT_TYPE[s]] += q
        elif s.startswith(CHILD) and paid > 0 and s not in dead:
            allowance += q
        allowance += paid_pack_items(x)
    for dc in (o.get("discount_codes") or []):
        for k, n in rs["disc"].get((dc.get("code") or "").lower(), {}).items():
            typed[k] += n; any_tot += n; parents.append(f"disc:{dc.get('code')}")

    act, per_sku = child_counts(o)
    tot = sum(act.values())
    base = {"box": ",".join(boxes), "actual": tot, "allowance": allowance,
            "children": " ".join(f"{k[:-1]}{v}" for k, v in sorted(act.items())),
            "parents": " ".join(parents) or "-"}

    if tot == 0:                                   # runs AFTER children are applied
        return {"verdict": "UNBUILT", "expected": any_tot or sum(typed.values()), **base}

    if mode == "TYPED":
        exp = sum(typed.values())
        short = {k: v - act[k] for k, v in typed.items() if act[k] < v}
        over  = sum(act[k] - v for k, v in typed.items() if act[k] > v)
        if short:
            return {"verdict": "SHORT", "expected": exp,
                    "detail": " ".join(f"{k[:-1]}-{v}" for k, v in short.items()), **base}
        if over > allowance:
            return {"verdict": "OVER", "expected": exp,
                    "detail": f"+{over} vs allowance {allowance}", **base}
        return {"verdict": "OK"}

    d = tot - any_tot
    if d < 0:
        return {"verdict": "SHORT", "expected": any_tot, "detail": str(d), **base}
    if d > allowance:
        return {"verdict": "OVER", "expected": any_tot,
                "detail": f"+{d} vs allowance {allowance}", **base}
    return {"verdict": "OK"}


def duplicate_check(o):
    """Check 8 — same child SKU twice with a TYPE-MATCHED CEX-/EX- parent.

    Exclusions: any line of that SKU priced (bought a second one), 2+ paid AHB- parents,
    BOX_CUSTOMIZED_POST_CHECKOUT. TR- piles are legal (#176563).
    """
    if any(t == "BOX_CUSTOMIZED_POST_CHECKOUT" for t in tags(o)):
        return []
    li = live(o)
    if sum(1 for x in li if sku(x).startswith("AHB-") and net(x) > 0) >= 2:
        return []
    cnt, priced = collections.Counter(), set()
    for x in li:
        s = sku(x)
        if s.startswith(CHILD):
            cnt[s] += x["current_quantity"]
            if net(x) > 0:
                priced.add(s)
    par = [sku(x) for x in li if sku(x) in SLOT_TYPE]
    out = []
    for s, n in cnt.items():
        if n < 2 or s in priced or s.startswith("TR-"):
            continue
        m = sorted({p for p in par if s.startswith(SLOT_TYPE[p])})
        if m:
            out.append((s, ",".join(m)))
    return out
