"""Audit the unfillable rows of UPDATE_Operational Issues.

For every row missing Issue Type and/or Order#, fetch the actual Gorgias
ticket and propose the correct values using:
  * the canonical issue classifier (ingest.slack_reship.parse.classify — same
    taxonomy as the Demi spec / weekly matrix), applied to subject + customer text
  * the sync module's own order-number extractors (integrations → text → email)
  * Kurt's documented decision rules:
      - ops/change requests ("pls ship this week", swaps, address changes) are
        NOT shipping failures → propose EXCLUDE
      - "arrived in time / not cool to touch" is NOT Arrived Warm
      - a shipping failure outranks a simultaneous cancel request (the
        CancelSubAndOrder mis-tag lesson, 2026-06-26)
      - NEVER fabricate: no signal in the ticket → MISSING (data-discipline rule)

READ-ONLY everywhere. Output = CSV of proposed fills + reasons; the sheet is
only written after Kurt reviews the CSV (live-writes gate).

Run:  python scripts/audits/audit_ops_sheet_unfillable.py [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "AppyHourMCP"))
sys.path.insert(0, str(ROOT / "AppyHourMCP" / "tools"))
sys.path.insert(0, str(ROOT / "GelPackCalculator"))

from ingest.slack_reship.parse import classify  # canonical taxonomy  # noqa: E402
from tools.gorgias_sheets_sync import (  # noqa: E402
    SPREADSHEET_ID, TAB_NAME, FIELD_ISSUE_TYPE,
    _gorgias_auth, _gorgias_get, _extract_order_number,
)
from google_integration import GoogleIntegration  # noqa: E402

OUT = ROOT.parent / "_outputs" / "reports" / "2026-07-25-ops-sheet-unfillable-audit.csv"

# Both an existing col-H value and a fresh text classification map to a coarse
# issue class; mismatch at THIS level = flagged (subvariant nitpicks ignored).
CLASS_MAP = [
    ("Arrived Warm", "Warm"), ("Melted", "Warm"),
    ("Delayed", "Delayed"),
    ("Lost in Transit", "Lost"), ("Misdeliver", "Lost"),
    ("Cannot be delivered", "Undeliverable"),
    ("Ice Pack", "Damaged"), ("Damaged", "Damaged"), ("Box damaged", "Damaged"),
    ("Missing item", "Fulfillment"), ("Wrong", "Fulfillment"), ("Substitute", "Fulfillment"),
]


def coarse(label: str) -> str:
    for pat, cls in CLASS_MAP:
        if pat.lower() in (label or "").lower():
            return cls
    return "Other"


OPS_REQUEST = re.compile(
    r"ship (this|next) week|pls ship|please ship|request change|address change|"
    r"change address|swap|can we (change|guarantee|offer)|repeat box|tasting guide|"
    r"cancel (my )?(sub|subscription|order)\b",
    re.I,
)
ON_TIME_NOT_WARM = re.compile(r"(not cool to touch|arrived (in|on) time)", re.I)


def first_customer_text(ticket: dict) -> str:
    for m in ticket.get("messages", []) or []:
        if m.get("from_agent") is False or (m.get("sender") or {}).get("id") == (ticket.get("customer") or {}).get("id"):
            txt = m.get("body_text") or m.get("stripped_text") or ""
            if txt.strip():
                return txt[:800]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap audited rows (0 = all)")
    args = ap.parse_args()

    auth, base_url = _gorgias_auth()
    # No path -> resolves via appyhour_lib.credentials (inline JSON or key file).
    g = GoogleIntegration()
    rows = g.read_sheet(SPREADSHEET_ID, f"'{TAB_NAME}'!A1:J")

    def in_window(datestr: str) -> bool:
        m = re.match(r"(\d\d)/(\d\d)/(\d{4})", datestr or "")
        return bool(m) and (int(m.group(3)), int(m.group(1)), int(m.group(2))) >= (2026, 6, 1)

    # Audit EVERY windowed row: fill blanks AND verify populated Issue Types
    # (Demi's auto-classification can be wrong; CS also hand-closes — neither
    # source is trusted blind, per Kurt 2026-07-25).
    targets = []
    for i, r in enumerate(rows):
        if i == 0:
            continue
        while len(r) < 10:
            r.append("")
        order, link, issue = r[2].strip(), r[3].strip(), r[7].strip()
        if not in_window(r[0]) and (order and issue):
            continue  # old + complete → skip; old + broken still audited
        targets.append((i + 1, r))
    if args.limit:
        targets = targets[: args.limit]
    print(f"rows to audit: {len(targets)}")

    out_rows = []
    for sheet_row, r in targets:
        order, link, issue = r[2].strip(), r[3].strip(), r[7].strip()
        rec = {
            "sheet_row": sheet_row, "date": r[0], "current_order": order,
            "current_issue": issue, "gorgias_link": link,
            "proposed_order": "", "proposed_issue": "", "verdict": "", "evidence": "",
        }
        tid_m = re.search(r"/(\d+)(?:[?#]|\s*$)", link)
        if not tid_m:
            rec["verdict"] = "MISSING"
            rec["evidence"] = "no usable gorgias link"
            out_rows.append(rec)
            continue
        try:
            resp = _gorgias_get(f"{base_url}/tickets/{tid_m.group(1)}", auth=auth)
            if resp.status_code == 404:
                rec["verdict"] = "TICKET_GONE"
                rec["evidence"] = "gorgias 404 — ticket deleted/merged; link is dead"
                out_rows.append(rec)
                continue
            if resp.status_code != 200:
                rec["verdict"] = "MISSING"
                rec["evidence"] = f"ticket fetch HTTP {resp.status_code}"
                out_rows.append(rec)
                continue
            ticket = resp.json()
        except Exception as e:
            rec["verdict"] = "MISSING"
            rec["evidence"] = f"ticket fetch {type(e).__name__}"
            out_rows.append(rec)
            continue

        subject = ticket.get("subject") or ""
        body = first_customer_text(ticket)
        text = f"{subject}\n{body}"
        cf_issue = (ticket.get("custom_fields", {}) or {}).get(FIELD_ISSUE_TYPE, {}).get("value", "")

        # order number
        if not order:
            found = _extract_order_number(ticket, gorgias_auth=auth, gorgias_base=base_url)
            rec["proposed_order"] = found or "MISSING"

        # classify ticket text with the canonical taxonomy + Kurt rules
        label, team = classify(text)
        if label and ON_TIME_NOT_WARM.search(text) and "Warm" in label:
            label, team = None, None
            text_verdict = "NOT_A_FAILURE (arrived in time — Kurt rule)"
        elif label:
            text_verdict = f"CLASSIFIED ({team})"
        elif OPS_REQUEST.search(text):
            text_verdict = "NOT_A_FAILURE (ops/change request)"
        else:
            text_verdict = "NO_SIGNAL"

        if not issue:
            # blank col H → propose a fill. cf_issue is NOT trusted blind
            # (Demi's auto-set can be wrong): only adopt it when the ticket
            # text agrees or gives no signal of its own.
            if cf_issue and label and coarse(cf_issue) != coarse(label):
                rec["proposed_issue"] = label
                rec["verdict"] = f"FIELD_VS_TEXT_MISMATCH (field said {coarse(cf_issue)})"
            elif cf_issue:
                rec["proposed_issue"] = cf_issue
                rec["verdict"] = "FROM_GORGIAS_FIELD (text agrees or silent)"
            elif label:
                rec["proposed_issue"] = label
                rec["verdict"] = text_verdict
            elif "NOT_A_FAILURE" in text_verdict:
                rec["proposed_issue"] = "EXCLUDE"
                rec["verdict"] = text_verdict
            else:
                rec["proposed_issue"] = "MISSING"
                rec["verdict"] = "NO_SIGNAL"
        else:
            # populated col H → VERIFY against ticket text
            if label and coarse(issue) != coarse(label):
                rec["proposed_issue"] = label
                rec["verdict"] = f"SHEET_VS_TEXT_MISMATCH (sheet={coarse(issue)}, text={coarse(label)})"
            elif "NOT_A_FAILURE" in text_verdict and coarse(issue) != "Fulfillment":
                rec["proposed_issue"] = "EXCLUDE?"
                rec["verdict"] = f"SHEET_SAYS_FAILURE_TEXT_SAYS_NOT ({text_verdict})"
            elif label:
                rec["verdict"] = "VERIFIED_MATCH"
            else:
                rec["verdict"] = "NO_SIGNAL_TO_VERIFY"
        if not order and not rec["verdict"]:
            rec["verdict"] = "ORDER_ONLY"
        rec["evidence"] = (subject or body)[:120].replace("\n", " ")
        out_rows.append(rec)
        time.sleep(0.25)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    from collections import Counter
    print("verdicts:", dict(Counter(x["verdict"] for x in out_rows).most_common()))
    print("proposed order# recovered:", sum(1 for x in out_rows if x["proposed_order"] not in ("", "MISSING")))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
