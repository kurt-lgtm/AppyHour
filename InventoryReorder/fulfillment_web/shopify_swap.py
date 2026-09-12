# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///

"""Shopify order SKU swap via GraphQL order edit API.

Swaps a shortage SKU for a substitute across unfulfilled orders
filtered by ship date tag. Used by the fulfillment web app's
swap integration on shortage rows.

TWO HARD RULES (Kurt 2026-06; enforced here so callers can't skip them):
1. AUDIT LOG — every swap appends a revert row (order, old->new, qty, ts, result) to
   _outputs/logs/swap_audit.jsonl via _audit(). Never run a swap path without it.
2. COUNT WITH fulfillableQuantity, NEVER quantity — a removed/zeroed line still reports
   its original `quantity`, so counting `quantity` double-counts items already swapped
   out. Any demand/inventory count over orders MUST use fulfillableQuantity.

Performance (2026-08-08):
- find_swap_targets passes the ship tag server-side (orders.json `tag` param) so only
  tagged orders paginate; the exact client-side tag check is KEPT as the authority.
- execute_bulk_swap fans out over ThreadPoolExecutor(max_workers=8) (mirrors
  AppyHourMCP/tools/order_edit.py); per-order rate-limit sleeps stay inside the worker.
- All HTTP goes through one module requests.Session (pool_maxsize=16, retry on 429/5xx).
- lookup_variant_gid memoized per (store_url, sku).
- find_skus_matching caches resolved patterns to _outputs/cache/sku_variant_catalog.json
  (24h TTL); bypass via no_cache=True or env SKU_CATALOG_NO_CACHE=1.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
import time
from typing import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# One pooled session for the whole module — connection reuse + retry on 429/5xx.
_SESSION = requests.Session()
_adapter = HTTPAdapter(
    pool_maxsize=16,
    max_retries=Retry(
        total=3, backoff_factor=1.0,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        # 🔴 FALSE, deliberately (2026-08-25). Shopify answers a 429 with a FRACTIONAL
        # Retry-After ("4.0"); urllib3 parses that header as an int and raises
        # InvalidHeader, which surfaces as a hard crash mid-run instead of a retry.
        # Killed a shorts_pass plan phase on _SHIP_2026-08-31. backoff_factor already
        # spaces the retries, so honouring the header buys nothing worth that risk.
        respect_retry_after_header=False,
    ),
)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)

# Append-only audit log so EVERY swap is revertible (order, from->to, qty, ts, result).
# Kurt 2026-06-19: never run a swap without a revert log. Built into the canonical
# module so /swap, execute_bulk_swap, and any caller log automatically.
_AUDIT_LOG = os.path.join(
    os.environ.get("SWAP_AUDIT_DIR", r"C:\Users\Work\Claude Projects\_outputs\logs"),
    "swap_audit.jsonl",
)


def _audit(row: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_AUDIT_LOG), exist_ok=True)
        row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), **row}
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass  # logging must never break a swap

# Failure classification — see E4 plan
_LOCKED_RE = re.compile(r"cannot be edited", re.I)
_TRANSIENT_RE = re.compile(
    r"\b50[234]\b|Bad Gateway|Gateway Timeout|timeout|ChunkedEncoding|Connection reset|Connection aborted",
    re.I,
)

# Dietary restriction box SKU fragments — kept for reference but no longer
# used to skip swaps. If an item is on a dietary order, it's safe to swap.
DIETARY_RESTRICTION_FRAGMENTS = ("NNRS", "CORS", "NCRS")

API_VERSION = "2026-04"


def _admin(store_url: str, token: str, path: str) -> tuple[str, dict]:
    """(url, headers) for one Admin API path.

    The callers pass their own store/token — this module is a library and does not resolve
    credentials for them — so the dedupe here is the URL shape, the version and the header
    dict, which lived in three copies. Sessions that own their credentials should get them
    from appyhour_lib.credentials.get_shopify_credentials and pass them in.
    """
    return (f"https://{store_url}.myshopify.com/admin/api/{API_VERSION}/{path}",
            {"X-Shopify-Access-Token": token, "Content-Type": "application/json"})


def _gql(store_url: str, token: str, query: str, variables: dict | None = None) -> dict:
    """Execute a Shopify Admin GraphQL query."""
    url, headers = _admin(store_url, token, "graphql.json")
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = _SESSION.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise Exception(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]

def _rest_get(store_url: str, token: str, path: str, params: dict | None = None) -> requests.Response:
    """Execute a Shopify Admin REST GET request."""
    url, headers = _admin(store_url, token, path)
    resp = _SESSION.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp

# Pattern-resolution cache (24h TTL) — the catalog walk is the slow part.
# Resolved from this file, not a machine-specific literal: fulfillment_web -> InventoryReorder ->
# AppyHour -> the workspace root that owns _outputs/. Same directory the literal pointed at.
_SKU_CATALOG_CACHE = str(
    Path(__file__).resolve().parents[3] / "_outputs" / "cache" / "sku_variant_catalog.json"
)
_SKU_CACHE_TTL_S = 24 * 3600


def _sku_cache_load() -> dict:
    try:
        with open(_SKU_CATALOG_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _sku_cache_save(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_SKU_CATALOG_CACHE), exist_ok=True)
        with open(_SKU_CATALOG_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass  # cache write must never break a lookup


def find_skus_matching(store_url: str, token: str, pattern: str, max_results: int = 1000,
                       no_cache: bool = False) -> list[str]:
    """Resolve wildcard SKU pattern to concrete SKU list.

    Patterns: '*-HHIGH' (suffix), 'TR-*' (prefix), '*BIX*' (substring), or exact (no `*`).
    Uses fnmatch for matching. Paginates productVariants and post-filters.
    Caps at max_results to prevent runaway scans.
    Results cached 24h in _outputs/cache/sku_variant_catalog.json; bypass with
    no_cache=True or env SKU_CATALOG_NO_CACHE=1.
    """
    import fnmatch
    if not pattern:
        return []
    if "*" not in pattern:
        return [pattern]

    use_cache = not (no_cache or os.environ.get("SKU_CATALOG_NO_CACHE") == "1")
    cache_key = f"{store_url}|{pattern}|{max_results}"
    if use_cache:
        entry = _sku_cache_load().get(cache_key)
        if entry and (time.time() - entry.get("ts", 0)) < _SKU_CACHE_TTL_S:
            return list(entry["skus"])

    # Extract longest non-* token for Shopify text search (cheap pre-filter)
    parts = [p for p in pattern.split("*") if p]
    if not parts:
        return []  # all-* pattern rejected
    search_term = max(parts, key=len)
    safe_term = search_term.replace('"', '\\"')

    matches: set[str] = set()
    cursor: str | None = None
    while len(matches) < max_results:
        after = f', after: "{cursor}"' if cursor else ""
        query = (
            '{ productVariants(first: 250, query: "sku:' + safe_term + '"' + after + ') { '
            'pageInfo { hasNextPage endCursor } '
            'edges { node { sku } } } }'
        )
        data = _gql(store_url, token, query)
        pv = data["productVariants"]
        for edge in pv["edges"]:
            sku = (edge["node"].get("sku") or "").strip()
            if sku and fnmatch.fnmatchcase(sku, pattern):
                matches.add(sku)
        if not pv["pageInfo"]["hasNextPage"]:
            break
        cursor = pv["pageInfo"]["endCursor"]
        time.sleep(0.1)

    result = sorted(matches)
    if use_cache:
        cache = _sku_cache_load()
        cache[cache_key] = {"ts": time.time(), "skus": result}
        _sku_cache_save(cache)
    return result


# Module-level variant GID memo — same SKUs get looked up every swap run
# (same pattern as AppyHourMCP/tools/order_edit.py::_variant_gid_cache).
_variant_gid_cache: dict[tuple[str, str], str] = {}


def lookup_variant_gid(store_url: str, token: str, sku: str) -> str | None:
    """Find the $0 variant GID for a SKU. Returns None if not found. Memoized."""
    memo_key = (store_url, sku)
    if memo_key in _variant_gid_cache:
        return _variant_gid_cache[memo_key]
    # Escape double quotes in SKU to prevent GraphQL injection
    safe_sku = sku.replace('"', '\\"')
    query = f'{{ productVariants(first: 5, query: "sku:{safe_sku}") {{ edges {{ node {{ id sku price }} }} }} }}'
    data = _gql(store_url, token, query)
    variants = []
    for edge in data["productVariants"]["edges"]:
        node = edge["node"]
        if node["sku"] == sku:
            variants.append(node)
    if not variants:
        return None
    # Prefer $0 variant (used for curation swaps)
    variants.sort(key=lambda v: float(v["price"]))
    gid = variants[0]["id"]
    _variant_gid_cache[memo_key] = gid
    return gid

UNTAGGED_SENTINEL = "__UNTAGGED__"
_SHIP_TAG_RE = re.compile(r"^_SHIP_\d{4}-\d{2}-\d{2}$")


def find_swap_targets(
    store_url: str,
    token: str,
    ship_tag: str,
    old_sku: str,
    progress_callback: Callable[[str], None] | None = None,
    bundle_only: bool = True,
    box_sku_contains: list[str] | None = None,
) -> list[dict]:
    """Find unfulfilled orders with ship_tag containing old_sku as a curation item.

    Only includes line items with fulfillableQuantity > 0.
    If bundle_only=True (default), restricts to line items with _rc_bundle property
    (skips paid/customer-chosen items). Set bundle_only=False to include paid items.
    If box_sku_contains is provided, restricts to orders where any line item SKU
    contains any of the given substrings (e.g. ['TR-', 'XMDT']).
    """
    targets = []
    url = "orders.json"
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "fields": "id,name,tags,line_items",
        # Server-side tag pre-filter (2026-08-08) — only tagged orders paginate.
        # The exact client-side `ship_tag in tags` check below stays the authority
        # (Shopify tag matching is loose on case/whitespace).
        "tag": ship_tag,
    }
    # UNTAGGED mode (Kurt 2026-09-09): orders not yet in ANY ship cohort. There is no
    # server-side "absent tag" filter, so the pre-filter is dropped and every open
    # unfulfilled order paginates; the client-side check below inverts to "carries no
    # _SHIP_ tag". Kurt's constraint was explicit — swap them WITHOUT tagging them.
    untagged_mode = ship_tag == UNTAGGED_SENTINEL
    if untagged_mode:
        params.pop("tag")
    page = 0

    while url:
        page += 1
        if progress_callback:
            progress_callback(f"Fetching orders page {page}...")

        if page == 1:
            resp = _rest_get(store_url, token, url, params)
        else:
            # Pagination URL is absolute — only the headers come from _admin here.
            _, headers = _admin(store_url, token, "")
            resp = _SESSION.get(url, headers=headers, timeout=30)
            resp.raise_for_status()

        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if untagged_mode:
                if any(_SHIP_TAG_RE.match(t) for t in tags):
                    continue
            elif ship_tag not in tags:
                continue
            order_line_items = o.get("line_items", [])
            if box_sku_contains:
                order_skus = [(li.get("sku") or "").strip() for li in order_line_items]
                if not any(any(frag in sku for frag in box_sku_contains) for sku in order_skus):
                    continue
            for li in order_line_items:
                sku = (li.get("sku") or "").strip()
                if sku != old_sku:
                    continue
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty <= 0:
                    continue
                props = li.get("properties", []) or []
                prop_names = {p.get("name", "") for p in props if isinstance(p, dict)}
                if bundle_only and "_rc_bundle" not in prop_names:
                    continue
                targets.append({
                    "order_id": o["id"],
                    "order_name": o["name"],
                    "order_gid": f"gid://shopify/Order/{o['id']}",
                    "qty": qty,
                })

        # Pagination via Link header
        link = resp.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
        time.sleep(0.1)

    return targets

def _line_paid_info(store_url: str, token: str, order_gid: str, sku: str) -> list[dict]:
    """Actual-paid + variant identity for every line of `sku` on the order.

    Returns [{"paid": float, "catalog_price": float, "variant_gid": str|None, "qty": int}, ...].
    `paid` = discountedUnitPriceAfterAllDiscountsSet (actual-paid ON THE SHOPIFY LINE).
    `catalog_price` = the line's variant catalog price — the rule-12b paid signal.

    🔴 BOTH matter (Kurt 2026-07-21, #163709): a Recharge ONETIME add-on collects the money
    ($9 MT-CCSP) on the Recharge charge and pushes the Shopify line at $0 — actual-paid
    alone reads $0 and the guard waves a paid item through. The line carries the PRICED
    variant, so catalog price > 0 is the detectable half of "customer paid for this".
    """
    data = _gql(store_url, token, """
    query($id: ID!) {
      order(id: $id) {
        lineItems(first: 100) {
          nodes {
            sku
            quantity
            currentQuantity
            discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } }
            variant { id price }
            customAttributes { key value }
          }
        }
      }
    }""", {"id": order_gid})
    out = []
    for li in data["order"]["lineItems"]["nodes"]:
        # 🔴 Removed/refunded lines keep their ORIGINAL `quantity`; only currentQuantity says
        # what is still on the order. Burn 2026-09-04 #178868: a $9 CH-BLR line refunded on
        # 9/03 (currentQuantity 0) still tripped the paid guard and blocked the live $0 BLR
        # swap. Never count a line the customer no longer has (shopify-line-items skill).
        # An ABSENT key is not a zero: a payload without currentQuantity (older query, a test
        # fixture, a caller's own selection set) must fall back to quantity, or this guard
        # fails OPEN and a paid line sails through the refusal below.
        qty_now = li.get("currentQuantity")
        if qty_now is None:
            qty_now = li.get("quantity") or 0
        if qty_now <= 0:
            continue
        if (li.get("sku") or "").strip() == sku:
            v = li.get("variant") or {}
            props = {p["key"] for p in (li.get("customAttributes") or [])}
            out.append({
                "paid": float(li["discountedUnitPriceAfterAllDiscountsSet"]["shopMoney"]["amount"]),
                "catalog_price": float(v.get("price") or 0),
                "variant_gid": v.get("id"),
                "qty": li.get("quantity", 0),
                "rc_bundle": "_rc_bundle" in props,
                "onetime": ("_parent_subscription_id" in props) or ("Type" in props),
            })
    return out


def execute_swap(
    store_url: str,
    token: str,
    order_gid: str,
    old_sku: str,
    new_variant_gid: str,
    staff_note: str = "",
    allow_paid: bool = False,
) -> dict:
    """Swap old_sku for new variant on a single order via GraphQL order edit.

    LOW-LEVEL PRIMITIVE — NOT AN ENTRY POINT (Kurt 2026-08-08). Batch/shorts work
    goes through find_swap_targets() + execute_bulk_swap() in THIS module (or the
    /swap skill), which add per-order accounting (locked/transient/failed classes),
    the fulfillableQuantity>0 filter, and REST pagination. Hand-rolling a loop
    around this function produced 34 phantom "successes" on wk0810 (it returns
    success:False, it does not raise). See TOOL_REGISTRY.md + vault Swap Rules #0.

    PAID-ITEM GUARD (Kurt 2026-07-10): a line the customer actually paid for
    (discounted price > $0) is NEVER swapped unless allow_paid=True is passed
    explicitly. Motivator: three paid lines slipped into a wk0713 rotation
    batch; the revert used the $0 in-box variant instead of the exact variant
    purchased, and Kurt had to repair the accounting manually. Paid items are
    left alone; if a revert is ever required it MUST restore the exact
    old_variant_gid recorded in the audit row — never lookup_variant_gid().

    Returns {success: bool, order_name: str, error: str | None}.
    """
    paid_lines = _line_paid_info(store_url, token, order_gid, old_sku)
    old_variant_gids = sorted({p["variant_gid"] for p in paid_lines if p["variant_gid"]})
    # PAID = actual-paid on the line, OR a priced (catalog > $0) variant on a line that is
    # actually an add-on. The catalog check catches Recharge-collected money invisible on the
    # Shopify line (onetime add-ons push at $0 actual-paid — #163709 MT-CCSP, 2026-07-21).
    #
    # 🔴 catalog>0 ALONE is NOT a paid signal (Kurt 2026-08-21). A `_rc_bundle` line is box
    # content: it carries the priced variant because the SKU also sells standalone, but the
    # customer paid $0 for it. Reading catalog price as paid refused ALL 107 AC-GLAW→AC-PRPE
    # swaps (paid=0.0 catalog=6.0) on uncustomized subscription first orders — a fail-closed
    # guard that blocks the exact work it was never meant to touch. The add-on half of the
    # #163709 case is still caught: a real Recharge onetime carries `_parent_subscription_id`
    # or `Type`, never `_rc_bundle`.
    paid_hits = [
        p for p in paid_lines
        if p["paid"] > 0
        or (p["catalog_price"] > 0 and p.get("onetime") and not p.get("rc_bundle"))
    ]
    if paid_hits and not allow_paid:
        detail = [f"paid={p['paid']} catalog={p['catalog_price']}" for p in paid_hits]
        _audit({"order_gid": order_gid, "old_sku": old_sku, "new_variant_gid": new_variant_gid,
                "old_variant_gids": old_variant_gids,
                "result": f"REFUSED:paid-item-guard {detail}"})
        return {"success": False,
                "error": f"paid-item guard: {old_sku} paid/priced line(s) {detail} — "
                         f"customer keeps what they paid for (Recharge may hold the money even when the "
                         f"Shopify line reads $0; pass allow_paid=True only with Kurt's explicit OK)"}
    # Step 1: Begin edit
    data = _gql(store_url, token, """
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder {
          id
          lineItems(first: 50) {
            edges { node { id sku quantity } }
          }
        }
        userErrors { field message }
      }
    }""", {"id": order_gid})

    if data["orderEditBegin"]["userErrors"]:
        errors = data["orderEditBegin"]["userErrors"]
        return {"success": False, "error": f"beginEdit: {errors}"}

    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]

    # Find ALL old-SKU line items — an order can carry the same SKU on multiple
    # lines (e.g. two TR-TAPAS lines on #157930). Swapping only the first line
    # leaves un-swapped quantity behind.
    li_nodes = [
        edge["node"] for edge in calc["lineItems"]["edges"]
        if (edge["node"].get("sku") or "").strip() == old_sku and edge["node"]["quantity"] > 0
    ]

    if not li_nodes:
        _audit({"order_gid": order_gid, "old_sku": old_sku, "new_variant_gid": new_variant_gid,
                "result": "skip:not-found"})
        return {"success": False, "error": f"Line item {old_sku} not found or qty=0"}

    total_qty = sum(n["quantity"] for n in li_nodes)

    # Revert info: to undo, restore old_variant_gids EXACTLY (never lookup_variant_gid —
    # the $0 in-box variant may differ from the variant on the removed line).
    _audit({"order_gid": order_gid, "old_sku": old_sku, "new_variant_gid": new_variant_gid,
            "old_variant_gids": old_variant_gids,
            "qty": total_qty, "lines": len(li_nodes), "result": "intent"})

    # Step 2: Set each old line item qty to 0
    for li_node in li_nodes:
        time.sleep(0.3)
        data = _gql(store_url, token, """
        mutation orderEditSetQuantity($id: ID!, $lineItemId: ID!, $quantity: Int!) {
          orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
            calculatedOrder { id }
            userErrors { field message }
          }
        }""", {"id": calc_id, "lineItemId": li_node["id"], "quantity": 0})

        if data["orderEditSetQuantity"]["userErrors"]:
            errors = data["orderEditSetQuantity"]["userErrors"]
            return {"success": False, "error": f"setQuantity: {errors}"}

    # Step 3: Add new variant at the SAME total qty removed (balance invariant —
    # Kurt 2026-07-09, #157930 shipped a tray short after an unbalanced edit)
    time.sleep(0.3)
    data = _gql(store_url, token, """
    mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
      orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
        calculatedLineItem { id }
        calculatedOrder { id }
        userErrors { field message }
      }
    }""", {"id": calc_id, "variantId": new_variant_gid, "quantity": total_qty})

    if data["orderEditAddVariant"]["userErrors"]:
        errors = data["orderEditAddVariant"]["userErrors"]
        return {"success": False, "error": f"addVariant: {errors}"}

    # Step 4: Commit
    time.sleep(0.3)
    data = _gql(store_url, token, """
    mutation orderEditCommit($id: ID!, $staffNote: String) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: $staffNote) {
        order { id name }
        userErrors { field message }
      }
    }""", {"id": calc_id, "staffNote": staff_note})

    if data["orderEditCommit"]["userErrors"]:
        errors = data["orderEditCommit"]["userErrors"]
        _audit({"order_gid": order_gid, "old_sku": old_sku, "new_variant_gid": new_variant_gid,
                "qty": total_qty, "result": f"FAIL:commit:{errors}"})
        return {"success": False, "error": f"commit: {errors}"}

    order = data["orderEditCommit"].get("order") or {}
    _audit({"order_gid": order_gid, "order_name": order.get("name"), "old_sku": old_sku,
            "new_variant_gid": new_variant_gid, "old_variant_gids": old_variant_gids,
            "qty": total_qty, "result": "OK"})
    return {"success": True, "error": None}

def execute_bulk_swap(
    store_url: str,
    token: str,
    targets: list[dict],
    old_sku: str,
    new_variant_gid: str,
    staff_note: str = "",
    dry_run: bool = True,
    progress_callback: Callable[[str], None] | None = None,
    cancel_flag: list | None = None,
    guard_report: dict | None = None,
) -> dict:
    """Execute swap on multiple orders.

    Args:
        targets: List from find_swap_targets().
        old_sku: SKU being replaced.
        new_variant_gid: GID of new variant to add.
        staff_note: Note added to each order edit.
        dry_run: If True, just return count without executing.
        progress_callback: Called with status string for each order.
        cancel_flag: Single-element list; if cancel_flag[0] is True, abort.

    Returns:
        {total, success, failed, errors, dry_run, locked, transient, other,
         successful_orders}
    """
    total = len(targets)

    # 🔴 GUARD GATE (Kurt 2026-08-25: "fix it so it never happens again unless I say so").
    # A live multi-order batch must carry the report from swap_provenance.guard_batch(),
    # which proves the login+customize scans ran and the per-order cap was applied. Missing
    # evidence is not permission. wk0817: 201 tray swaps ran with the login column dropped
    # and 42 customized rows included; one customer got 5 swaps in 28 seconds.
    if not dry_run and total > 1:
        ok = isinstance(guard_report, dict) and (
            (guard_report.get("login_scan_ran") and guard_report.get("customize_scan_ran"))
            or guard_report.get("kurt_override"))
        if not ok:
            raise RuntimeError(
                "execute_bulk_swap refused: pass guard_report from "
                "swap_provenance.guard_batch(...) proving the login-OR-customize scans ran "
                "(or carrying kurt_override). See AppyHour/swap_provenance.py.")

    if dry_run:
        return {
            "total": total,
            "success": 0,
            "failed": 0,
            "errors": [],
            "locked": [],
            "transient": [],
            "other": [],
            "successful_orders": [],
            "dry_run": True,
            "targets": [
                {"order_name": t["order_name"], "qty": t["qty"]}
                for t in targets
            ],
        }

    success = 0
    failed = 0
    errors: list[str] = []
    locked: list[dict] = []
    transient: list[dict] = []
    other: list[dict] = []
    successful_orders: list[str] = []

    # Parallel fan-out (2026-08-08) — mirrors AppyHourMCP/tools/order_edit.py.
    # GraphQL rate-limit sleeps live INSIDE execute_swap (per-order pacing);
    # accounting shape below is unchanged. Results aggregated in target order.
    from concurrent.futures import ThreadPoolExecutor

    done_count = [0]

    def _do_swap(t: dict):
        if cancel_flag and cancel_flag[0]:
            return "cancelled"
        result = execute_swap(
            store_url, token, t["order_gid"], old_sku, new_variant_gid, staff_note
        )
        done_count[0] += 1
        if progress_callback:
            progress_callback(f"Swapping {done_count[0]}/{total}: {t['order_name']}...")
        time.sleep(0.1)
        return result

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_do_swap, targets))

    cancelled = False
    for t, result in zip(targets, results):
        if result == "cancelled":
            cancelled = True
            continue
        if result["success"]:
            success += 1
            successful_orders.append(t["order_name"])
        else:
            failed += 1
            err_text = str(result["error"] or "")
            errors.append(f"{t['order_name']}: {err_text}")
            item = {"order_name": t["order_name"], "error": err_text}
            if _LOCKED_RE.search(err_text):
                locked.append(item)
            elif _TRANSIENT_RE.search(err_text):
                transient.append(item)
            else:
                other.append(item)
    if cancelled:
        errors.append("Cancelled by user")

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "errors": errors,
        "locked": locked,
        "transient": transient,
        "other": other,
        "successful_orders": successful_orders,
        "dry_run": False,
    }


# ---------------------------------------------------------------------------
# Conditional-target swap (per-parent-box remediation).
# Promoted from a documented /swap flow 2026-06-15 after a /forge reason debate:
# verdict = "promote behind a verification gate, decided per-pattern."
#   - Pattern "protected-swap" (re-add bystander) was NOT built: verified by code
#     inspection that execute_swap only zeroes the old SKU line, so no bystander
#     can drop in the canonical flow — it guarded a non-existent bug.
#   - This (conditional-target) WAS built: its premise is confirmed (per-parent
#     remediation really happened, e.g. swap_bad_ipac_blr.py). The pure resolver is
#     unit-tested; the LIVE MUTATION is gated behind dry_run + a required live-order test.
# ---------------------------------------------------------------------------

def resolve_conditional_adds(parent_sku: str, target_map: dict) -> list[tuple[str, int]]:
    """Pure (no I/O, unit-testable): given an order's parent box SKU and a
    {parent_sku: {"cheese": sku, "jams": [(sku, qty), ...]}} map, return the
    list of (sku, qty) to ADD for that order. Raises KeyError if the parent
    isn't in the map (fail loud — never silently add nothing)."""
    t = target_map.get(parent_sku)
    if t is None:
        raise KeyError(f"no conditional target for parent box SKU {parent_sku!r}")
    adds: list[tuple[str, int]] = []
    if t.get("cheese"):
        adds.append((t["cheese"], 1))
    adds.extend((sku, qty) for sku, qty in t.get("jams", []))
    return adds


def execute_conditional_swap(
    store_url: str,
    token: str,
    order_gid: str,
    removes: list[str],
    adds: list[tuple[str, int]],
    staff_note: str = "",
    dry_run: bool = True,
    allow_paid: bool = False,
) -> dict:
    """Multi-remove + multi-add on ONE order via Shopify order edit.

    Unlike execute_swap (fixed single old->new), the *correct* additions vary per
    order — the caller resolves them (resolve_conditional_adds + lookup_variant_gid)
    and passes the resolved removes/adds here.

    Args:
        removes: SKUs to zero out on the order (each matched line set to qty 0).
        adds:    list of (variant_gid, qty) to add.
        dry_run: True (default, SAFE) returns the resolved plan WITHOUT mutating.

    ⚠️ LIVE IRREVERSIBLE MUTATION when dry_run=False. AppyHour is live-data-only
    (no staging). Per the 2026-06-15 /forge reason verdict, do NOT call with
    dry_run=False in production until a supervised live-order test session has
    passed. Returns {success, order_name?, removed, added, dry_run, error}.
    """
    if dry_run:
        return {"success": True, "dry_run": True, "removes": list(removes),
                "adds": list(adds), "order_gid": order_gid, "error": None}

    # PAID-ITEM GUARD (parity with execute_swap; Kurt 2026-07-21 #163709): refuse any
    # remove whose line has actual-paid > 0 OR a priced (catalog > $0) variant — Recharge
    # onetime add-ons collect the money off-Shopify and push the line at $0.
    if not allow_paid:
        for rsku in removes:
            hits = [p for p in _line_paid_info(store_url, token, order_gid, rsku)
                    if p["paid"] > 0 or p["catalog_price"] > 0]
            if hits:
                detail = [f"paid={p['paid']} catalog={p['catalog_price']}" for p in hits]
                _audit({"order_gid": order_gid, "old_sku": rsku,
                        "result": f"REFUSED:paid-item-guard(conditional) {detail}"})
                return {"success": False, "dry_run": False,
                        "error": f"paid-item guard: {rsku} paid/priced line(s) {detail} — "
                                 f"pass allow_paid=True only with Kurt's explicit OK"}

    # Step 1: begin edit (snapshot line items)
    data = _gql(store_url, token, """
    mutation orderEditBegin($id: ID!) {
      orderEditBegin(id: $id) {
        calculatedOrder { id lineItems(first: 50) { edges { node { id sku quantity } } } }
        userErrors { field message }
      }
    }""", {"id": order_gid})
    if data["orderEditBegin"]["userErrors"]:
        return {"success": False, "dry_run": False, "error": f"beginEdit: {data['orderEditBegin']['userErrors']}"}
    calc = data["orderEditBegin"]["calculatedOrder"]
    calc_id = calc["id"]
    lines = {(e["node"].get("sku") or "").strip(): e["node"] for e in calc["lineItems"]["edges"]}

    # BALANCE INVARIANT (Kurt 2026-07-09 — order #157930: this path zeroed a
    # qty-2 TR-ICTRY line but the caller only passed 1 TR-TRUFF to add; the box
    # shipped a tray short). The FULL line quantity is what gets removed here,
    # regardless of what the caller assumed — so total add qty must equal total
    # qty that will actually be zeroed. Checked BEFORE any mutation.
    # Note: `lines` is keyed by SKU, so a duplicate-SKU order only zeroes the
    # last line per SKU — the balance below counts exactly what will be zeroed.
    qty_to_remove = sum(
        lines[sku]["quantity"] for sku in set(removes)
        if lines.get(sku) and lines[sku]["quantity"] > 0
    )
    qty_to_add = sum(qty for _, qty in adds)
    if qty_to_remove != qty_to_add:
        _audit({"order_gid": order_gid, "removes": list(removes), "adds": list(adds),
                "result": f"ABORT:unbalanced remove={qty_to_remove} add={qty_to_add}"})
        return {"success": False, "dry_run": False,
                "error": f"ABORT: unbalanced conditional swap — would remove qty {qty_to_remove} "
                         f"but add qty {qty_to_add}; edit NOT started"}

    _audit({"order_gid": order_gid, "removes": list(removes), "adds": list(adds),
            "qty": qty_to_remove, "result": "intent:conditional"})

    # Step 2: zero out each remove SKU that's present
    removed = []
    for sku in removes:
        node = lines.get(sku)
        if not node or node["quantity"] <= 0:
            continue
        time.sleep(0.3)
        d = _gql(store_url, token, """
        mutation setQ($id: ID!, $lineItemId: ID!, $quantity: Int!) {
          orderEditSetQuantity(id: $id, lineItemId: $lineItemId, quantity: $quantity) {
            calculatedOrder { id } userErrors { field message } }
        }""", {"id": calc_id, "lineItemId": node["id"], "quantity": 0})
        if d["orderEditSetQuantity"]["userErrors"]:
            return {"success": False, "dry_run": False, "error": f"setQuantity {sku}: {d['orderEditSetQuantity']['userErrors']}"}
        removed.append(sku)

    # Step 3: add each (variant_gid, qty)
    added = []
    for variant_gid, qty in adds:
        time.sleep(0.3)
        d = _gql(store_url, token, """
        mutation addV($id: ID!, $variantId: ID!, $quantity: Int!) {
          orderEditAddVariant(id: $id, variantId: $variantId, quantity: $quantity, allowDuplicates: true) {
            calculatedLineItem { id } userErrors { field message } }
        }""", {"id": calc_id, "variantId": variant_gid, "quantity": qty})
        if d["orderEditAddVariant"]["userErrors"]:
            return {"success": False, "dry_run": False, "error": f"addVariant {variant_gid}: {d['orderEditAddVariant']['userErrors']}"}
        added.append((variant_gid, qty))

    # Step 4: commit
    time.sleep(0.3)
    d = _gql(store_url, token, """
    mutation commit($id: ID!, $staffNote: String) {
      orderEditCommit(id: $id, notifyCustomer: false, staffNote: $staffNote) {
        order { id name } userErrors { field message } }
    }""", {"id": calc_id, "staffNote": staff_note})
    if d["orderEditCommit"]["userErrors"]:
        _audit({"order_gid": order_gid, "removes": removed, "adds": added,
                "result": f"FAIL:commit:{d['orderEditCommit']['userErrors']}"})
        return {"success": False, "dry_run": False, "error": f"commit: {d['orderEditCommit']['userErrors']}"}
    _audit({"order_gid": order_gid, "order_name": d["orderEditCommit"]["order"]["name"],
            "removes": removed, "adds": added, "qty": qty_to_remove, "result": "OK:conditional"})
    return {"success": True, "dry_run": False, "order_name": d["orderEditCommit"]["order"]["name"],
            "removed": removed, "added": added, "error": None}
