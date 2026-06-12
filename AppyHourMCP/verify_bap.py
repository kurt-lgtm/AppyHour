"""Verify all 110 tagged orders kept their original tags + bap."""
import sys
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

EXPECTED_CORE = {"_SHIP_2026-04-27", "RMFG", "bap"}

def main():
    base, headers = get_shopify_auth()
    gql_url = f"{base}/graphql.json"
    issues = []
    counts = {"has_bap": 0, "has_ship": 0, "has_rmfg": 0, "tag_lt_3": 0}
    sample_tags = []
    for i in range(0, len(NAMES), 25):
        chunk = NAMES[i:i+25]
        q = " OR ".join(f"name:{n}" for n in chunk)
        query = '''query($q: String!) {
            orders(first: 50, query: $q) {
                edges { node { name tags } }
            }
        }'''
        r = requests.post(gql_url, json={"query": query, "variables": {"q": q}}, headers=headers, timeout=30)
        r.raise_for_status()
        for edge in r.json().get("data", {}).get("orders", {}).get("edges", []):
            n = edge["node"]
            tags = set(n.get("tags") or [])
            if "bap" in tags: counts["has_bap"] += 1
            if "_SHIP_2026-04-27" in tags: counts["has_ship"] += 1
            if "RMFG" in tags: counts["has_rmfg"] += 1
            if len(tags) < 3:
                counts["tag_lt_3"] += 1
                issues.append({"name": n["name"], "tags": sorted(tags)})
            if len(sample_tags) < 3:
                sample_tags.append({"name": n["name"], "tags": sorted(tags)})
    print(f"Total names: {len(NAMES)}")
    print(f"Counts: {counts}")
    print(f"Sample tags:")
    for s in sample_tags:
        print(f"  {s['name']}: {s['tags']}")
    if issues:
        print(f"\n⚠️ {len(issues)} orders with <3 tags (suspicious):")
        for it in issues[:10]:
            print(f"  {it}")

if __name__ == "__main__":
    main()
