# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Swap wrong CH-IPAC/CH-BLR additions to correct cheese+jam per parent SKU.

28 orders total (SS skipped — already correct):
- 17 IPAC orders: remove CH-IPAC + AC-RPJ, add correct per parent
- 11 BLR orders (non-SS, non-MONG): remove CH-BLR + AC-BLBALS, add correct per parent

All edits are _rc_bundle (curation). No refund.

Usage:
    python swap_bad_ipac_blr.py             # dry-run
    python swap_bad_ipac_blr.py --commit    # apply
"""
from __future__ import annotations
import sys, time, json
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

BASE, HEADERS = get_shopify_auth()
GQL = f"{BASE}/graphql.json"
COMMIT = "--commit" in sys.argv

# Parent SKU → correct (cheese, [jam SKUs with qty])
PARENT_MAP: dict[str, dict] = {
    "ALPN":  {"cheese": "CH-6COM",  "jams": [("AC-FOJ", 1)]},
    "BYO":   {"cheese": "CH-FONT",  "jams": [("AC-RSPM", 1)]},
    "GEN":   {"cheese": "CH-FAG",   "jams": [("AC-RPJ", 1)]},
    "HHIGH": {"cheese": "CH-HCGU",  "jams": [("AC-RSPM", 1)]},
    "ISUN":  {"cheese": "CH-UBRI",  "jams": [("AC-BLBALS", 1)]},
    "MDT":   {"cheese": "CH-LOU",   "jams": [("AC-CFPH", 1)]},
    "MONG":  {"cheese": "CH-BLR",   "jams": [("AC-BLBALS", 1)]},
    "NMS":   {"cheese": "CH-FONT",  "jams": [("AC-RSPM", 1)]},
    "SPN":   {"cheese": "CH-SMG",   "jams": [("AC-GBEF", 1)]},
}

# (order_name, parent_short, [wrong_skus_to_remove])
JOBS = [
    # 17 IPAC orders: remove CH-IPAC + AC-RPJ
    ("139672", "HHIGH", ["CH-IPAC","AC-RPJ"]),
    ("139676", "GEN",   ["CH-IPAC","AC-RPJ"]),
    ("140066", "ISUN",  ["CH-IPAC","AC-RPJ"]),
    ("140174", "SPN",   ["CH-IPAC","AC-RPJ"]),
    ("140395", "NMS",   ["CH-IPAC","AC-RPJ"]),
    ("140451", "NMS",   ["CH-IPAC","AC-RPJ"]),
    ("140458", "NMS",   ["CH-IPAC","AC-RPJ"]),
    ("141544", "ALPN",  ["CH-IPAC","AC-RPJ"]),
    ("141592", "NMS",   ["CH-IPAC","AC-RPJ"]),
    ("141600", "ALPN",  ["CH-IPAC","AC-RPJ"]),
    ("141644", "BYO",   ["CH-IPAC","AC-RPJ"]),
    ("141723", "SPN",   ["CH-IPAC","AC-RPJ"]),
    ("141770", "NMS",   ["CH-IPAC","AC-RPJ"]),
    ("141821", "BYO",   ["CH-IPAC","AC-RPJ"]),
    ("141855", "HHIGH", ["CH-IPAC","AC-RPJ"]),
    ("141857", "HHIGH", ["CH-IPAC","AC-RPJ"]),
    ("141885", "MONG",  ["CH-IPAC","AC-RPJ"]),
    # 11 BLR orders (non-SS, non-MONG): remove CH-BLR + AC-BLBALS
    ("140495", "ALPN",  ["CH-BLR","AC-BLBALS"]),
    ("141080", "ALPN",  ["CH-BLR","AC-BLBALS"]),
    ("141088", "HHIGH", ["CH-BLR","AC-BLBALS"]),
    ("141232", "BYO",   ["CH-BLR","AC-BLBALS"]),
    ("141304", "ALPN",  ["CH-BLR","AC-BLBALS"]),
    ("141484", "GEN",   ["CH-BLR","AC-BLBALS"]),
    ("141495", "ALPN",  ["CH-BLR","AC-BLBALS"]),
    ("141547", "NMS",   ["CH-BLR","AC-BLBALS"]),
    ("141679", "MDT",   ["CH-BLR","AC-BLBALS"]),
    ("141702", "ALPN",  ["CH-BLR","AC-BLBALS"]),
    ("141850", "ALPN",  ["CH-BLR","AC-BLBALS"]),
]


def gql(query: str, variables: dict | None = None) -> dict:
    r = requests.post(GQL, headers=HEADERS, json={"query": query, "variables": variables or {}}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(json.dumps(d["errors"], indent=2))
    return d["data"]


def lookup_variants(skus: set[str]) -> dict[str, str]:
    """Return {sku: variant_gid} — cheapest variant per sku."""
    out: dict[str, str] = {}
    q = '''
    query($q: String!) {
      productVariants(first: 25, query: $q) {
        edges { node { id sku price } }
      }
    }
    '''
    for sku in skus:
        d = gql(q, {"q": f"sku:{sku}"})
        cands = []
        for e in d["productVariants"]["edges"]:
            n = e["node"]
            if (n.get("sku") or "").strip() == sku:
                cands.append((float(n.get("price") or "0"), n["id"]))
        if not cands:
            print(f"  WARN: no variant for {sku}")
            continue
        cands.sort()
        out[sku] = cands[0][1]
    return out


def find_order_gid(name: str) -> str | None:
    q = '''
    query($q: String!) {
      orders(first: 5, query: $q) {
        edges { node { id name } }
      }
    }
    '''
    d = gql(q, {"q": f"name:#{name}"})
    for e in d["orders"]["edges"]:
        if e["node"]["name"].lstrip("#") == name:
            return e["node"]["id"]
    return None


def begin_edit(order_gid: str) -> tuple[str, list[dict]]:
    q = '''
    mutation($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder {
          id
          lineItems(first: 100) {
            edges { node { id sku quantity title customAttributes { key value } } }
          }
        }
        userErrors { field message }
      }
    }
    '''
    d = gql(q, {"id": order_gid})
    res = d["orderEditBegin"]
    if res["userErrors"]:
        raise RuntimeError(f"begin: {res['userErrors']}")
    return res["calculatedOrder"]["id"], [e["node"] for e in res["calculatedOrder"]["lineItems"]["edges"]]


def set_qty(calc_id: str, li_id: str, qty: int) -> None:
    q = '''
    mutation($id: ID!, $lineItemId: ID!, $quantity: Int!) {
      orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity, restock: false) {
        userErrors { field message }
      }
    }
    '''
    d = gql(q, {"id": calc_id, "lineItemId": li_id, "quantity": qty})
    if d["orderEditSetQuantity"]["userErrors"]:
        raise RuntimeError(f"setQty: {d['orderEditSetQuantity']['userErrors']}")


def add_variant(calc_id: str, variant_gid: str, qty: int) -> None:
    q = '''
    mutation($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
        userErrors { field message }
      }
    }
    '''
    d = gql(q, {"id": calc_id, "variantId": variant_gid, "quantity": qty})
    if d["orderEditAddVariant"]["userErrors"]:
        raise RuntimeError(f"addVariant: {d['orderEditAddVariant']['userErrors']}")


def commit_edit(calc_id: str, note: str) -> None:
    q = '''
    mutation($id: ID!, $note: String!) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: $note) {
        userErrors { field message }
      }
    }
    '''
    d = gql(q, {"id": calc_id, "note": note})
    if d["orderEditCommit"]["userErrors"]:
        raise RuntimeError(f"commit: {d['orderEditCommit']['userErrors']}")


def is_bundle(item: dict) -> bool:
    return any((a.get("key") == "_rc_bundle") for a in (item.get("customAttributes") or []))


def process(order_name: str, parent: str, wrong_skus: list[str], variants: dict[str, str], dry: bool) -> dict:
    out = {"order": order_name, "parent": parent, "removed": [], "added": [], "errors": []}
    cfg = PARENT_MAP.get(parent)
    if not cfg:
        out["errors"].append(f"no parent map for {parent}"); return out

    gid = find_order_gid(order_name)
    if not gid:
        out["errors"].append("order not found"); return out
    calc_id, items = begin_edit(gid)

    # plan removes (1x each)
    removes = []
    for sku in wrong_skus:
        cands = [i for i in items if (i.get("sku") or "").strip() == sku and i["quantity"] > 0]
        if not cands:
            out["errors"].append(f"{sku} not present")
            continue
        m = cands[0]
        removes.append((m["id"], m["quantity"] - 1, sku, m["quantity"]))

    # plan adds — cheese + jam(s)
    adds: list[tuple[str, int, str]] = []
    if cfg["cheese"]:
        v = variants.get(cfg["cheese"])
        if not v: out["errors"].append(f"variant missing: {cfg['cheese']}")
        else: adds.append((v, 1, cfg["cheese"]))
    for jam_sku, qty in cfg["jams"]:
        v = variants.get(jam_sku)
        if not v: out["errors"].append(f"variant missing: {jam_sku}")
        else: adds.append((v, qty, jam_sku))

    if out["errors"]:
        return out

    # execute
    for li_id, new_qty, sku, cur in removes:
        out["removed"].append(f"{sku} {cur}->{new_qty}")
        if not dry:
            set_qty(calc_id, li_id, new_qty)
            time.sleep(0.2)
    for variant_gid, qty, sku in adds:
        out["added"].append(f"+{qty}x {sku}")
        if not dry:
            add_variant(calc_id, variant_gid, qty)
            time.sleep(0.2)

    if not dry:
        note = f"Swap to {parent} correct: -{','.join(wrong_skus)} +{cfg['cheese'] or ''},{','.join(j[0] for j in cfg['jams'])}"
        commit_edit(calc_id, note)
    return out


def main() -> None:
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n=== Swap bad IPAC/BLR -> correct per parent [{mode}] -- {len(JOBS)} orders ===\n")

    # Collect all SKUs needed
    skus_needed = set()
    for _, parent, _ in JOBS:
        cfg = PARENT_MAP.get(parent)
        if not cfg: continue
        if cfg["cheese"]: skus_needed.add(cfg["cheese"])
        for j, _ in cfg["jams"]: skus_needed.add(j)
    print(f"Looking up {len(skus_needed)} variants: {sorted(skus_needed)}")
    variants = lookup_variants(skus_needed)
    missing = skus_needed - set(variants)
    if missing:
        print(f"  MISSING VARIANTS: {missing}")
        if COMMIT:
            print("  ABORTING — fix missing variants first.")
            return
    print()

    ok = fail = 0
    for name, parent, wrong in JOBS:
        try:
            r = process(name, parent, wrong, variants, dry=not COMMIT)
            status = "OK" if r["removed"] and r["added"] and not r["errors"] else ("ERR" if r["errors"] else "NOOP")
            if status == "OK": ok += 1
            else: fail += 1
            print(f"  #{name} [{parent}] [{status}] rm={r['removed']} add={r['added']} err={r['errors']}")
        except Exception as e:
            fail += 1
            print(f"  #{name} [{parent}] [EXC] {e}")
        time.sleep(0.4)
    print(f"\n=== {mode} done: ok={ok} fail/noop={fail} ===")


if __name__ == "__main__":
    main()
