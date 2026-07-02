"""Pre-import duplicate checker for a Matrixify line-item add CSV.

Given a Matrixify "add line item" export (one row per order+child_sku), fetch
every target order's CURRENT line items from Shopify (one bulk read) and flag
any intended add whose SKU is already present on that order — i.e. importing
would create a duplicate.

READ-ONLY against Shopify. Writes a warnings .txt + _DUPES/_CLEAN split CSVs
to _outputs/artifacts/.

Usage:
  python check_import_dupes.py <csv_path> <order_tag>
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

OUT_DIR = Path(r"C:\Users\Work\Claude Projects\_outputs\artifacts")

SUBMIT = """
mutation($q: String!) {
  bulkOperationRunQuery(query: $q) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
"""
POLL = """
query { currentBulkOperation { id status url errorCode objectCount } }
"""


def _gql(base, headers, query, variables=None):
    r = requests.post(f"{base}/graphql.json", headers=headers,
                      json={"query": query, "variables": variables or {}}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def bulk_fetch_orders(tag: str) -> dict[str, dict]:
    """Return {order_digits: {"name": str, "skus": set(current skus), "titles": {sku: title}}}."""
    base, headers = get_shopify_auth()
    inner = (
        '{ orders(query: "tag:%s") { edges { node { id name '
        "lineItems { edges { node { sku quantity currentQuantity title } } } } } } }" % tag
    )
    d = _gql(base, headers, SUBMIT, {"q": inner})
    errs = d["bulkOperationRunQuery"]["userErrors"]
    if errs:
        raise RuntimeError(f"bulk submit userErrors: {errs}")
    # poll
    deadline = time.monotonic() + 1200
    sleep = 2.0
    url = None
    while True:
        op = _gql(base, headers, POLL)["currentBulkOperation"] or {}
        st = op.get("status")
        if st == "COMPLETED":
            url = op.get("url")
            break
        if st in ("FAILED", "CANCELED"):
            raise RuntimeError(f"bulk op {st}: {op.get('errorCode')}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"bulk op timeout (last status={st})")
        time.sleep(sleep)
        sleep = min(sleep * 1.5, 30)

    orders: dict[str, dict] = {}
    if not url:
        return orders
    resp = requests.get(url, stream=True, timeout=180)
    resp.raise_for_status()
    index: dict[str, str] = {}  # gid -> order_digits
    for line in resp.iter_lines():
        if not line:
            continue
        row = json.loads(line)
        pid = row.get("__parentId")
        if pid is None:
            name = row.get("name", "")
            digits = re.sub(r"\D", "", name)
            index[row["id"]] = digits
            orders[digits] = {"name": name, "skus": set(), "titles": {}}
        else:
            digits = index.get(pid)
            if digits is None:
                continue
            qty = row.get("currentQuantity")
            if qty is None:
                qty = row.get("quantity", 0)
            sku = (row.get("sku") or "").strip()
            if sku and qty and qty > 0:
                orders[digits]["skus"].add(sku)
                orders[digits]["titles"].setdefault(sku, row.get("title") or "")
    return orders


def main():
    csv_path = Path(sys.argv[1])
    tag = sys.argv[2]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    name_col = next(c for c in fieldnames if re.match(r"^(Name|Order)", c, re.I))

    # intended adds per order
    intended: dict[str, list[dict]] = {}
    for r in rows:
        digits = re.sub(r"\D", "", r[name_col])
        intended.setdefault(digits, []).append(r)

    print(f"CSV orders: {len(intended)}  rows: {len(rows)}")
    print(f"Fetching tag:{tag} from Shopify (bulk) ...")
    live = bulk_fetch_orders(tag)
    print(f"Live orders fetched: {len(live)}")

    dupe_issue: dict[str, list[str]] = {}   # order -> ["<sku> already exists (<title>)"]
    warnings: list[str] = []
    missing = []
    for digits, items in intended.items():
        if digits not in live:
            missing.append(digits)
            continue
        cur = live[digits]["skus"]
        for r in items:
            sku = (r["child_sku"] or "").strip()
            if sku and sku in cur:
                title = live[digits]["titles"].get(sku, "")
                dupe_issue.setdefault(digits, []).append(f'{sku} already exists')
                warnings.append(
                    f'WARN Order #{digits}: SKU "{sku}" ({title}) already exists '
                    f'as a line item -- adding again may create a duplicate'
                )

    # in-sheet duplicates: same order lists the same SKU on >1 add row.
    # (live-only comparison misses these — they fail import as "already on the order".)
    for digits, items in intended.items():
        counts: dict[str, int] = {}
        for r in items:
            s = (r["child_sku"] or "").strip()
            if s:
                counts[s] = counts.get(s, 0) + 1
        for s, c in counts.items():
            if c > 1:
                dupe_issue.setdefault(digits, []).append(f'{s} listed {c}x in sheet')
                warnings.append(
                    f'WARN Order #{digits}: SKU "{s}" appears {c}x in the import sheet '
                    f'-- in-sheet duplicate'
                )

    # split
    dup_rows, clean_rows = [], []
    for r in rows:
        digits = re.sub(r"\D", "", r[name_col])
        if digits in dupe_issue:
            rr = dict(r)
            rr["Dupe Issue"] = "; ".join(sorted(set(dupe_issue[digits])))
            dup_rows.append(rr)
        else:
            clean_rows.append(r)

    base = csv_path.stem
    dup_path = OUT_DIR / f"{base}_DUPES.csv"
    clean_path = OUT_DIR / f"{base}_CLEAN.csv"
    warn_path = OUT_DIR / f"{base}_WARNINGS.txt"

    if dup_rows:
        with dup_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fieldnames) + ["Dupe Issue"])
            w.writeheader(); w.writerows(dup_rows)
    with clean_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(clean_rows)
    with warn_path.open("w", encoding="utf-8") as f:
        f.write("\n".join("⚠️ " + w[5:] for w in warnings) + ("\n" if warnings else ""))

    print("-" * 50)
    print(f"DUPE orders: {len(dupe_issue)}  dupe line-adds: {len(warnings)}")
    print(f"CLEAN orders: {len(intended) - len(dupe_issue) - len(missing)}")
    print(f"MISSING from tag (not found live): {len(missing)} {missing[:20]}")
    print(f"DUPES csv : {dup_path if dup_rows else '(none)'}")
    print(f"CLEAN csv : {clean_path}")
    print(f"WARNINGS  : {warn_path}")
    print("-" * 50)
    for w in warnings[:60]:
        print(w)
    if len(warnings) > 60:
        print(f"... +{len(warnings) - 60} more (see WARNINGS file)")


if __name__ == "__main__":
    main()
