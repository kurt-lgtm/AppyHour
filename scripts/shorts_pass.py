"""shorts_pass.py — weekly shorts→swap orchestrator (canonical).

Usage:
    python scripts/shorts_pass.py SHIP_TAG --pairs pairs.csv [--apply] [--limit N]
                                  [--login-window-days 30]

pairs.csv columns: old_sku,new_sku,count

🔴 Constraints SSOT: scripts/SHORTS_PASS_RULES.md — read BEFORE changing this file.
Motivating burn: wk0810 hand-rolled loops counted calls as successes → 34 phantom
swaps (execute_swap returns success:False, it does not raise). This tool:
  plan    — find_swap_targets (fulfillable>0, _rc_bundle only) + login-OR-customize
            exclusion (Recharge /events?verb=login + bundle_selections), cap at count
  preview — per-pair table: eligible / planned / excluded + why
  apply   — order_edit._swap_order_skus module path ONLY (balance invariant,
            swap_audit.jsonl, paid-item guard, variant-GID cache)
  verify  — re-fetch tag, re-count fulfillable_quantity old/new, diff vs plan;
            NONZERO EXIT on mismatch. Success is never call-count.
Dry-run is the default; --apply required for writes. Every run appends a JSONL
log under _outputs/logs/ (never overwrites).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent          # AppyHour/scripts
_APPYHOUR = _HERE.parent                          # AppyHour/
for p in (_APPYHOUR, _APPYHOUR / "AppyHourMCP", _APPYHOUR / "InventoryReorder" / "fulfillment_web"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from appyhour_lib.credentials import get_shopify_auth, get_shopify_credentials  # noqa: E402
from cut_order_server.app.creds import get_recharge_token  # noqa: E402
import shopify_swap  # noqa: E402  (fulfillment_web — find_swap_targets et al.)

# Canonical execution module — NEVER reimplement GraphQL edits (SHORTS_PASS_RULES #1).
sys.path.insert(0, str(_APPYHOUR / "AppyHourMCP" / "tools"))
import order_edit  # noqa: E402

# 🔴 Windows cp1252 kills any non-ASCII print (arrows, emoji) MID-RUN — for a --apply tool that
# means a crash BETWEEN mutations, leaving the batch half-applied. Wrap stdout before anything
# prints, including argparse --help. (Live 2026-08-09: shorts_pass.py --help died on U+2192.)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


LOG_DIR = Path(r"C:\Users\Work\Claude Projects\_outputs\logs")

RC_HEADERS = None  # built lazily


def _rc_headers() -> dict:
    global RC_HEADERS
    if RC_HEADERS is None:
        # recharge-api skill conventions: v2021-11 header, timeout=30, cursor pagination.
        RC_HEADERS = {
            "X-Recharge-Access-Token": get_recharge_token(),
            "X-Recharge-Version": "2021-11",
            "Content-Type": "application/json",
        }
    return RC_HEADERS


def rc_get(session: requests.Session, path: str, params: dict | None = None, retries: int = 5) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = session.get(f"https://api.rechargeapps.com{path}", headers=_rc_headers(),
                            params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("retry-after", "5")))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — retried, re-raised after retries
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Recharge GET {path} failed after {retries} tries: {last}")


# ---------------------------------------------------------------- Shopify fetch

def fetch_tag_orders(session: requests.Session, store: str, token: str, ship_tag: str) -> list[dict]:
    """All open unfulfilled orders carrying ship_tag (exact tag match), with customer email.

    Single pass reused for exclusion mapping and verification counts.
    """
    out: list[dict] = []
    url = f"https://{store}.myshopify.com/admin/api/2026-04/orders.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    params: dict | None = {
        "status": "open", "fulfillment_status": "unfulfilled", "limit": 250,
        "fields": "id,name,tags,line_items,email,customer",
    }
    while url:
        # 🔴 429 retry (2026-08-25). This paginates ~2,500 orders at 250/page; a single
        # unretried 429 aborted the whole plan phase mid-cohort. Shopify may answer with a
        # FRACTIONAL Retry-After ("4.0") — parse as float, never int.
        for attempt in range(6):
            resp = session.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code != 429:
                break
            wait = float(resp.headers.get("Retry-After") or 0) or 2.0 * (attempt + 1)
            print(f"  [429] backing off {wait:.1f}s (attempt {attempt + 1}/6)", flush=True)
            time.sleep(wait)
        resp.raise_for_status()
        for o in resp.json().get("orders", []):
            tags = [t.strip() for t in (o.get("tags") or "").split(",")]
            if ship_tag in tags:
                out.append(o)
        link = resp.headers.get("Link", "")
        url, params = None, None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split("<")[1].split(">")[0]
        time.sleep(0.05)
    return out


def count_fulfillable(orders: list[dict], sku: str) -> int:
    """Fulfillable-only count (SHORTS_PASS_RULES #3 — never `quantity`)."""
    total = 0
    for o in orders:
        for li in o.get("line_items", []):
            if (li.get("sku") or "").strip() == sku:
                total += max(0, li.get("fulfillable_quantity", 0) or 0)
    return total


def cohort_skus(orders: list[dict]) -> set[str]:
    """Union of fulfillable SKUs on the tag — same fulfillable-only rule as count_fulfillable (#3)."""
    present: set[str] = set()
    for o in orders:
        for li in o.get("line_items", []):
            if (li.get("fulfillable_quantity", 0) or 0) > 0:
                s = (li.get("sku") or "").strip()
                if s:
                    present.add(s)
    return present


def near_miss_candidates(sku: str, present: set[str]) -> list[str]:
    """Prefix-overlap candidates for a SKU that matched nothing (PR-CJAM -> PR-CJAM-GEN)."""
    return sorted(p for p in present if p.startswith(sku) or sku.startswith(p))[:5]


def absent_pair_refusals(pairs: list[dict], present: set[str]) -> list[str]:
    """🔴 Join-zero guard (2026-08-08 live smoke test: `PR-CJAM` matched 0 of 2321 orders because
    the real SKU is PR-CJAM-GEN). An old_sku on ZERO cohort orders is a TYPO in pairs.csv, not a
    pair with nothing to do — and it renders as an innocuous `elig 0 / plan 0` row in the preview
    table, exactly the silent zero that hides a bad week's shorts file. Returns one loud message
    per bad pair; empty list = every old_sku is really on the cohort."""
    out = []
    for p in pairs:
        if p["old_sku"] in present:
            continue
        cands = near_miss_candidates(p["old_sku"], present)
        out.append(f"  {p['old_sku']} -> {p['new_sku']}: old_sku on ZERO cohort orders; "
                   f"did you mean {cands or '(no similar SKU in cohort)'}")
    return out


# ------------------------------------------------- login-OR-customize exclusion

def fetch_login_customer_ids(session: requests.Session, since_iso: str) -> set[int]:
    """Recharge customer_ids with a portal login since `since_iso` (/events?verb=login,
    newest-first, stop at cutoff — same pattern as scripts/traaab_logins_join.py)."""
    ids: set[int] = set()
    params: dict | None = {"verb": "login", "sort_by": "created_at-desc", "limit": 250}
    while True:
        data = rc_get(session, "/events", params)
        events = data.get("events", [])
        if not events:
            break
        past_cutoff = False
        for e in events:
            if (e.get("created_at") or "") < since_iso:
                past_cutoff = True
                break
            cid = e.get("customer_id")
            if cid:
                ids.add(int(cid))
        cursor = data.get("next_cursor")
        if past_cutoff or not cursor:
            break
        params = {"cursor": cursor, "limit": 250}
        time.sleep(0.3)
    return ids


def rc_customer_id(session: requests.Session, email: str) -> int | None:
    if not email:
        return None
    data = rc_get(session, "/customers", {"email": email, "limit": 1})
    custs = data.get("customers", [])
    return int(custs[0]["id"]) if custs else None


# A bundle_selection written by Recharge itself has updated_at == created_at (0s apart).
# Anything above this many seconds is a human edit in the portal.
_CUSTOMIZE_MIN_DELTA_S = 5.0


def _bundle_edit_delta_s(bs_row: dict) -> float:
    """Seconds between created_at and updated_at on a bundle_selection. 0 = system write."""
    c_, u_ = bs_row.get("created_at"), bs_row.get("updated_at")
    if not c_ or not u_:
        return 0.0
    try:
        cd = dt.datetime.fromisoformat(c_.replace("Z", "+00:00"))
        ud = dt.datetime.fromisoformat(u_.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (ud - cd).total_seconds()


def rc_customized(session: requests.Session, customer_id: int) -> bool:
    """True if the customer has MODIFIED a bundle selection in the portal.

    🔴 PRESENCE IS NOT CUSTOMIZATION (live finding 2026-08-09). Recharge writes a
    bundle_selection for EVERY bundle subscription by default, so the old
    `if bs.get("bundle_selections"): return True` flagged essentially every subscriber.
    Measured on `_SHIP_2026-08-10`: 44 of 67 CH-CONI candidates excluded as "customized";
    a per-order re-check of 10 of them found delta = 0s on all 10 — every one a false
    positive. Effect was silent, not loud: the pass planned 1 swap where ~45 were
    available, and the rule-2 verify still passed (plan 1 == actual 1), so a week's
    shorts would quietly go unresolved. The real signal is
    `updated_at > created_at` (memory: recharge-bundle-selection-scoping).

    PARKED refinement: scope to THIS order (order -> /charges?external_order_id= -> box
    line purchase_item_id -> /bundle_selections?purchase_item_ids=) instead of the
    customer's active subscriptions. Customer scope over-protects (an edit on any
    subscription flags them) which is the SAFE direction, but it is not the question we
    mean. Needs the caller to pass order id, not just email — Kurt's go before changing.
    """
    subs = rc_get(session, "/subscriptions",
                  {"customer_id": customer_id, "status": "active", "limit": 250})
    for sub in subs.get("subscriptions", []):
        pid = sub.get("id")
        if not pid:
            continue
        bs = rc_get(session, "/bundle_selections", {"purchase_item_ids": pid})
        for row in bs.get("bundle_selections", []):
            if _bundle_edit_delta_s(row) > _CUSTOMIZE_MIN_DELTA_S:
                return True
        time.sleep(0.1)
    return False


def exclusion_reason(session: requests.Session, email: str,
                     login_ids: set[int], cache: dict) -> str | None:
    """None = swappable. Else 'logged_in' / 'customized'. Conservative on lookup failure."""
    key = (email or "").lower()
    if key in cache:
        return cache[key]
    reason: str | None
    try:
        cid = rc_customer_id(session, email)
        if cid is None:
            reason = None  # no Recharge customer → no portal to log into / customize
        elif cid in login_ids:
            reason = "logged_in"
        elif rc_customized(session, cid):
            reason = "customized"
        else:
            reason = None
    except Exception as e:  # noqa: BLE001 — unknown = NOT cleared; exclude, never guess
        reason = f"rc_lookup_failed:{e}"
    cache[key] = reason
    return reason


# --------------------------------------------------------------------- pipeline

def load_pairs(path: Path) -> list[dict]:
    pairs = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pairs.append({
                "old_sku": row["old_sku"].strip(),
                "new_sku": row["new_sku"].strip(),
                "count": int(row["count"]),
                # identical-product pair -> login/customize protection does not apply
                "silent": (row.get("silent") or "").strip(),
            })
    if not pairs:
        raise SystemExit("pairs.csv is empty")
    return pairs


def is_silent(pair: dict) -> bool:
    """Identical-product pair: protection, _rc_bundle fence and paid guard all lift."""
    return str(pair.get("silent", "")).strip().lower() in ("1", "y", "yes", "true")


# 🔴 Guard constants (Kurt 2026-08-25). SSOT: AppyHour/swap_provenance.py.
PER_ORDER_SKU_CAP = 2
NEVER_SWAP_TAGS = {"pr box"}


def plan_pair(store: str, token: str, ship_tag: str, pair: dict,
              email_by_order: dict, rc_session: requests.Session,
              login_ids: set[int], excl_cache: dict, limit: int | None) -> dict:
    # Eligibility: canonical finder — fulfillable>0 + _rc_bundle only (paid lines out).
    # silent pair -> also reach lines with no _rc_bundle prop (2 AC-RBOL orders were
    # invisible to a bundle_only finder and failed at commit).
    targets = shopify_swap.find_swap_targets(store, token, ship_tag, pair["old_sku"],
                                             bundle_only=not is_silent(pair))
    cap = min(pair["count"], limit) if limit else pair["count"]
    planned, excluded = [], []
    # Per-ORDER SKU cap (Kurt 2026-08-25): a shortage sweep runs SKU-by-SKU, so nobody
    # watches the per-order total. Order 7305663676696 took FIVE tray swaps in 28 seconds
    # on 2026-08-14. `swaps_by_order` persists across pairs within one run.
    swaps_by_order = plan_pair.swaps_by_order
    for t in targets:
        if len(planned) >= cap:
            break
        oid = str(t.get("order_id") or t.get("order_name") or "")
        # 🔴 "PR box" is never swappable — a short there is escalated to Kurt, not filled.
        tags = t.get("tags")
        if tags is not None and {str(x).strip().lower() for x in tags} & NEVER_SWAP_TAGS:
            excluded.append({**t, "why": "PR box (never swap - report as real short)"})
            continue
        if len(swaps_by_order.get(oid, set())) >= PER_ORDER_SKU_CAP and \
                pair["old_sku"] not in swaps_by_order.get(oid, set()):
            excluded.append({**t, "why": f"per-order cap {PER_ORDER_SKU_CAP} already reached"})
            continue
        # silent pair = identical product; protection does not apply (see module docstring
        # on `silent`). Tag block + per-order cap above still ran.
        if is_silent(pair):
            planned.append(t)
            swaps_by_order.setdefault(oid, set()).add(pair["old_sku"])
            continue
        email = email_by_order.get(t["order_id"], "")
        reason = exclusion_reason(rc_session, email, login_ids, excl_cache)
        if reason:
            excluded.append({**t, "why": reason})
        else:
            planned.append(t)
            swaps_by_order.setdefault(oid, set()).add(pair["old_sku"])
    return {**pair, "eligible": len(targets), "planned": planned, "excluded": excluded}


plan_pair.swaps_by_order = {}


def execute_plans(plans: list[dict]) -> list[dict]:
    """Execute via the canonical order_edit module path. Returns per-order results.

    NEVER counts calls as success — results feed the verify phase, which is the
    only success authority (SHORTS_PASS_RULES #2)."""
    base, headers = get_shopify_auth()
    all_skus = {p["old_sku"] for p in plans} | {p["new_sku"] for p in plans}
    # One batched, cached lookup up-front ($0-preferring). Per-order re-lookups forbidden
    # (GraphQL sku: index-lag produced phantom failures on wk0810).
    gids = order_edit._lookup_variant_gids(base, headers, all_skus)
    jobs = []
    for p in plans:
        for t in p["planned"]:
            jobs.append((p["old_sku"], p["new_sku"], t, is_silent(p)))
    results = []

    def _one(old_sku: str, new_sku: str, t: dict, silent: bool = False) -> dict:
        try:
            # Non-silent: _rc_bundle fence + paid guard both armed (allow_paid never passed).
            # Silent: same product under two SKUs, so both lift (Kurt 2026-08-25).
            swapped = order_edit._swap_order_skus(
                base, headers, t["order_gid"], {old_sku: new_sku}, gids,
                rc_bundle_only=not silent, allow_paid=silent)
            return {"order": t["order_name"], "old": old_sku, "new": new_sku,
                    "swapped": swapped, "ok": bool(swapped)}
        except Exception as e:  # noqa: BLE001 — recorded per-order, run continues
            return {"order": t["order_name"], "old": old_sku, "new": new_sku,
                    "error": str(e), "ok": False}

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_one, *j) for j in jobs]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            print(f"  {'OK ' if r['ok'] else 'FAIL'} {r['order']} {r['old']} -> {r['new']}"
                  + (f"  [{r.get('error', '')}]" if not r["ok"] else ""))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ship_tag")
    ap.add_argument("--pairs", required=True, type=Path)
    ap.add_argument("--apply", action="store_true", help="execute writes (default: dry-run)")
    ap.add_argument("--limit", type=int, default=None, help="max swaps per pair (test cap)")
    ap.add_argument("--allow-absent-sku", action="store_true",
                    help="permit a pairs.csv old_sku that is on ZERO cohort orders (default: refuse)")
    ap.add_argument("--login-window-days", type=int, default=30,
                    help="Recharge login lookback, days (30 = ~one charge cycle, Kurt-confirmed "
                         "2026-08-09; see SHORTS_PASS_RULES #4 before changing)")
    args = ap.parse_args()

    pairs = load_pairs(args.pairs)
    store, token = get_shopify_credentials()
    sess = requests.Session()      # Shopify REST
    rc_sess = requests.Session()   # Recharge

    t0 = time.time()
    print(f"[plan] {args.ship_tag}: fetching tag orders + login events...")
    orders = fetch_tag_orders(sess, store, token, args.ship_tag)
    email_by_order = {o["id"]: (o.get("email") or (o.get("customer") or {}).get("email") or "")
                      for o in orders}
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=args.login_window_days)).strftime("%Y-%m-%dT%H:%M:%S")
    # 🔴 Join-zero guard BEFORE the (slow) Recharge pass — a typo'd old_sku otherwise renders as
    # a harmless `elig 0 / plan 0` preview row instead of a bad pairs.csv (SHORTS_PASS_RULES #12).
    if not args.allow_absent_sku:
        bad = absent_pair_refusals(pairs, cohort_skus(orders))
        if bad:
            print(f"\n🔴 REFUSED: pairs.csv old_sku(s) appear on ZERO of {len(orders)} orders on "
                  f"{args.ship_tag} — a wrong SKU, not an empty pair:")
            print("\n".join(bad))
            print("  Re-run with --allow-absent-sku only if you truly expect it to be absent.")
            return 2

    login_ids = fetch_login_customer_ids(rc_sess, since)
    print(f"  {len(orders)} orders on tag; {len(login_ids)} customers logged in "
          f"since {since[:10]}")

    pre_counts = {}
    for p in pairs:
        pre_counts[p["old_sku"]] = count_fulfillable(orders, p["old_sku"])
        pre_counts[p["new_sku"]] = count_fulfillable(orders, p["new_sku"])

    excl_cache: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        plans = list(ex.map(lambda p: plan_pair(store, token, args.ship_tag, p,
                                                email_by_order, rc_sess, login_ids,
                                                excl_cache, args.limit), pairs))
    print(f"[plan] done in {time.time() - t0:.0f}s\n")

    # ---- preview table
    print(f"{'OLD':16} {'NEW':16} {'want':>4} {'elig':>4} {'plan':>4} {'excl':>4}")
    for p in plans:
        print(f"{p['old_sku']:16} {p['new_sku']:16} {p['count']:>4} {p['eligible']:>4} "
              f"{len(p['planned']):>4} {len(p['excluded']):>4}")
        for e in p["excluded"]:
            print(f"    excluded {e['order_name']}: {e['why']}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"shorts_pass_{args.ship_tag.replace('/', '_')}_{ts}.jsonl"

    def log(row: dict) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                                "ship_tag": args.ship_tag, **row}, default=str) + "\n")

    log({"phase": "plan", "apply": args.apply, "pairs": [
        {**p, "planned": [t["order_name"] for t in p["planned"]]} for p in plans]})

    if not args.apply:
        print(f"\nDRY-RUN (no writes). Log: {log_path}")
        return 0

    # ---- execute (canonical module path only)
    print("\n[apply] executing via order_edit._swap_order_skus ...")
    results = execute_plans(plans)
    log({"phase": "execute", "results": results})

    # ---- verify: the ONLY success authority (never call-count)
    print("\n[verify] re-fetching tag and re-counting fulfillable quantities...")
    post_orders = fetch_tag_orders(sess, store, token, args.ship_tag)
    mismatches = []
    for p in plans:
        n = len(p["planned"])
        for sku, delta in ((p["old_sku"], -n), (p["new_sku"], +n)):
            expected = pre_counts[sku] + delta
            actual = count_fulfillable(post_orders, sku)
            status = "OK" if actual == expected else "MISMATCH"
            print(f"  {sku:16} pre={pre_counts[sku]:>4} expected={expected:>4} "
                  f"actual={actual:>4}  {status}")
            if actual != expected:
                mismatches.append({"sku": sku, "expected": expected, "actual": actual})
    log({"phase": "verify", "mismatches": mismatches})
    if mismatches:
        print(f"\nVERIFY FAILED — {len(mismatches)} sku count mismatch(es). Log: {log_path}")
        return 1
    print(f"\nVERIFY OK — plan matches actual. Log: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
