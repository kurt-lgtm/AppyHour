#!/usr/bin/env python
"""outreach.py — weekly customer-outreach list + notice drafting (READ-ONLY builder).

SSOT: scripts/OUTREACH_RULES.md — read it BEFORE changing this file.

    python scripts/outreach.py SHIP_TAG --type sub|refund|short --items items.csv [--out DIR]

🔴 THIS TOOL NEVER SENDS. It builds contacts.csv + per-customer draft .md files only.
Sending is a separate explicit human/session action in Gorgias. Do NOT add a --send flag.

Guardrails enforced here (see OUTREACH_RULES.md gotchas):
- prior-contact Gorgias check per email (YES/NO/UNKNOWN — API failure => UNKNOWN, never NO)
- drafts stamped DRAFT-NEEDS-HUMANIZER (final wording is a humanizer session step)
- refund amount = actual refund records only, else MISSING (never catalog/list price)
- item names verbatim from order line items (active_line_items) — never fabricated
- one row + one draft per customer email (dedupe across orders/items)
- never overwrite a prior output dir (version alongside)
"""

from __future__ import annotations

import argparse
import csv
import json
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 🔴 Windows cp1252 kills any non-ASCII print (arrows, emoji) MID-RUN — for a --apply tool that
# means a crash BETWEEN mutations, leaving the batch half-applied. Wrap stdout before anything
# prints, including argparse --help. (Live 2026-08-09: shorts_pass.py --help died on U+2192.)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


SCRIPTS_DIR = Path(__file__).resolve().parent
APPYHOUR_ROOT = SCRIPTS_DIR.parent
MCP_DIR = APPYHOUR_ROOT / "AppyHourMCP"
OUT_ROOT = APPYHOUR_ROOT.parent / "_outputs" / "artifacts"

for _p in (str(APPYHOUR_ROOT), str(MCP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Windows cp1252 stdout guard
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VALID_TYPES = ("sub", "refund", "short")
GORGIAS_WORKERS = 3  # stay under Gorgias ~2 req/s cap; _gorgias_internal paces per-call

TEMPLATES = {
    "sub": (
        "Hi {first_name},\n\n"
        "Quick heads-up about your upcoming AppyHour box (order {order_number}): we had to make a "
        "substitution this week.\n\n{item_lines}\n"
        "The replacement is a comparable item we think you'll enjoy. If you'd rather we handle it "
        "differently, just reply and we'll make it right.\n"
    ),
    "refund": (
        "Hi {first_name},\n\n"
        "We're reaching out about your order {order_number}. We weren't able to include the "
        "following item(s), and we've refunded you for them:\n\n{item_lines}\n"
        "Refund amount: {amount}\n\n"
        "The refund goes back to your original payment method. Reply if anything looks off and "
        "we'll sort it out.\n"
    ),
    "short": (
        "Hi {first_name},\n\n"
        "A heads-up about your order {order_number}: the following item(s) were shorted this week:\n\n"
        "{item_lines}\n"
        "Reply and let us know how you'd like us to make it right.\n"
    ),
}


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_orders(ship_tag: str) -> list[dict]:
    """All open orders on the tag via cached shopify_paginate (read-only)."""
    from utils import get_shopify_auth, shopify_paginate  # AppyHourMCP/utils.py

    base, headers = get_shopify_auth()
    fields = "id,name,order_number,email,customer,tags,line_items,refunds,cancelled_at"
    orders = shopify_paginate(
        f"{base}/orders.json",
        headers,
        params={"status": "any", "limit": 250, "fields": fields},
        key="orders",
    )
    tag = ship_tag.lower()
    return [
        o for o in orders
        if tag in [t.strip().lower() for t in (o.get("tags") or "").split(",")]
        and not o.get("cancelled_at")
    ]


def load_items_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows or "sku" not in rows[0]:
        raise ValueError(f"{path}: items csv must have a 'sku' column (got {list(rows[0].keys()) if rows else 'empty'})")
    return rows


def actual_refund_amount(order: dict, affected_line_ids: set[int]) -> str:
    """Actual-paid refund amount from the order's refund records. MISSING if none.

    NEVER computed from catalog/list price (OUTREACH_RULES gotcha 4).
    Sums refund_line_items subtotal+tax for affected lines; if refunds exist but
    none map to affected lines, falls back to MISSING (flag, don't guess).
    """
    total = 0.0
    found = False
    for refund in order.get("refunds", []):
        for rli in refund.get("refund_line_items", []):
            if rli.get("line_item_id") in affected_line_ids:
                total += float(rli.get("subtotal", 0) or 0) + float(rli.get("total_tax", 0) or 0)
                found = True
    return f"{total:.2f}" if found else "MISSING"


def gorgias_prior_contact(email: str) -> tuple[str, str]:
    """(prior_contact YES/NO/UNKNOWN, ticket refs). Failure => UNKNOWN, never NO."""
    try:
        from tools._gorgias_internal import gorgias_paginate  # rate-limited + retried

        tickets = gorgias_paginate(
            "tickets",
            params={"customer_email": email, "order_by": "updated_datetime:desc"},
            limit=10,
        )
        recent = [
            t for t in tickets
            if t.get("status") == "open" or (t.get("updated_datetime") or "") >= _recent_cutoff()
        ]
        if recent:
            refs = ";".join(str(t.get("id")) for t in recent)
            return "YES", refs
        return "NO", ""
    except Exception as e:  # noqa: BLE001 — any API failure means we DON'T know
        print(f"  ⚠️ gorgias lookup failed for {email}: {type(e).__name__}: {e}", file=sys.stderr)
        return "UNKNOWN", ""


def _recent_cutoff(days: int = 14) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_customer_rows(orders: list[dict], items: list[dict], notice_type: str) -> list[dict]:
    """One row per customer email, covering ALL their affected items across orders."""
    from utils import active_line_items

    affected_skus = {r["sku"].strip(): r for r in items if r.get("sku", "").strip()}
    by_email: dict[str, dict] = {}

    for order in orders:
        email = (order.get("email") or "").strip().lower()
        if not email:
            continue
        lines = active_line_items(order) if notice_type != "refund" else order.get("line_items", [])
        hits, hit_ids = [], set()
        for li in lines:
            row = affected_skus.get((li.get("sku") or "").strip())
            if row is not None:
                # item name VERBATIM from the order line item — never fabricated
                hits.append({
                    "sku": li.get("sku"),
                    "title": li.get("title", ""),
                    "quantity": li.get("quantity", 0),
                    "new_sku": (row.get("new_sku") or "").strip(),
                    "note": (row.get("note") or "").strip(),
                })
                hit_ids.add(li["id"])
        if not hits:
            continue

        entry = by_email.setdefault(email, {
            "email": email,
            "first_name": (order.get("customer") or {}).get("first_name") or "there",
            "orders": [],
            "items": [],
            "amount_cents_str": [],
        })
        entry["orders"].append(order.get("name") or f"#{order.get('order_number')}")
        entry["items"].extend(hits)
        if notice_type == "refund":
            entry["amount_cents_str"].append(actual_refund_amount(order, hit_ids))

    rows = []
    for entry in by_email.values():
        if notice_type == "refund":
            amts = entry["amount_cents_str"]
            if any(a == "MISSING" for a in amts):
                entry["amount"] = "MISSING"
            else:
                entry["amount"] = f"${sum(float(a) for a in amts):.2f}"
        else:
            entry["amount"] = ""
        rows.append(entry)
    return sorted(rows, key=lambda r: r["email"])


def render_draft(entry: dict, notice_type: str) -> str:
    item_lines = ""
    for it in entry["items"]:
        line = f"- {it['title']} (x{it['quantity']})"
        if notice_type == "sub" and it["new_sku"]:
            line += f" → replaced with SKU {it['new_sku']} [VERIFY replacement name from order/catalog — do not fabricate]"
        if it["note"]:
            line += f" — {it['note']}"
        item_lines += line + "\n"
    body = TEMPLATES[notice_type].format(
        first_name=entry["first_name"],
        order_number=", ".join(dict.fromkeys(entry["orders"])),
        item_lines=item_lines,
        amount=entry.get("amount", ""),
    )
    header = (
        "---\n"
        "status: DRAFT-NEEDS-HUMANIZER\n"
        f"type: {notice_type}\n"
        f"email: {entry['email']}\n"
        f"prior_contact: {entry.get('prior_contact', 'UNKNOWN')}\n"
        f"gorgias_ticket_refs: {entry.get('gorgias_ticket_refs', '')}\n"
        "note: NOT final wording. Run through humanizer; a human sends via Gorgias. This tool never sends.\n"
        "---\n\n"
    )
    return header + body


def resolve_out_dir(base: Path, tag: str, notice_type: str) -> Path:
    """Never overwrite a prior run's dir — version alongside."""
    d = base / f"outreach-{tag}-{notice_type}"
    n = 2
    while d.exists():
        d = base / f"outreach-{tag}-{notice_type}-{n}"
        n += 1
    return d


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build outreach list + notice drafts (NEVER sends).")
    ap.add_argument("ship_tag")
    ap.add_argument("--type", required=True, choices=VALID_TYPES, dest="notice_type")
    ap.add_argument("--items", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=OUT_ROOT)
    ap.add_argument("--skip-gorgias", action="store_true",
                    help="skip prior-contact lookups (rows marked UNKNOWN)")
    args = ap.parse_args(argv)

    t0 = time.time()
    items = load_items_csv(args.items)
    print(f"items.csv: {len(items)} affected SKU rows")

    orders = fetch_orders(args.ship_tag)
    print(f"{args.ship_tag}: {len(orders)} open orders on tag")
    if not orders:
        print("⚠️ zero orders — verify the tag exists in Shopify before trusting this (a zero is a claim).")

    rows = build_customer_rows(orders, items, args.notice_type)
    print(f"{len(rows)} customers affected (deduped by email)")

    # Prior-contact check (mandatory; --skip-gorgias marks UNKNOWN, never NO)
    if args.skip_gorgias:
        for r in rows:
            r["prior_contact"], r["gorgias_ticket_refs"] = "UNKNOWN", ""
    else:
        with ThreadPoolExecutor(max_workers=GORGIAS_WORKERS) as pool:
            results = list(pool.map(lambda r: gorgias_prior_contact(r["email"]), rows))
        for r, (flag, refs) in zip(rows, results):
            r["prior_contact"], r["gorgias_ticket_refs"] = flag, refs

    out_dir = resolve_out_dir(args.out, args.ship_tag, args.notice_type)
    out_dir.mkdir(parents=True)

    with open(out_dir / "contacts.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["email", "order_numbers", "items", "amount", "prior_contact", "gorgias_ticket_refs"])
        for r in rows:
            item_str = "; ".join(f"{i['title']} x{i['quantity']}" for i in r["items"])
            w.writerow([r["email"], ", ".join(dict.fromkeys(r["orders"])), item_str,
                        r["amount"], r["prior_contact"], r["gorgias_ticket_refs"]])

    for r in rows:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in r["email"])
        (out_dir / f"draft-{safe}.md").write_text(render_draft(r, args.notice_type), encoding="utf-8")

    with open(out_dir / "run.json", "w", encoding="utf-8") as f:
        json.dump({
            "ship_tag": args.ship_tag, "type": args.notice_type, "items_csv": str(args.items),
            "orders_on_tag": len(orders), "customers": len(rows),
            "already_contacted": sum(1 for r in rows if r["prior_contact"] == "YES"),
            "prior_contact_unknown": sum(1 for r in rows if r["prior_contact"] == "UNKNOWN"),
            "elapsed_s": round(time.time() - t0, 1),
        }, f, indent=2)

    print(f"→ {out_dir}")
    print(f"⏱️ {time.time() - t0:.1f}s. Drafts are DRAFT-NEEDS-HUMANIZER — this tool NEVER sends.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
