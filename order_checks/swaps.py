"""Swap-list construction: draw-downs, usage caps, forced replacements, reverts.

Distinct from check7, which produces REPEAT swaps. Everything here is inventory- or
operator-driven:

  DRAW_DOWN    target units LEFT on the shelf; the excess comes out of boxes.
  USAGE_CAP    target units USED this run. 🔴 A cap is a PLANNING TARGET, not permission
               to swap: Kurt asked for ~400 CH-SOT, saw the 101-row list, and said "if you
               mean to swap them to get to 400, no." Only act on a cap when told.
  FORCED_SWAP  one-for-one replacement that ignores ranking.
  reverts      undo an operator's own earlier assignment (the 08-28 AC-BLUCAR case).

🔴 Every list this module produces must pass `checks.validate_swap_list` before it is
applied. A draw-down picks rows by SKU with no tag awareness, which is exactly how a gift
(#175930) reached an applied list on 2026-08-28.
"""
from __future__ import annotations
import collections
import sqlite3

from .check7 import (MINI_JAMS, NO_SUBSTITUTE, REPEAT_EXEMPT, RESERVE_FLOOR,
                     _ever_received, build_cracker_set, load_have, sheet_demand,
                     sku_first_seen, typ)
from .checks import write_blocked
from .customer_map import recharge_id
from .history import DB, previous_orders
from .recharge_gate import customized


def _live_skus(o):
    return {(e["node"]["sku"] or "").strip()
            for e in o["lineItems"]["edges"] if (e["node"].get("currentQuantity") or 0) > 0}


def candidate_pool(demand, have, crackers, first_seen, want_type, exclude=()):
    """Substitutes of one type, ranked by HEADROOM then recency.

    🔴 Headroom first, not newest first. Newest-first buried AC-BRJA (2,284 on hand, 60
    committed -- the substitute Kurt's declared list uses 175 times) behind two newer SKUs
    and poured one new item across the whole run.
    """
    pool = [s for s in demand
            if typ(s, crackers) == want_type and s not in REPEAT_EXEMPT
            and s not in NO_SUBSTITUTE and s not in MINI_JAMS and s not in exclude]
    pool.sort(key=lambda s: (have.get(s, 0) - demand.get(s, 0), first_seen.get(s, "")),
              reverse=True)
    return pool


def build(orders, sheet, targets, con=None, respect_customized=True, verbose=True):
    """targets: {sku_out: (why, units_to_remove)} -> list of swap rows.

    respect_customized: never pull an item out of a box the CUSTOMER built. Kurt
    2026-08-28: "we don't want to remove them if people had them in their order already."
    """
    close = con is None
    con = con or sqlite3.connect(DB)
    have, demand = load_have(), sheet_demand(sheet)
    crackers = build_cracker_set(orders)
    first_seen = sku_first_seen(con)
    remaining = {s: have.get(s, 0) - demand.get(s, 0) for s in set(have) | set(demand)}

    rows, notes = [], collections.Counter()
    for sku_out, (why, need) in targets.items():
        if need <= 0:
            notes[f"{sku_out}: nothing to do"] += 1
            continue
        pool = candidate_pool(demand, have, crackers, first_seen,
                              typ(sku_out, crackers), exclude={sku_out})
        made = 0
        for oid, o in orders.items():
            if made >= need:
                break
            if write_blocked(o):                       # pr box / reship / gift
                notes["blocked order skipped"] += 1
                continue
            box = _live_skus(o)
            if sku_out not in box:
                continue
            if respect_customized:
                tags = o.get("tags") or []
                if "BOX_CUSTOMIZED_POST_CHECKOUT" in tags:
                    notes["customer-built box skipped"] += 1
                    continue
                was, _ = customized(con, recharge_id(con, (o.get("customer") or {}).get("id")))
                if was:
                    notes["customer-built box skipped"] += 1
                    continue
            gid = (o.get("customer") or {}).get("id")
            ever = _ever_received(con, gid, pool) if gid else set()
            cand = next((s for s in pool if s not in box and s not in ever
                         and remaining.get(s, 0) > RESERVE_FLOOR), None)
            if not cand:
                notes[f"{sku_out}: no eligible substitute"] += 1
                continue
            remaining[cand] -= 1
            remaining[sku_out] = remaining.get(sku_out, 0) + 1
            made += 1
            rows.append({"Order ID": oid, "SKU to Swap": sku_out,
                         "Proposed Swap": cand, "Flag": why})
        if verbose:
            print(f"  {sku_out}: have {have.get(sku_out, 0)}, sheet {demand.get(sku_out, 0)}"
                  f" -> {why}; need {need}, produced {made}")
    if verbose:
        for k, v in notes.most_common():
            print(f"    {v:>4}  {k}")
    if close:
        con.close()
    return rows


def draw_down_targets(sheet, floors):
    """{sku: units_left_wanted} -> targets for build(). Units come OUT of boxes."""
    have, demand = load_have(), sheet_demand(sheet)
    return {s: (f"draw down to {n} left", demand.get(s, 0) - (have.get(s, 0) - n))
            for s, n in floors.items()}


def revert_targets(assignments, sheet):
    """{sku: n_assigned} -> targets that undo an operator's own earlier assignment."""
    demand = sheet_demand(sheet)
    return {s: ("revert operator assignment", min(n, demand.get(s, 0)))
            for s, n in assignments.items()}
