"""Add free AC-KETT + AC-RHB to unfulfilled AHB-X4JUL orders missing AC-KETT.

Targets sourced from _outputs/cache/ahb_x4jul_addacc_targets.json (built by scan).
Both variants are the $0.00 (free / curation) versions — customer charge = $0.

Usage:
    python add_acc_to_x4jul.py            # DRY RUN (default) — prints intended edits
    python add_acc_to_x4jul.py --apply    # execute live order edits
"""
import sys, time, json, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "AppyHourMCP"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "InventoryReorder"))
from utils import get_shopify_auth
import requests

KETT_GID = "gid://shopify/ProductVariant/52336226828568"  # AC-KETT  $0
RHB_GID  = "gid://shopify/ProductVariant/50051145695512"  # AC-RHB   $0
TARGETS = Path(r"C:\Users\Work\Claude Projects\_outputs\cache\ahb_x4jul_addacc_targets.json")

class GqlError(Exception):
    pass

def gql(base, headers, query, variables):
    for a in range(5):
        try:
            r = requests.post(f"{base}/graphql.json", headers=headers,
                              json={"query": query, "variables": variables}, timeout=45)
            if r.status_code == 429:
                time.sleep(3); continue
            d = r.json()
            if "data" not in d or d.get("data") is None:
                errs = d.get("errors", d)
                # throttled? retry
                if any("THROTTLED" in str(e).upper() or "throttl" in str(e).lower()
                       for e in (errs if isinstance(errs, list) else [errs])):
                    time.sleep(3); continue
                raise GqlError(str(errs)[:300])
            return d
        except requests.exceptions.RequestException:
            if a == 4: raise
            time.sleep(2 * (a + 1))
    raise GqlError("retries exhausted")

BEGIN = "mutation($id:ID!){orderEditBegin(id:$id){calculatedOrder{id} userErrors{message}}}"
ADD   = ("mutation($id:ID!,$v:ID!){orderEditAddVariant(id:$id,variantId:$v,quantity:1,allowDuplicates:true)"
         "{calculatedOrder{id} userErrors{message}}}")
COMMIT= ("mutation($id:ID!){orderEditCommit(id:$id,notifyCustomer:false,"
         "staffNote:\"Added free AC-KETT + AC-RHB (X4JUL accompaniments)\")"
         "{order{id} userErrors{message}}}")

def edit_order(base, headers, oid):
    try:
        return _edit_order(base, headers, oid)
    except GqlError as e:
        return False, f"gql: {e}"

def _already_has_both(base, headers, oid):
    r = requests.get(f"{base}/orders/{oid}.json", headers=headers,
                     params={"fields": "line_items"}, timeout=30)
    skus = {li.get("sku") for li in r.json().get("order", {}).get("line_items", [])
            if li.get("fulfillable_quantity", li.get("quantity", 0)) > 0}
    return "AC-KETT" in skus and "AC-RHB" in skus

def _edit_order(base, headers, oid):
    if _already_has_both(base, headers, oid):
        return True, "skip (already has both)"
    gid = f"gid://shopify/Order/{oid}"
    d = gql(base, headers, BEGIN, {"id": gid})
    be = d["data"]["orderEditBegin"]
    if be["userErrors"]:
        return False, "begin: " + "; ".join(e["message"] for e in be["userErrors"])
    calc = be["calculatedOrder"]["id"]
    for v in (KETT_GID, RHB_GID):
        d = gql(base, headers, ADD, {"id": calc, "v": v})
        ae = d["data"]["orderEditAddVariant"]
        if ae["userErrors"]:
            return False, "add: " + "; ".join(e["message"] for e in ae["userErrors"])
    d = gql(base, headers, COMMIT, {"id": calc})
    ce = d["data"]["orderEditCommit"]
    if ce["userErrors"]:
        return False, "commit: " + "; ".join(e["message"] for e in ce["userErrors"])
    return True, "ok"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute (default is dry run)")
    args = ap.parse_args()

    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    base, headers = get_shopify_auth()

    print(f"{'APPLY' if args.apply else 'DRY RUN'} - {len(targets)} orders")
    if not args.apply:
        for t in targets:
            print(f"  would edit {t['name']} (id {t['id']}): +AC-KETT +AC-RHB")
        print("Re-run with --apply to execute.")
        return

    ok = []; fail = []
    for i, t in enumerate(targets):
        success, msg = edit_order(base, headers, t["id"])
        (ok if success else fail).append((t["name"], msg))
        print(f"  {t['name']}: {'OK' if success else 'FAIL ' + msg}")
        time.sleep(0.15)
        if (i + 1) % 40 == 0:
            time.sleep(2)
    print(f"\nDONE: {len(ok)} ok, {len(fail)} failed")
    if fail:
        print("FAILED:")
        for n, m in fail:
            print(f"  {n}: {m}")

if __name__ == "__main__":
    main()
