"""Bundle explosion check: did every bundle parent's components reach the order?

🔴 THE MOTIVATING MISS (#178568, RMFG_20260828). A Simple Bundles parent can carry
NO SKU AT ALL -- 'Ultimate Add-on Package: Summer Cookout', $28, sku=None. Every other
check in this package keys on SKU: a null-SKU line adds 0 to the child count, matches no
rule in the RULE SET, resolves to no parent, and is therefore INVISIBLE. Kurt found it by
eye in the Shopify admin; the suite reported the order clean. 262 such lines were live on
RMFG_20260828 alone.

So the parent is identified by VARIANT ID, not SKU, and the recipe is read from the
variant's simple_bundles.bundled_variants metafield -- the same authority
InventoryReorder._get_bundle_recipe uses. Never infer a recipe from a title.

🔴 CEX-<slot> satisfies an EX-<slot> component and vice versa (rules.slot_key).
Comparing raw SKUs called #181468 and #181629 un-exploded when both were complete: the
recipe says EX-EA, the order carries CEX-EA. Kurt: "effectively the same".

A parent with NO recipe is reported separately from one whose components are MISSING. The
box-parent offers ('AppyHour Box + Free Brie for a Year') have no bundle recipe at all --
nothing to explode -- but they are still null-SKU and still invisible to every other
check, which is worth seeing.
"""
from __future__ import annotations

import json
import os
import sys

_MCP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AppyHourMCP")
for _p in (_MCP, os.path.join(_MCP, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .rules import slot_key

BUNDLE_PREFIX = ("BL-", "AHB-X")

RECIPE_Q = ('query($id:ID!){node(id:$id){... on ProductVariant{sku title '
            'metafield(namespace:"simple_bundles",key:"bundled_variants"){value}}}}')

# A null-SKU line with NO bundle recipe is usually a box OFFER, not a bundle: its variant
# title is the box size and the real parent is a separate AHB-MED / AHB-LGE line on the
# same order (Kurt 2026-09-08). #181498 'AppyHour Box + Free Brie for a Year' / variant
# 'Medium (Serves 2-4)' sits beside AHB-MED; #181557 Large beside AHB-LGE. That is
# correct, not a miss -- but if the matching AHB parent is ABSENT the box has no parent
# at all, which no SKU-keyed check can see.
#
# AHB-CMED is ALSO a valid Medium parent (Kurt 2026-09-11, #183392) -- a single-SKU map
# called that order "offer with NO AHB parent" when its parent was right there.
OFFER_PARENT = {"medium": ("AHB-MED", "AHB-CMED"), "large": ("AHB-LGE",)}


def _offer_parent(variant_title):
    """-> tuple of AHB parents that satisfy this box offer, or None."""
    t = (variant_title or "").strip().lower()
    for k, skus in OFFER_PARENT.items():
        if t.startswith(k):
            return skus
    return None


def _recipe(base, headers, vid, cache):
    """-> {component_sku: qty_per_parent} from the variant metafield. {} when none."""
    if vid in cache:
        return cache[vid]
    from order_edit import shopify_graphql
    node = shopify_graphql(base, headers, RECIPE_Q, {"id": vid}).get("node") or {}
    mf = node.get("metafield")
    out = {}
    if mf:
        for c in json.loads(mf["value"]):
            if c.get("sku"):
                out[c["sku"]] = out.get(c["sku"], 0) + int(c.get("quantity_in_bundle") or 1)
    cache[vid] = (out, node.get("title"))
    return cache[vid]


def is_bundle_line(sku, title=None):
    """A line worth asking the metafield about: a BL-/AHB-X SKU, or NO SKU at all."""
    s = (sku or "").strip().upper()
    return (not s) or s.startswith(BUNDLE_PREFIX)


def bundle_check(orders, base=None, headers=None, verbose=True):
    """-> rows for every bundle parent whose components did not all land on the order.

    Rows carry state: 'missing components' (actionable), 'no recipe' (a null-SKU line
    with nothing to explode -- still invisible to the SKU-keyed checks), 'no variant'.
    Clean parents are not reported.
    """
    if base is None:
        from appyhour_lib.credentials import get_shopify_auth
        base, headers = get_shopify_auth()
    cache, rows, seen = {}, [], 0
    from .checks import in_scope
    for o in orders.values():
        # Gift / PR box / reship / cancelled: a gift's Shopify lines are stale by
        # construction (Kurt 2026-09-11, "they won't be in shopify") -- a missing bundle
        # component on one is expected, not a finding.
        if not in_scope(o)[0]:
            continue
        live, cand = set(), []
        for e in o["lineItems"]["edges"]:
            n = e["node"]
            if n["currentQuantity"] <= 0:
                continue
            sk = (n.get("sku") or "").strip()
            if sk:
                live.add(slot_key(sk))
            cand.append((sk, n.get("title") or "", (n.get("variant") or {}).get("id"),
                         n["currentQuantity"]))
        for sk, title, vid, qty in cand:
            if not is_bundle_line(sk, title):
                continue
            seen += 1
            row = {"order": o.get("name"), "sku": sk or "(no sku)", "title": title[:60]}
            if not vid:
                rows.append({**row, "state": "no variant", "missing": ""})
                continue
            rec, vtitle = _recipe(base, headers, vid, cache)
            if not rec:
                parent = _offer_parent(vtitle) if not sk else None
                if parent:
                    if any(p in live for p in parent):
                        continue        # box offer, real AHB parent present -- correct
                    rows.append({**row, "state": "🔴 box offer, NO AHB parent",
                                 "missing": "/".join(parent)})
                else:
                    rows.append({**row, "state": "no recipe", "missing": ""})
                continue
            miss = {c: p * qty for c, p in rec.items() if slot_key(c) not in live}
            if miss:
                rows.append({**row, "state": "missing components",
                             "missing": " ".join(f"{k}x{v}" for k, v in sorted(miss.items()))})
    if verbose:
        bad = sum(1 for r in rows if r["state"] == "missing components")
        noparent = sum(1 for r in rows if "NO AHB parent" in r["state"])
        print(f"    bundles       {seen} parent lines · {bad} missing components · "
              f"{noparent} offer w/o AHB parent · "
              f"{sum(1 for r in rows if r['state'] == 'no recipe')} unclassified")
    return rows
