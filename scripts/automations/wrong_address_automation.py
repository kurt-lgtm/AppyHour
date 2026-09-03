"""Wrong-Address inbox automation (P2-2).

Removes Kurt from the physical loop of hunting ~25 "Wrong Customer Address"
emails/week, opening each Shopify order, and eyeballing the address.

PIPELINE (daily):
  1. DETECT  — IMAP-read hi@appyhourbox.com "Wrong * Address" notifications
               (last N days) -> Shopify order IDs.
  2. ENRICH  — GraphQL pull each order: name, customer, shippingAddress, tags.
  3. TRIAGE  — learned rules first (free, perpetual — grown from Kurt's own
               past fixes via address_learning.py), then heuristic:
                 AUTO_FIX   : a typo Kurt already corrected before (learned
                              substitution) -> auto-rewrite.
                 SAFE_UNTAG : rural false-positive (complete address, no PO box,
                              bare unit/route) OR a learned false-positive street
                              -> auto-untag (reversible).
                 NEEDS_FIX  : new/ambiguous -> STAGED for Kurt's decision. His
                              decision is captured next run and learned.
                 HOLD       : military/APO/FPO or already _HOLD -> left alone.
  4. ACT     — auto-apply AUTO_FIX + SAFE_UNTAG; stage NEEDS_FIX into a scaffold
               matching scripts/incident-fixes/fix_invalid_addresses format.
  5. LEARN   — capture_resolutions() at the top of each run reads what Kurt did
               to previously-staged orders; mine_candidates() proposes new rules
               for approval. NO paid API — the corpus is Kurt's own decisions.
  6. REPORT  — triage markdown digest.

Reuses the proven apply primitives (orderUpdate shippingAddress + tagsRemove)
validated 37/37 on the 6/15 cohort, 2026-06-12.

Customer/Recharge address PROPAGATION (so the fix sticks across renewals) stays
BLOCKED until Shopify app gets `write_customers` + Recharge token gets
address-write — see KURT-TODO #1/#2. This script only touches the ORDER.

Usage:
  python wrong_address_automation.py                          # dry-run: detect+triage+learn+report
  python wrong_address_automation.py --apply-untag --apply-fix  # auto-apply safe untags + learned typos
  python wrong_address_automation.py --days 1                 # window (default 2)

ASCII-only stdout (cp1252-safe). Live data — order writes gated behind --apply-* flags.
"""
import argparse
import email
import email.header
import imaplib
import json
import os
import re
import sys
import time
from datetime import datetime

_AH = r"C:\Users\Work\Claude Projects\AppyHour"
_MCP = os.path.join(_AH, "AppyHourMCP")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

import requests  # noqa: E402
from utils import get_shopify_auth  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import address_learning as learn  # noqa: E402

if _AH not in sys.path:
    sys.path.insert(0, _AH)
from appyhour_lib.paths import inventory_settings_path  # noqa: E402

# 🔴 Was the repo `dist/` copy, which exists in the DEV tree ONLY — the 2026-07-27 carrier-IMAP
# class of failure (a scheduled run raises FileNotFoundError while the parent still reports ok).
# Resolution order now belongs to appyhour_lib.paths: canonical C:\AppyHourData first, then the
# legacy %APPDATA% copy (MSIX-virtualized; packaged and unpackaged readers saw two divergent
# files for three weeks) with a loud warning, then the repo copies.
# Resolved LAZILY at the call site: the resolver raises when nothing exists anywhere, and an
# import-time raise would take down callers that only want the address heuristics.
OUT_DIR = r"C:\Users\Work\Claude Projects\_outputs\reports"
SENDER = "hi@appyhourbox.com"
# Subject variants seen in the inbox (Customer / GIFT / Specials AHB-X)
SUBJECT_RE = re.compile(r"wrong .*address", re.I)
ORDER_ID_RE = re.compile(r"/orders/(\d+)")
INVALID_TAG = "invalid_address"
HOLD_TAG = "_HOLD"
# 2026-08-17: CS/Flow hold tags were split per Dan's _HOLD proposal — _FLOWHOLD (Flow),
# _CSHOLD (agent), _UNRESOLVED (closed unresolved). Legacy _HOLD kept for pre-cutover
# orders. Matching only "_HOLD" goes SILENT on the new tags ("_FLOWHOLD" does not
# contain "_HOLD"), which would let this automation edit addresses on held orders.
HOLD_TAGS = (HOLD_TAG, "_FLOWHOLD", "_CSHOLD", "_UNRESOLVED")

# ── address heuristics ────────────────────────────────────────────────
_PO_BOX_RE = re.compile(r"\b(p\.?\s*o\.?\s*box|post office box)\b", re.I)
_HAS_NUMBER_RE = re.compile(r"\d")
_MILITARY = {"APO", "FPO", "DPO"}

# 🔴 2026-08-27: _BARE_UNIT_RE's keyword group was OPTIONAL and `[\w\-]+` matches any
# single word, so a one-word STREET NAME in address2 ("Grabowski", "MAPLEWOOD") — and an
# EMPTY address2 — read as a bare unit. Combined with _HAS_NUMBER_RE, which only asks
# whether address1 contains a digit, an address1 of "77" passed the "missing street
# number" gate and the order was certified a validator false-positive and UNTAGGED.
# Verified: 25/25 sampled SAFE_UNTAG orders have no invalid_address tag today, so the
# untag executes. That is the mechanism behind 8 Veho $20 Address Correction charges on
# orders that were flagged and shipped anyway. The keyword is now REQUIRED.
_BARE_UNIT_RE = re.compile(
    r"^(?:(?:lot|unit|apt|ste|suite|trlr|rte|route|lane|box|bldg|fl|rm)\b\.?\s*|#\s*)[\w\-]+$",
    re.I,
)

# A bare house number with no street: "77", "3782", "12B". address1 in this shape is
# undeliverable no matter what else is right, and must NEVER be untagged.
_BARE_HOUSE_NUM_RE = re.compile(r"^\s*\d+\s*[A-Za-z]?\s*$")
_HAS_STREET_RE = re.compile(r"[A-Za-z]{2,}")


def _decode(raw: str) -> str:
    out = []
    for part, charset in email.header.decode_header(raw or ""):
        out.append(part.decode(charset or "utf-8", errors="replace") if isinstance(part, bytes) else part)
    return " ".join(out)


def fetch_wrong_address_order_ids(days_back: int) -> list[str]:
    """IMAP-read the wrong-address notifications -> unique Shopify order IDs."""
    with open(inventory_settings_path(), encoding="utf-8") as f:
        s = json.load(f)
    user, password = s["smtp_user"], s["smtp_password"]
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, password)
    order_ids: list[str] = []
    seen: set[str] = set()
    try:
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            print("INBOX select failed")
            return []
        status, data = imap.search(
            None, "X-GM-RAW", f'"from:{SENDER} subject:address newer_than:{days_back}d"'
        )
        if status != "OK":
            return []
        for mid in data[0].split():
            st, md = imap.fetch(mid, "(RFC822)")
            if st != "OK":
                continue
            msg = email.message_from_bytes(md[0][1])
            if not SUBJECT_RE.search(_decode(msg.get("Subject", ""))):
                continue
            body = _extract_body(msg)
            for oid in ORDER_ID_RE.findall(body):
                if oid not in seen:
                    seen.add(oid)
                    order_ids.append(oid)
    finally:
        imap.logout()
    return order_ids


def _extract_body(msg) -> str:
    if msg.is_multipart():
        chunks = []
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    chunks.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        return "\n".join(chunks)
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace") if payload else ""


# ── Shopify ───────────────────────────────────────────────────────────
ORDER_QUERY = """
query($id: ID!) {
  order(id: $id) {
    id name tags
    customer { id email }
    shippingAddress { address1 address2 city provinceCode zip country }
  }
}"""
TAGS_REMOVE = """
mutation($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) { userErrors { field message } }
}"""
ORDER_UPDATE = """
mutation($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id name shippingAddress { address1 address2 city } }
    userErrors { field message }
  }
}"""


def gql(base, hdr, query, variables):
    r = requests.post(f"{base}/graphql.json", headers=hdr,
                      json={"query": query, "variables": variables}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("errors"):
        raise RuntimeError(json.dumps(j["errors"])[:300])
    return j["data"]


def triage(addr: dict, tags: list[str]) -> tuple[str, str]:
    """Return (category, reason). Categories: SAFE_UNTAG | NEEDS_FIX | HOLD."""
    a1 = (addr.get("address1") or "").strip()
    a2 = (addr.get("address2") or "").strip()
    city = (addr.get("city") or "").strip()
    province = (addr.get("provinceCode") or "").strip()
    zip_ = (addr.get("zip") or "").strip()

    held = [t for t in HOLD_TAGS if t in tags]
    if held or province in _MILITARY or "AA" == province or "AE" == province or "AP" == province:
        why = held[0] if held else "military/APO"
        return "HOLD", f"military/APO or already held ({why}) — leave for manual review"
    if _PO_BOX_RE.search(a1) or _PO_BOX_RE.search(a2):
        return "NEEDS_FIX", "PO box — carrier may reject; confirm with customer"
    # 🔴 This test runs BEFORE _HAS_NUMBER_RE. "77" contains a digit, so it sails through
    # the missing-street-number gate below; the only thing that catches it is asking
    # whether address1 is NOTHING BUT a number. See _BARE_HOUSE_NUM_RE above.
    if _BARE_HOUSE_NUM_RE.match(a1):
        if _HAS_STREET_RE.search(a2):
            return "NEEDS_FIX", "SPLIT: house number in address1, street in address2 — merge, never untag"
        return "NEEDS_FIX", "address1 is a bare house number with no street anywhere — undeliverable"
    if not a1 or not _HAS_NUMBER_RE.search(a1):
        return "NEEDS_FIX", "address1 missing street number — likely split/typo"
    if not (city and province and zip_):
        return "NEEDS_FIX", "missing city/state/zip"
    # has full street + city + state + zip, no PO box, a2 is bare unit/route ->
    # the validator over-flagged a rural/unit address that is actually fine.
    if not a2 or _BARE_UNIT_RE.match(a2):
        return "SAFE_UNTAG", "complete address; validator false-positive (rural/unit)"
    return "NEEDS_FIX", "address2 nonstandard — eyeball before shipping"


def main() -> int:
    ap = argparse.ArgumentParser(description="Wrong-Address inbox automation (P2-2)")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--apply-untag", action="store_true",
                    help="auto-apply SAFE_UNTAG + learned false-positives (reversible tagsRemove)")
    ap.add_argument("--apply-fix", action="store_true",
                    help="auto-apply LEARNED typo rewrites (orderUpdate + untag) — only patterns Kurt already approved")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    order_ids = fetch_wrong_address_order_ids(args.days)
    print(f"detected {len(order_ids)} wrong-address order(s) in last {args.days}d")
    if not order_ids:
        return 0

    base, hdr = get_shopify_auth()
    # LEARN FIRST: capture what Kurt decided on previously-staged orders.
    learned = learn.capture_resolutions(base and gql, base, hdr, ORDER_QUERY, INVALID_TAG)
    if learned:
        print(f"learned from {len(learned)} resolved order(s): "
              + ", ".join(f"{r['name']}={r['kind']}" for r in learned[:6]))
    fp_allow = learn.learned_false_positives()
    safe, needs, hold, gone, autofix = [], [], [], [], []
    resolved = 0
    for oid in order_ids:
        gid = f"gid://shopify/Order/{oid}"
        try:
            o = gql(base, hdr, ORDER_QUERY, {"id": gid})["order"]
        except Exception as e:  # noqa: BLE001
            print(f"  {oid}: pull failed — {e}")
            continue
        if not o:
            gone.append(oid)
            continue
        tags = o.get("tags") or []
        if INVALID_TAG not in tags:
            resolved += 1  # tag cleared since the email fired — nothing to do
            continue
        addr = o.get("shippingAddress") or {}
        cat, reason = triage(addr, tags)
        row = {
            "gid": gid, "name": o.get("name"), "reason": reason,
            "a1": addr.get("address1"), "a2": addr.get("address2"),
            "city": addr.get("city"), "prov": addr.get("provinceCode"), "zip": addr.get("zip"),
            "email": (o.get("customer") or {}).get("email"),
            "fix": None,
        }
        # LEARNED rules consulted FIRST (free, perpetual, grown from Kurt's own fixes):
        # 1) a previously-approved false-positive street -> auto-untag.
        if cat == "NEEDS_FIX" and learn.fp_key(addr) in fp_allow:
            cat, row["reason"] = "SAFE_UNTAG", "learned false-positive (Kurt untagged before)"
        # 2) a known typo Kurt already corrected -> auto-fixable rewrite.
        elif cat == "NEEDS_FIX":
            fixed_a1 = learn.apply_learned_typos(addr.get("address1") or "")
            if fixed_a1 != (addr.get("address1") or ""):
                row["fix"] = {"a1": fixed_a1, "a2": addr.get("address2") or "", "city": addr.get("city") or ""}
                cat, row["reason"] = "AUTO_FIX", "learned typo substitution"
        {"SAFE_UNTAG": safe, "NEEDS_FIX": needs, "HOLD": hold, "AUTO_FIX": autofix}[cat].append(row)
        if cat == "NEEDS_FIX":
            learn.record_pending(row)  # watch for Kurt's decision -> learn next run
        time.sleep(0.3)

    untagged = fixed = 0
    if args.apply_untag:
        for r in safe:
            try:
                d = gql(base, hdr, TAGS_REMOVE, {"id": r["gid"], "tags": [INVALID_TAG]})
                if d["tagsRemove"]["userErrors"]:
                    print(f"  untag ERROR {r['name']}: {d['tagsRemove']['userErrors']}")
                else:
                    untagged += 1
                time.sleep(0.35)
            except Exception as e:  # noqa: BLE001
                print(f"  untag EXCEPTION {r['name']}: {e}")
    if args.apply_fix:
        for r in autofix:
            fx = r["fix"]
            addr_in = {"address1": fx["a1"], "address2": fx["a2"]}
            if fx["city"]:
                addr_in["city"] = fx["city"]
            try:
                d = gql(base, hdr, ORDER_UPDATE, {"input": {"id": r["gid"], "shippingAddress": addr_in}})
                if d["orderUpdate"]["userErrors"]:
                    print(f"  fix ERROR {r['name']}: {d['orderUpdate']['userErrors']}")
                    continue
                d2 = gql(base, hdr, TAGS_REMOVE, {"id": r["gid"], "tags": [INVALID_TAG]})
                if d2["tagsRemove"]["userErrors"]:
                    print(f"  fix-untag ERROR {r['name']}: {d2['tagsRemove']['userErrors']}")
                else:
                    fixed += 1
                time.sleep(0.4)
            except Exception as e:  # noqa: BLE001
                print(f"  fix EXCEPTION {r['name']}: {e}")

    cand = learn.mine_candidates()  # surface new rules from the ledger for approval

    out_path = args.out or os.path.join(OUT_DIR, f"wrong-address-{datetime.now():%Y-%m-%d}.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    _write_digest(out_path, autofix, safe, needs, hold, gone, fixed, untagged,
                  args.apply_fix, args.apply_untag, learned, cand)

    print(f"AUTO_FIX: {len(autofix)} ({'applied ' + str(fixed) if args.apply_fix else 'staged'}) | "
          f"SAFE_UNTAG: {len(safe)} ({'untagged ' + str(untagged) if args.apply_untag else 'staged'}) | "
          f"NEEDS_FIX(your call): {len(needs)} | HOLD: {len(hold)} | tag-cleared: {resolved} | deleted: {len(gone)}")
    if cand.get("typos") or cand.get("false_positives"):
        print(f"NEW learned-rule candidates: {cand['typos']} typo, {cand['false_positives']} false-positive "
              f"-> approve in {cand.get('path')}")
    print(f"digest: {out_path}")
    return 0


def _write_digest(path, autofix, safe, needs, hold, gone, fixed, untagged,
                  did_fix, did_untag, learned, cand):
    L = [f"# Wrong-Address triage — {datetime.now():%Y-%m-%d}", ""]
    if learned:
        L.append(f"_Learned from {len(learned)} order(s) you resolved since last run: "
                 + ", ".join(f"{r['name']}={r['kind']}" for r in learned[:8]) + "_")
        L.append("")
    L.append(f"AUTO_FIX {len(autofix)} ({'applied ' + str(fixed) if did_fix else 'staged'}) | "
             f"SAFE_UNTAG {len(safe)} ({'untagged ' + str(untagged) if did_untag else 'staged'}) | "
             f"NEEDS_FIX {len(needs)} | HOLD {len(hold)} | resolved {len(gone)}")
    L.append("")
    L.append("## NEEDS_FIX — needs your call (the automation learns from what you do here)")
    L.append("Fix the address in Shopify (the next run captures your edit and learns the pattern), "
             "or fill the scaffold below + one-tap apply via "
             "`scripts/incident-fixes/fix_invalid_addresses_*.py`.")
    L.append("")
    if needs:
        L.append("```python")
        L.append("FIXES = {")
        for r in needs:
            cur = f"{r['a1']} | {r['a2'] or ''} | {r['city']}, {r['prov']} {r['zip']}"
            L.append(f'    "{r["gid"]}": {{"name": "{r["name"]}", "a1": "", "a2": ""}},  # NOW: {cur} | {r["reason"]}')
        L.append("}")
        L.append("```")
    else:
        L.append("_None — everything auto-handled._")
    L.append("")
    L.append("## AUTO_FIX — learned typo rewrites (patterns you already approved)")
    if autofix:
        L.append("| Order | Was | -> Corrected |")
        L.append("|---|---|---|")
        for r in autofix:
            was = f"{r['a1']} | {r['a2'] or ''}"
            now = f"{r['fix']['a1']} | {r['fix']['a2'] or ''}"
            L.append(f"| {r['name']} | {was} | {now} |")
        if not did_fix:
            L.append("")
            L.append("_Run with `--apply-fix` to write these._")
    else:
        L.append("_None._")
    L.append("")
    if cand.get("typos") or cand.get("false_positives"):
        L.append(f"## NEW rule candidates — {cand['typos']} typo, {cand['false_positives']} false-positive")
        L.append(f"Mined from your fixes. Review + approve in `{cand.get('path')}` to auto-apply next time.")
        L.append("")
    L.append("## SAFE_UNTAG — rural false-positives (reversible)")
    if safe:
        L.append("| Order | Address | Reason |")
        L.append("|---|---|---|")
        for r in safe:
            L.append(f"| {r['name']} | {r['a1']}, {r['city']} {r['prov']} {r['zip']} | {r['reason']} |")
    else:
        L.append("_None._")
    L.append("")
    if hold:
        L.append("## HOLD — manual (military/APO or a hold tag)")
        for r in hold:
            L.append(f"- {r['name']}: {r['reason']}")
        L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    _rc = main()
    if _rc == 0:
        # Dead-man heartbeat for routine `wrong-address-handler-daily` (HEARTBEAT_RULES rule 16).
        # Beats only on a clean exit — a 0-order day still ran detect+learn and IS the routine;
        # a non-zero exit did not do the work and must not beat. Graded by
        # scripts/automation_health.py EXPECTED["wrong-address-handler"] (4d: Mon-Fri 12:36).
        try:
            from appyhour_lib.heartbeat import beat
            beat("wrong-address-handler")
        except Exception:
            pass
    sys.exit(_rc)
