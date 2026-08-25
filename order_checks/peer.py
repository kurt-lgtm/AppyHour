"""Peer-group outlier check — second opinion with no rule-set dependency.

🔴 Only valid on BUILT orders. Unscoped on _SHIP_2026-08-31 it returned 341 outliers,
200+ of them orders created after the sheet with zero children. Scope first.
"""
from __future__ import annotations
import collections
from .checks import live, sku, in_scope
from .rules import CHILD, resolve_box

MIN_PEERS = 8


def peer_outliers(orders, min_peers: int = MIN_PEERS):
    """-> {order_name: dict(box, ships, mode, delta, peers, at_mode, note)}"""
    recs = []
    for o in orders:
        ok, _ = in_scope(o)
        if not ok:
            continue
        li = live(o)
        boxes = sorted(b for b in (resolve_box(x) for x in li) if b)
        if not boxes:
            continue
        n = sum(x["current_quantity"] for x in li if sku(x).startswith(CHILD))
        if n == 0:                      # unbuilt - the count check reports this, not peers
            continue
        recs.append(("+".join(boxes), n, o))

    grp = collections.defaultdict(list)
    for k, n, _ in recs:
        grp[k].append(n)

    out = {}
    for k, n, o in recs:
        peers = grp[k]
        if len(peers) < min_peers:
            continue
        mode, at_mode = collections.Counter(peers).most_common(1)[0]
        if n == mode:
            continue
        out[o["name"]] = {"box": k, "ships": n, "mode": mode, "delta": n - mode,
                          "peers": len(peers) - 1, "at_mode": at_mode,
                          "note": f"{at_mode} of {len(peers)-1} peers ship {mode}"}
    return out
