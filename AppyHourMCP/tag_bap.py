"""
Bulk tag 109 orders with 'bap'. Resolves names via GraphQL (batched), applies tag via REST.
"""
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from utils import get_shopify_auth
import requests

NAMES = [
    "#132331","#132397","#132399","#132444","#132485","#132504","#132509","#132521",
    "#132527","#132532","#132550","#132551","#132552","#132553","#132559","#132562",
    "#132563","#132569","#132570","#132573","#132577","#132589","#132595","#132621",
    "#132740","#132753","#132762","#132763","#132769","#132773","#132777","#132786",
    "#132787","#132792","#132802","#132804","#132816","#132838","#132842","#132850",
    "#132973","#133027","#133029","#133046","#133055","#133067","#133077","#133083",
    "#133094","#133096","#133102","#133116","#133117","#133138","#133172","#133336",
    "#133344","#133351","#133355","#133362","#133363","#133366","#133370","#133372",
    "#133379","#133404","#133407","#133412","#133422","#133429","#133519","#133524",
    "#133559","#133732","#133762","#133771","#133774","#133818","#133844","#133845",
    "#133846","#133852","#133860","#133863","#133864","#133869","#133870","#133874",
    "#133875","#133882","#133887","#133889","#133891","#133895","#133899","#133900",
    "#133905","#133908","#133911","#133912","#133914","#133935","#133940","#133942",
    "#133956","#133961","#133969","#133977","#134035","#134060",
]

TAG = "bap"

def resolve_ids(base, headers):
    """Resolve name -> (id, current_tags) via GraphQL, batched."""
    out = {}
    gql_url = f"{base}/graphql.json"
    batch = 25  # Shopify query string limit
    for i in range(0, len(NAMES), batch):
        chunk = NAMES[i:i+batch]
        q = " OR ".join(f"name:{n}" for n in chunk)
        query = """
        query($q: String!) {
            orders(first: 50, query: $q) {
                edges { node { id name tags legacyResourceId } }
            }
        }
        """
        r = requests.post(gql_url, json={"query": query, "variables": {"q": q}}, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            print(f"  GQL errors on batch {i}: {data['errors']}", flush=True)
        for edge in data.get("data", {}).get("orders", {}).get("edges", []):
            n = edge["node"]
            out[n["name"]] = {
                "id": int(n["legacyResourceId"]),
                "tags": n.get("tags") or [],
            }
        time.sleep(0.5)
    return out

def add_tag(base, headers, order_id, existing_tags):
    """Add 'bap' tag via REST PUT."""
    tags_list = existing_tags if isinstance(existing_tags, list) else [t.strip() for t in (existing_tags or "").split(",")]
    if TAG in tags_list:
        return "already"
    new_tags = ",".join([*tags_list, TAG]).strip(",")
    url = f"{base}/orders/{order_id}.json"
    r = requests.put(url, json={"order": {"id": order_id, "tags": new_tags}}, headers=headers, timeout=30)
    if r.status_code == 200:
        return "ok"
    return f"fail:{r.status_code}:{r.text[:200]}"

def main():
    base, headers = get_shopify_auth()
    print(f"Resolving {len(NAMES)} order names...", flush=True)
    resolved = resolve_ids(base, headers)
    missing = [n for n in NAMES if n not in resolved]
    print(f"Resolved: {len(resolved)} · Missing: {len(missing)}", flush=True)
    if missing:
        print("Missing:", missing, flush=True)

    print(f"Applying tag '{TAG}' to {len(resolved)} orders...", flush=True)
    results = {"ok": 0, "already": 0, "fail": []}
    for name, info in resolved.items():
        status = add_tag(base, headers, info["id"], info["tags"])
        if status == "ok":
            results["ok"] += 1
        elif status == "already":
            results["already"] += 1
        else:
            results["fail"].append({"name": name, "id": info["id"], "status": status})
        time.sleep(0.1)  # rate-limit safety

    print(f"\nDone. ok={results['ok']} already={results['already']} fail={len(results['fail'])}", flush=True)
    if results["fail"]:
        print("Failures:", json.dumps(results["fail"], indent=2), flush=True)
    if missing:
        print("\nUnresolved names (not tagged):", missing, flush=True)

if __name__ == "__main__":
    main()
