"""Klaviyo engagement for swap candidates: did this customer open an email in the last N days?

  python -m order_checks.klaviyo_opens <swap_csv> [--days 90] [--email-col email]

Read-only. Writes <swap_csv stem>_klaviyo.csv beside the input, same rows plus
`kl_state` / `kl_opens` / `kl_received`, sorted NO-OPEN first, then newest order first.

Why: inside the swappable pool (already cleared by login-OR-customize, failed-charge,
gift/PR guards) the customer who is NOT reading our emails is the one least likely to
notice or mind a substitution. INVENTORY_COORDINATOR rule 9: "no Klaviyo email open in
90d first (`Opened Email` metric), then newest orders."

🔴 GOTCHAS
  * This is a PRIORITY, never a guard. An open does not make a customer unswappable and
    no-open does not clear one -- the login/customize gate did that already.
  * "No Klaviyo profile" sorts WITH no-open (nothing was read), but is reported
    separately -- it can also mean an email mismatch between Shopify and Klaviyo.
  * An API error is its own state (`ERR<code>`), never folded into "no open": a 429 or
    500 that read as zero would push an engaged customer to the front of the line.
  * The metric is looked up by NAME ('Opened Email', integration Klaviyo). If it is not
    there, abort -- never guess a metric id.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
BASE = "https://a.klaviyo.com/api"
NO_OPEN = ("NO OPEN", "NO KLAVIYO PROFILE")


def _key():
    if os.environ.get("KLAVIYO_API_KEY"):
        return os.environ["KLAVIYO_API_KEY"]
    with open(ENV, encoding="utf-8") as fh:
        for line in fh:
            k, _, v = line.strip().partition("=")
            if k.strip() == "KLAVIYO_API_KEY":
                return v.strip().strip('"').strip("'")
    raise RuntimeError(f"KLAVIYO_API_KEY not configured (env or {ENV})")


class Klaviyo:
    def __init__(self):
        self.h = {"Authorization": "Klaviyo-API-Key " + _key(), "revision": "2024-10-15",
                  "accept": "application/json"}

    def get(self, path, params=None):
        r = None
        for i in range(6):
            r = requests.get(BASE + path, headers=self.h, params=params, timeout=40)
            if r.status_code != 429:
                return r
            time.sleep(float(r.headers.get("Retry-After") or 0) or 3 * (i + 1))
        return r

    def metric_ids(self):
        r = self.get("/metrics", {"filter": 'equals(integration.name,"Klaviyo")'})
        if r.status_code != 200:
            raise RuntimeError(f"Klaviyo /metrics {r.status_code}: {r.text[:200]}")
        m = {d["attributes"]["name"]: d["id"] for d in r.json()["data"]}
        if "Opened Email" not in m:
            raise RuntimeError("no 'Opened Email' metric -- refusing to guess an id")
        return m["Opened Email"], m.get("Received Email")

    def count(self, pid, metric, since):
        """-> 0/1 (any event in window) or 'ERR<code>'. page[size]=1: presence, not a total."""
        r = self.get("/events", {"filter": f'and(equals(profile_id,"{pid}"),'
                                           f'equals(metric_id,"{metric}"),'
                                           f'greater-than(datetime,{since}))',
                                 "page[size]": 1, "fields[event]": "datetime"})
        return len(r.json().get("data", [])) if r.status_code == 200 else f"ERR{r.status_code}"

    def state(self, email, open_id, recv_id, since):
        r = self.get("/profiles", {"filter": f'equals(email,"{email.strip()}")'})
        if r.status_code != 200:
            return {"kl_state": f"ERR{r.status_code}", "kl_opens": "", "kl_received": ""}
        d = r.json().get("data") or []
        if not d:
            return {"kl_state": "NO KLAVIYO PROFILE", "kl_opens": "", "kl_received": ""}
        pid = d[0]["id"]
        op = self.count(pid, open_id, since)
        rc = self.count(pid, recv_id, since) if recv_id else ""
        st = "NO OPEN" if op == 0 else ("opened" if isinstance(op, int) else op)
        return {"kl_state": st, "kl_opens": op, "kl_received": rc}


def annotate(rows, email_col="email", days=90, verbose=True):
    """-> rows + kl_* columns, NO-OPEN/no-profile first, then newest order first."""
    kl = Klaviyo()
    open_id, recv_id = kl.metric_ids()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache, out = {}, []
    for i, r in enumerate(rows, 1):
        em = (r.get(email_col) or "").strip().lower()
        if not em:
            out.append({**r, "kl_state": "NO EMAIL", "kl_opens": "", "kl_received": ""})
            continue
        if em not in cache:
            cache[em] = kl.state(em, open_id, recv_id, since)
            time.sleep(0.15)
        out.append({**r, **cache[em]})
        if verbose and i % 50 == 0:
            print(f"    klaviyo {i}/{len(rows)}")

    def order_no(r):
        digits = "".join(c for c in str(r.get("order") or r.get("Order ID") or "") if c.isdigit())
        return int(digits or 0)
    out.sort(key=lambda r: (0 if r["kl_state"] in NO_OPEN else 1, -order_no(r)))
    if verbose:
        n = sum(r["kl_state"] in NO_OPEN for r in out)
        err = sum(str(r["kl_state"]).startswith("ERR") for r in out)
        print(f"    klaviyo: {n} of {len(out)} no open in {days}d (incl. no profile)"
              + (f"   \U0001f534 {err} API errors -- NOT counted as no-open" if err else ""))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="order_checks.klaviyo_opens")
    ap.add_argument("csv")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--email-col", default="email")
    ap.add_argument("--tag", help="RMFG tag: join customer email onto rows by 'Order ID' "
                                  "(check7's swap CSV carries no email)")
    a = ap.parse_args(argv)
    with open(a.csv, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"{a.csv}: no rows")
    if a.tag:
        from .fetch_gql import fetch_by_tag
        em = {k: ((o.get("customer") or {}).get("email") or "")
              for k, o in fetch_by_tag(a.tag, verbose=False).items()}
        rows = [{**r, a.email_col: em.get(str(r.get("Order ID", "")).lstrip("#"), "")} for r in rows]
    if a.email_col not in rows[0]:
        sys.exit(f"{a.csv}: no '{a.email_col}' column (have {list(rows[0])}) -- pass --tag")
    out = annotate(rows, a.email_col, a.days)
    dst = os.path.splitext(a.csv)[0] + "_klaviyo.csv"
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"    -> {dst} ({len(out)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
