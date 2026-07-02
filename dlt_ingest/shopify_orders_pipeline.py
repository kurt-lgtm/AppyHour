"""Isolated dlt pipeline skeleton: Shopify orders -> OWN sqlite (dlt_ingest.db).

Additive + isolated. NEVER touches shipping.db. Import-clean + no-op unless run with
``--run``. Creds come ONLY from appyhour_lib.credentials.get_shopify_auth() (never
hardcoded). See DLT_INGEST_RULES.md (constraints SSOT) before changing.

Run (only path that hits the Shopify API):
    python dlt_ingest/shopify_orders_pipeline.py --run
"""

from __future__ import annotations

import sys
from pathlib import Path

import dlt
from dlt.sources.helpers import requests

# This pipeline's OWN sqlite lives beside this file — NEVER shipping.db.
_HERE = Path(__file__).resolve().parent
DLT_DB_PATH = _HERE / "dlt_ingest.db"
# dlt sqlite destination takes a file path WITHOUT the .db suffix (it appends it).
_DEST_PATH = str(DLT_DB_PATH.with_suffix(""))


@dlt.resource(name="orders", write_disposition="replace", primary_key="id")
def shopify_orders(page_size: int = 250):
    """Yield Shopify orders via Admin REST, following Link-header ``rel="next"``.

    Read-only GET. Creds resolved lazily from the canonical single source so that
    importing this module (or running without --run) makes ZERO network calls.
    """
    # Local import keeps module import side-effect-free and API-free.
    import os

    _repo_root = _HERE.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    from appyhour_lib.credentials import get_shopify_auth  # canonical single source

    base, headers = get_shopify_auth()
    url = f"{base}/orders.json"
    params = {"status": "any", "limit": page_size}

    while url:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
        if not orders:
            break
        yield orders

        # Shopify Admin REST pagination is Link-header based (NOT page=N).
        link = resp.headers.get("Link", "") or resp.headers.get("link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                seg = part.split(";")[0].strip()
                next_url = seg.strip("<> ")
        url = next_url
        params = None  # the next URL already carries page_info/limit


def build_pipeline() -> "dlt.Pipeline":
    """Construct the isolated pipeline. No API call happens here."""
    return dlt.pipeline(
        pipeline_name="appyhour_shopify_orders",
        destination=dlt.destinations.sqlite(_DEST_PATH),
        dataset_name="shopify",
        dev_mode=True,  # isolated dataset per run while the schema is iterated
    )


def run() -> None:
    """Execute the pull. ONLY reachable via the --run flag."""
    pipe = build_pipeline()
    info = pipe.run(shopify_orders(), workers=1)  # single writer to the sqlite file
    print(info)
    print(f"Wrote to isolated db: {DLT_DB_PATH}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--run" not in argv:
        print(
            "dlt Shopify-orders ingest skeleton (no-op).\n"
            "  Writes to its OWN sqlite: "
            f"{DLT_DB_PATH}\n"
            "  NEVER touches shipping.db. Creds via appyhour_lib.get_shopify_auth().\n"
            "  Pass --run to actually pull from Shopify (hits the API):\n"
            "    python dlt_ingest/shopify_orders_pipeline.py --run"
        )
        return 0
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
