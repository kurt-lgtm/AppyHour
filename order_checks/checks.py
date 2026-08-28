"""Count checks against RULE SET. 🔴 Read ORDER_CHECKS_RULES.md before changing anything."""
from __future__ import annotations
import collections
from .rules import (CHILD, SLOT_TYPE, PARTY, BOARD, CRACKERS, route_tags,
                    FIXED_ROUTE_TAG, MILITARY_TAG, net, resolve_box, box_expect,
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
    # 🔴 AHB-X / BL- scope test runs on ALL lines, NOT just live ones. Simple Bundles
    # zeroes a paid board's parent and explodes it into components, so the board sits at
    # currentQuantity 0 while its children inflate the count. #176565 read as a +4
    # exception on exactly BL-4USA's four components; five identical AHB-LCUST-SS +
    # BL-4USA + PR-CJAM-SS orders all ship the same 15 and are correctly out of scope.
    # Gate on the line EXISTING, not on it being live. (Dan, RUN_2026-08-25.)
    if any(sku(x).startswith(("AHB-X", "BL-")) for x in o["line_items"]):
        return {"verdict": "DEFERRED", "box": "AHB-X/BL- present"}
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


def cracker_check(o):
    """A CEX-CR slot must deliver a real cracker. '' when fine.

    Invisible to a count check: #176361 (9/9) and #176392 (11/11) are full but their
    cracker slot holds something else. Ported from Dan's build_cracker_check.py.
    """
    # A gift's contents live in the Matrixify import, not the Shopify order (Kurt
    # 2026-08-28) - a bare slot here proves nothing.
    if any(t.strip().lower() == "gift redemption" for t in tags(o)):
        return ""
    liveset = {sku(x) for x in live(o)}
    if "CEX-CR" not in liveset or (liveset & CRACKERS):
        return ""
    zeroed = [sku(x) for x in o["line_items"]
              if x["quantity"] > 0 and x["current_quantity"] == 0 and sku(x) in CRACKERS]
    return ("cracker slot unfilled; cracker zeroed by an edit: " + ",".join(zeroed)
            if zeroed else "CEX-CR slot filled with a non-cracker")


# Orders that never get written for a ROTATION reason -- repeats, rebalancing, caps.
# 🔴 A REAL STOCKOUT is the one exception (Kurt 2026-08-28): "we don't fuck with reships
# or pr boxes unless its a real stockout." If the SKU genuinely is not there, the box
# cannot ship as written and a substitution is the only option -- pass
# stockout=True to say so explicitly, never as a default.
#
# The blocks have DIFFERENT reasons and that matters:
#   pr box           a RULE. Kurt 2026-08-28: "we never fuck with pr boxes." Internal
#                    sample; blocked even under an explicit override.
#   gift redemption  NOT a rule -- it is simply IMPOSSIBLE. Kurt 2026-08-28: "its not a
#                    rule because it just means its not possible." Gift orders are LOCKED
#                    in Shopify, so the edit fails; the shopify-api skill says the same
#                    ("always locked - don't retry"). Their contents are reconciled in
#                    Matrixify instead. Skip them because a write cannot land, not
#                    because policy forbids it -- an agent told "policy" will look for an
#                    override that does not exist.
#   reship           a RULE, same class as pr box: never touched to fix a repeat or to
#                    rebalance stock. A reship exists to correct a failure; changing its
#                    contents re-opens the failure.
NEVER_WRITE_TAGS = ("pr box", "gift redemption")
ROTATION_BLOCKED = ("pr box", "reship")     # overridable ONLY by a real stockout


def write_blocked(o, stockout=False):
    """Reason this order must not be edited, or '' when it may be.

    Assert this BEFORE any write -- a shortage `sub` selects rows by SKU and knows
    nothing about tags, which is how #175930 (a gift) reached an applied swap list on
    2026-08-28.

    stockout=True relaxes ONLY the rotation blocks (pr box, reship): a genuinely absent
    SKU leaves no alternative. It never unlocks a gift, which is a locked order in
    Shopify rather than a policy choice. Pass it explicitly, never by default.
    """
    ts = [t.strip().lower() for t in tags(o)]
    if "gift redemption" in ts:
        return "gift redemption order - LOCKED in Shopify, the edit cannot land"
    for t in ROTATION_BLOCKED:
        if any(x == t or x.startswith(t) for x in ts):
            if stockout:
                continue
            return f"{t} order - not touched except for a real stockout"
    return ""


def validate_swap_list(rows, orders, order_key="Order ID"):
    """Flag rows targeting an order that must never be written. Returns the bad rows.

    🔴 Run this on ANY list before applying it, including lists this package produced.
    A shortage `vf_edit sub` picks rows by SKU with no tag awareness, so a gift can enter
    a swap list that every upstream check excluded -- #175930 (Gift Redemption) reached
    the applied 08-31 set exactly that way and had to be pulled before the Shopify pass,
    where it would simply have failed as a locked order.
    """
    bad = []
    for r in rows:
        oid = str(r.get(order_key) or r.get("order") or "").strip().lstrip("#")
        o = orders.get(oid)
        if not o:
            continue
        why = write_blocked(o)
        if why:
            bad.append({**r, "blocked_reason": why})
    return bad


def bare_cex_check(o):
    """A bare CEX-EC with NO CEX-EC-<CURATION> counterpart = unresolved slot. '' when fine.

    CEX-EC (bare) + CEX-EC-{CURATION} coexisting is EXPECTED - the bare line is the
    placeholder written first, the suffixed one is its curation-specific resolution
    (SKU Quirks). Bare-only means the resolution never ran.

    🔴 Fix is to add the CEX-EC-<CURATION> line qty 1, NOT the CH- SKU (rule 11).
    Gift Redemption orders are out of scope. Found #178549 on _SHIP_2026-08-31.
    """
    if any(t.strip().lower() == "gift redemption" for t in tags(o)):
        return ""
    live_skus = {sku(x) for x in live(o)}
    if "CEX-EC" not in live_skus:
        return ""
    # count removed/zeroed suffix lines too - a zeroed counterpart is still not shipping
    all_skus = {sku(x) for x in o["line_items"]}
    if any(s.startswith("CEX-EC-") for s in all_skus):
        return ""
    return "bare CEX-EC with no CEX-EC-<CURATION> counterpart"


def fixed_route_check(o):
    """Fixed_Route pin on the customer profile must also be on the live order. '' when fine.

    🔴 The "Customer Specific Routing" Shopify Flow only fires on order_created. An order
    that ALREADY existed when the pin was set never re-triggers it, so the profile says
    pinned while the live order routes on the default carrier. Found 3 of 4 pinned
    customers in _SHIP_2026-08-31 this way (#178090, #177442, #176917 - all
    "!UPS Ground - Dallas_AHB!" on the profile, no route tag at all on the order).

    Military profiles must never land on OnTrac (Kurt 2026-08-13).

    Fix: the ORDER (and the sheet row) takes the CUSTOMER's pin - the profile is
    authoritative. Append, never overwrite.
    """
    cust = (o.get("customer") or {}).get("tags") or ""
    ot = o.get("tags") or ""
    pinned = FIXED_ROUTE_TAG in cust.lower()
    cust_route = route_tags(cust)
    order_route = route_tags(ot)

    if MILITARY_TAG in cust.lower() and any("ontrac" in r.lower() for r in order_route):
        return f"MILITARY customer routed OnTrac: {','.join(order_route)}"
    if not pinned:
        return ""
    if not cust_route:
        return "Fixed_Route on profile but no route tag to pin to"
    if not order_route:
        return f"pin {cust_route[0]} on profile, NO route tag on the order"
    if set(cust_route) != set(order_route):
        return f"pin {','.join(cust_route)} on profile, order routed {','.join(order_route)}"
    return ""


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
