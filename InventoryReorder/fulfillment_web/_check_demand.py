# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Quick check: what's in the cut order interactive response.

Run-once probe against a LOCALLY RUNNING fulfillment_web (port 5187). The POST used to fire at
import time, so merely importing this module - a test collector walking the package is enough -
hit the network. Everything now sits behind main() under the __main__ guard (Plan: R-13).
"""
import json

import requests

ENDPOINT = "http://127.0.0.1:5187/api/cut_order_interactive"


def main() -> None:
    resp = requests.post(ENDPOINT, json={}, timeout=10)
    d = resp.json()
    rc = d.get('raw_components', {}).get('wk1', {})
    print(f"Wk1 direct: {len(rc.get('direct', {}))} keys")
    print(f"Wk1 prcjam_counts: {len(rc.get('prcjam_counts', {}))} keys")
    print(f"Wk1 cexec_counts: {len(rc.get('cexec_counts', {}))} keys")
    print(f"Wk2 demand: {len(d.get('wk2_demand', {}))} keys")
    print(f"SKUs: {len(d.get('skus', {}))}")

    if rc.get('prcjam_counts'):
        print(f"\nPR-CJAM counts: {json.dumps(rc['prcjam_counts'], indent=2)}")
    if rc.get('cexec_counts'):
        print(f"\nCEX-EC counts: {json.dumps(rc['cexec_counts'], indent=2)}")
    if rc.get('direct'):
        top = sorted(rc['direct'].items(), key=lambda x: -x[1])[:5]
        print(f"\nTop 5 direct: {top}")
    if not rc.get('direct') and not rc.get('prcjam_counts'):
        print("\n*** ALL WK1 COMPONENTS EMPTY ***")


if __name__ == "__main__":
    main()
