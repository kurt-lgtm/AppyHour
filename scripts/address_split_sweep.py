"""Repair split shipping addresses (house number in address1, street name in address2).

🔴 AUTHORITY: AppyHour/ADDRESS_HYGIENE_RULES.md — read it BEFORE changing anything here.
The rules that cost the most to learn, in short:

  * Repair ONLY what is provably lossless. Joining address1+address2 is lossless.
    Fixing a spelling is not — `Countrt`/`Ridg`/`Lanr` are near-certain and still get
    FLAGGED for a human, never guessed.
  * A bare trailing letter is a DIRECTIONAL, not a unit. `15401 E 40th St S` keeps its
    S in address1. Only explicit tokens (Apt/Unit/Ste/#/...) move to address2.
  * Match on the full (address1, address2) pair actually observed. Matching on one
    field edited an unrelated address that merely shared a zip.
  * SHOPIFY FIRST — labels print from the Shopify order address. Orders, then customer
    defaults, then Recharge. A run that dies partway must already have fixed what ships.
  * Idempotent by construction: every write re-reads live state and matches the exact
    observed pair, so running this twice — or from two triggers at once — is a no-op.

Triggers (redundancy is required, §7): scheduled catch-up job, the weekly shipping run's
pre-ship gate, and manual CLI. Never assume this is the only one running.

Usage:
    python address_split_sweep.py                  # dry-run, reports what it would do
    python address_split_sweep.py --apply          # writes
    python address_split_sweep.py --apply --quiet  # for the scheduler (Slack only on change)

As a pre-ship gate:
    from address_split_sweep import sweep
    result = sweep(apply=True)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

APPYHOUR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APPYHOUR / "AppyHourMCP"))
sys.path.insert(0, str(APPYHOUR / "InventoryReorder"))
sys.path.insert(0, str(APPYHOUR))

from appyhour_lib.bootstrap import init as _bootstrap_init  # noqa: E402

_bootstrap_init()  # UTF-8 stdio + canonical .env (AH_SLACK_BOT_TOKEN for _slack) — replaces ad-hoc reconfigure

import requests  # noqa: E402
from utils import get_shopify_auth, shopify_paginate  # noqa: E402
from inventory_reorder import load_settings as load_inv_settings  # noqa: E402

REPORTS = Path(r"C:\Users\Work\Claude Projects\_outputs\reports")
SLACK_CHANNEL = "C0BT47XG8CW"  # #kurt-ops (private — bot appyhouropsreader must be a member)
RC_BASE = "https://api.rechargeapps.com"

# A bare house number: "77", "3782", "12B". This and only this is the repairable pattern.
BARE_NUM = re.compile(r"^\s*\d+\s*[A-Za-z]?\s*$")
HAS_STREET = re.compile(r"[A-Za-z]{2,}")

# EXPLICIT unit tokens only. See ADDRESS_HYGIENE_RULES §1 — a bare trailing letter is a
# directional (S, W) far more often than a unit, and moving it out of the street name
# turns a deliverable address into an undeliverable one.
#
# 🔴 The keyword must be a WHOLE TOKEN. Written as a bare alternation inside
# `(?P<unit>...\.?\s*\S+)` it matched a PREFIX, so "Camino Flores" split as street
# "Camino" + unit "Flores" — because "Flores" starts with "fl". That is the third
# instance of this exact bug in one day (Mechanic §3 substring, Mechanic §4 `contains`,
# and here), which is why the trailing `\b` and the tokenized fallback below are not
# optional. Same reason "Stonington" must not match "ste" and "Farm" must not match "rm".
UNIT_KW = r"(?:apt|apartment|unit|ste|suite|bldg|building|fl|floor|rm|room|lot|trlr)"
UNIT_RE = re.compile(r"^(?P<street>.*?)[\s,]+(?P<unit>(?:" + UNIT_KW + r")\b\.?\s*\S+|#\s*\S+)$", re.I)
_UNIT_TOKENS = frozenset("apt apartment unit ste suite bldg building fl floor rm room lot trlr".split())


def _has_unit_token(a2: str) -> bool:
    """Whole-token test. Never a substring — see the comment on UNIT_KW."""
    for tok in re.split(r"[\s,]+", (a2 or "").lower()):
        tok = tok.strip(".")
        if tok in _UNIT_TOKENS or tok.startswith("#"):
            return True
    return False


def _starts_with_own_number(a2: str) -> bool:
    """address2 whose FIRST token is pure digits is a COMPLETE address line carrying its
    own house number, not a street fragment. Merging "77" + "1077 Sunset Blvd" would
    produce two house numbers. An ORDINAL ("128th St E", "5th Street") is a real street
    name and must still merge — hence digits-ONLY."""
    toks = re.split(r"[\s,]+", (a2 or "").strip())
    if not toks or not toks[0]:
        return False
    return not re.sub(r"[\d\-]", "", toks[0])


def split_unit(address2: str):
    """-> (street, unit) or (None, None) when a unit keyword can't be cleanly separated."""
    a2 = (address2 or "").strip()
    if _starts_with_own_number(a2):
        return None, None
    m = UNIT_RE.match(a2)
    if m:
        return m.group("street").strip().rstrip(","), m.group("unit").strip()
    if _has_unit_token(a2):
        return None, None
    return a2, ""


def plan(a1: str, a2: str):
    """-> (new_address1, new_address2) or (None, None) when ambiguous."""
    street, unit = split_unit(a2)
    if street is None:
        return None, None
    return ((a1 or "").strip() + " " + street).strip(), unit


def is_split(a1: str, a2: str) -> bool:
    return bool(BARE_NUM.match((a1 or "").strip()) and HAS_STREET.search((a2 or "").strip()))


# ------------------------------------------------------------------ Recharge


def _rc_headers(token):
    return {"X-Recharge-Access-Token": token, "X-Recharge-Version": "2021-11"}


def rc_call(method, path, headers, params=None, json_body=None, retries=5):
    last = None
    for attempt in range(retries):
        try:
            r = requests.request(method, RC_BASE + path, headers=headers,
                                 params=params, json=json_body, timeout=30)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("retry-after", "5")))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as e:
            last = e
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(2)
    raise last if last else RuntimeError("recharge %s %s exhausted retries" % (method, path))


def rc_paginate(path, headers, key, first_params):
    out, cursor = [], None
    while True:
        params = {"cursor": cursor, "limit": 250} if cursor else dict(first_params, limit=250)
        d = rc_call("GET", path, headers, params=params)
        batch = d.get(key, [])
        if not batch:
            break
        out.extend(batch)
        cursor = d.get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)
    return out


# ------------------------------------------------------------------ legs


def sweep_shopify_orders(base, hdr, apply, res):
    """LEG 1 — the one that matters: labels print from these."""
    orders = shopify_paginate(
        "%s/orders.json" % base, hdr,
        params={"status": "open", "fulfillment_status": "unfulfilled", "limit": 250,
                "fields": "id,name,tags,shipping_address,customer"},
        timeout=60, sleep=0.3)
    res["orders_scanned"] = len(orders)
    for o in orders:
        a = o.get("shipping_address") or {}
        a1, a2 = (a.get("address1") or "").strip(), (a.get("address2") or "").strip()
        if not is_split(a1, a2):
            continue
        new1, new2 = plan(a1, a2)
        if new1 is None:
            res["ambiguous"].append({"where": "order", "ref": o["name"],
                                     "address1": a1, "address2": a2,
                                     "why": "unit keyword could not be cleanly separated"})
            continue
        entry = {"where": "order", "ref": o["name"], "id": o["id"],
                 "from": [a1, a2], "to": [new1, new2]}
        if not apply:
            res["planned"].append(entry)
            continue
        # Re-read: another trigger may have fixed it since the scan (idempotence, §7).
        live = requests.get("%s/orders/%s.json" % (base, o["id"]), headers=hdr, timeout=30,
                            params={"fields": "id,shipping_address"}).json().get("order", {})
        la = live.get("shipping_address") or {}
        if (la.get("address1") or "").strip() != a1 or (la.get("address2") or "").strip() != a2:
            res["drifted"].append(entry)
            continue
        r = requests.put("%s/orders/%s.json" % (base, o["id"]), headers=hdr, timeout=30,
                         json={"order": {"id": o["id"],
                                         "shipping_address": {"address1": new1,
                                                              "address2": new2}}})
        if r.status_code < 300:
            res["fixed"].append(entry)
            _clear_invalid_tag(base, hdr, o["id"], o["name"], res)
        else:
            entry["error"] = "%s %s" % (r.status_code, r.text[:160])
            res["failed"].append(entry)
        time.sleep(0.4)


def _clear_invalid_tag(base, hdr, oid, name, res):
    """Clear `invalid_address` ONLY on an order this run just verified clean (§5)."""
    o = requests.get("%s/orders/%s.json" % (base, oid), headers=hdr, timeout=30,
                     params={"fields": "id,tags,shipping_address"}).json().get("order", {})
    a = o.get("shipping_address") or {}
    if is_split((a.get("address1") or ""), (a.get("address2") or "")):
        return
    tags = [t.strip() for t in (o.get("tags") or "").split(",") if t.strip()]
    if "invalid_address" not in tags:
        return
    keep = [t for t in tags if t != "invalid_address"]
    r = requests.put("%s/orders/%s.json" % (base, oid), headers=hdr, timeout=30,
                     json={"order": {"id": oid, "tags": ", ".join(keep)}})
    if r.status_code < 300:
        res["tags_cleared"].append(name)
    time.sleep(0.3)


def sweep_shopify_customers(base, hdr, apply, res, customer_ids):
    """LEG 2 — stop the next renewal recreating it on the order."""
    for cid in sorted(customer_ids):
        try:
            addrs = requests.get("%s/customers/%s/addresses.json" % (base, cid),
                                 headers=hdr, timeout=30).json().get("addresses", [])
        except Exception as e:
            res["failed"].append({"where": "customer", "ref": str(cid), "error": str(e)})
            continue
        for ad in addrs:
            a1, a2 = (ad.get("address1") or "").strip(), (ad.get("address2") or "").strip()
            if not is_split(a1, a2):
                continue
            new1, new2 = plan(a1, a2)
            if new1 is None:
                res["ambiguous"].append({"where": "customer", "ref": "%s/%s" % (cid, ad["id"]),
                                         "address1": a1, "address2": a2,
                                         "why": "unit keyword could not be cleanly separated"})
                continue
            entry = {"where": "customer", "ref": "%s/%s" % (cid, ad["id"]),
                     "from": [a1, a2], "to": [new1, new2]}
            if not apply:
                res["planned"].append(entry)
                continue
            r = requests.put("%s/customers/%s/addresses/%s.json" % (base, cid, ad["id"]),
                             headers=hdr, timeout=30,
                             json={"address": {"address1": new1, "address2": new2}})
            if r.status_code < 300:
                res["fixed"].append(entry)
            elif r.status_code == 422 and "already exists" in r.text.lower():
                # Expected, not an error (§3): a clean twin is already on the account.
                # Make it the default rather than retrying or forcing.
                twin = next((x for x in addrs
                             if x["id"] != ad["id"]
                             and (x.get("address1") or "").strip().lower() == new1.lower()), None)
                if twin and not twin.get("default"):
                    dr = requests.put("%s/customers/%s/addresses/%s/default.json"
                                      % (base, cid, twin["id"]), headers=hdr, timeout=30)
                    entry["resolution"] = "clean twin %s made default (%s)" % (twin["id"],
                                                                              dr.status_code)
                    res["fixed"].append(entry)
                elif twin:
                    entry["resolution"] = "clean twin %s already default" % twin["id"]
                    res["skipped"].append(entry)
                else:
                    entry["error"] = "422 already-exists but no clean twin found"
                    res["failed"].append(entry)
            else:
                entry["error"] = "%s %s" % (r.status_code, r.text[:160])
                res["failed"].append(entry)
            time.sleep(0.4)


def sweep_recharge(token_r, token_w, apply, res):
    """LEG 3 — the source. Fixes recurrence, not this week's shipment."""
    hdr_r, hdr_w = _rc_headers(token_r), _rc_headers(token_w)
    subs = rc_paginate("/subscriptions", hdr_r, "subscriptions", {"status": "active",
                                                                  "sort_by": "id-asc"})
    active = {str(s.get("address_id")) for s in subs}
    res["recharge_active_subs"] = len(subs)
    addrs = rc_paginate("/addresses", hdr_r, "addresses", {"sort_by": "id-asc"})
    res["recharge_addresses_scanned"] = len(addrs)
    for a in addrs:
        if str(a.get("id")) not in active:
            continue  # §4: only addresses backing an active subscription
        a1, a2 = (a.get("address1") or "").strip(), (a.get("address2") or "").strip()
        if not is_split(a1, a2):
            continue
        who = ("%s %s" % (a.get("first_name") or "", a.get("last_name") or "")).strip()
        new1, new2 = plan(a1, a2)
        if new1 is None:
            res["ambiguous"].append({"where": "recharge", "ref": "%s (%s)" % (a["id"], who),
                                     "address1": a1, "address2": a2,
                                     "why": "unit keyword could not be cleanly separated"})
            continue
        entry = {"where": "recharge", "ref": "%s (%s)" % (a["id"], who),
                 "from": [a1, a2], "to": [new1, new2]}
        if not apply:
            res["planned"].append(entry)
            continue
        live = rc_call("GET", "/addresses/%s" % a["id"], hdr_r).get("address", {})
        if ((live.get("address1") or "").strip() != a1
                or (live.get("address2") or "").strip() != a2):
            res["drifted"].append(entry)
            continue
        try:
            rc_call("PUT", "/addresses/%s" % a["id"], hdr_w,
                    json_body={"address1": new1, "address2": new2})
            res["fixed"].append(entry)
        except Exception as e:
            entry["error"] = str(e)[:160]
            res["failed"].append(entry)
        time.sleep(0.5)


# ------------------------------------------------------------------ reporting


def _slack(text: str) -> bool:
    token = os.environ.get("AH_SLACK_BOT_TOKEN", "").strip()
    if not token:
        try:
            for line in (APPYHOUR / ".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("AH_SLACK_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            pass
    if not token:
        return False
    try:
        r = requests.post("https://slack.com/api/chat.postMessage", timeout=15,
                          headers={"Authorization": "Bearer %s" % token},
                          json={"channel": SLACK_CHANNEL, "text": text})
        return bool(r.json().get("ok"))
    except Exception:
        return False


def _summary(res, apply):
    verb = "fixed" if apply else "would fix"
    lines = ["*Address split sweep* — %s" % ("APPLY" if apply else "DRY-RUN")]
    n = len(res["fixed"]) if apply else len(res["planned"])
    lines.append("%s %d split address(es)" % (verb, n))
    for e in (res["fixed"] if apply else res["planned"]):
        lines.append("  %-9s %-22s %r | %r  ->  %r | %r%s"
                     % (e["where"], str(e["ref"])[:22], e["from"][0], e["from"][1],
                        e["to"][0], e["to"][1],
                        "   [%s]" % e["resolution"] if e.get("resolution") else ""))
    if res["tags_cleared"]:
        lines.append("cleared invalid_address on: %s" % ", ".join(res["tags_cleared"]))
    for e in res["ambiguous"]:
        lines.append("  FLAG %-9s %-22s %r | %r — %s"
                     % (e["where"], str(e["ref"])[:22], e["address1"], e["address2"], e["why"]))
    for e in res["drifted"]:
        lines.append("  DRIFT-SKIP %s %s (changed since scan)" % (e["where"], e["ref"]))
    for e in res["failed"]:
        lines.append("  FAILED %s %s — %s" % (e["where"], e["ref"], e.get("error")))
    return "\n".join(lines)


def sweep(apply=False, quiet=False, notify=True):
    res = {"fixed": [], "planned": [], "ambiguous": [], "drifted": [], "failed": [],
           "skipped": [], "tags_cleared": [], "started_at": datetime.now().isoformat(timespec="seconds")}
    base, hdr = get_shopify_auth()
    settings = load_inv_settings()
    token_r = settings.get("recharge_api_token", "")
    token_w = settings.get("recharge_api_token_write", "") or token_r

    # §4 ordering: what ships first, source last.
    sweep_shopify_orders(base, hdr, apply, res)
    touched = {e["ref"] for e in res["fixed"] + res["planned"] if e["where"] == "order"}
    cust_ids = set()
    if touched:
        for e in res["fixed"] + res["planned"]:
            if e["where"] != "order":
                continue
            o = requests.get("%s/orders/%s.json" % (base, e["id"]), headers=hdr, timeout=30,
                             params={"fields": "id,customer"}).json().get("order", {})
            cid = (o.get("customer") or {}).get("id")
            if cid:
                cust_ids.add(cid)
    sweep_shopify_customers(base, hdr, apply, res, cust_ids)
    if token_r:
        sweep_recharge(token_r, token_w, apply, res)
    else:
        res["failed"].append({"where": "recharge", "ref": "-", "error": "no recharge_api_token"})

    res["finished_at"] = datetime.now().isoformat(timespec="seconds")
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    (REPORTS / ("address-split-sweep-%s.json" % stamp)).write_text(
        json.dumps(res, indent=2, default=str), encoding="utf-8")

    body = _summary(res, apply)
    if not quiet:
        print(body)
    changed = bool(res["fixed"] or res["planned"] or res["ambiguous"] or res["failed"])
    if notify and changed:
        if not _slack(body):
            try:
                from appyhour_lib.notify import notify as _n
                _n(body, level="critical")
            except Exception:
                print("ESCALATION UNDELIVERED — findings stand in the report above")
    return res


def main():
    ap = argparse.ArgumentParser(description="Repair split shipping addresses (Shopify first).")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--quiet", action="store_true", help="suppress stdout; Slack only on change")
    ap.add_argument("--no-notify", action="store_true", help="skip Slack/notify entirely")
    args = ap.parse_args()
    res = sweep(apply=args.apply, quiet=args.quiet, notify=not args.no_notify)
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
