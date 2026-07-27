"""Slack #reship-and-order-requests → structured shipping-ticket records.

WHY: CS reliably types the issue diagnosis into the Slack post ("Delay in
transit", "Arrived warm"), but leaves the Gorgias Contact Reason / custom_fields
empty (confirmed 2026-06-26: tickets 278775358, 278667512 → tags=['auto-reply'],
custom_fields={}). The feedback-DB sync classifies off that empty Gorgias field,
so ~50% of shipping tickets fall out of the weekly counts. Slack IS the real
categorization layer. This module turns each Slack post into a typed record.

Pure functions only — no DB, no network. Loader lives in sync.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional

# --- canonical issue taxonomy (mirrors feedback.issue_type nesting) ----------
# Order matters: first matching rule wins, most-specific first.
# team: "shipping" = carrier/cold-chain failure (counts in the weekly report);
#       "fulfillment" = pack error (Order::*, surfaced separately);
#       None = ops request, NOT a failure (ship-this-week, swaps, questions).
ISSUE_RULES: list[tuple[str, str, str]] = [
    # (regex, canonical_issue, team)
    (r"melted\s+ice|ice\s*pack\s*melt", "Shipping::Damaged in transit::Arrived Warm (melted)", "shipping"),
    (r"(ice|gel)\s*pack\s*(leak|broke|exploded|leaked)", "Shipping::Damaged in transit::Broken/Leaking Ice Pack", "shipping"),
    (r"arriv\w*\s+warm|arrive\s+warm|arrived\s+warm|\bwarm\b", "Shipping::Damaged in transit::Arrived Warm", "shipping"),
    (r"lost\s+in\s+transit|\blost\b", "Shipping::Lost in Transit/Misdelivered::Lost", "shipping"),
    (r"misdeliver\w*|mis-deliver\w*", "Shipping::Lost in Transit/Misdelivered::Misdelivered", "shipping"),
    (r"cannot\s+be?\s+deliver\w*|undeliverable|can'?t\s+deliver", "Shipping::Cannot be delivered", "shipping"),
    (r"delay\w*\s+in\s+transit|delay\s+in\s+transit|\bdelay\w*\b", "Shipping::Delayed in transit", "shipping"),
    (r"damage\w*\s+box|box\s+damage\w*|damaged\s+in\s+transit", "Shipping::Damaged in transit::Box damaged", "shipping"),
    # fulfillment / pack errors — captured but flagged as a different team
    (r"missing\s+\d*\s*item|missing\s+\w+\s+item", "Order::Missing item", "fulfillment"),
    (r"wrong\s+order|wrong\s+item", "Order::Wrong item", "fulfillment"),
]

# Phrases that mark a post as an OPS REQUEST, not a failure. If a post has no
# shipping/fulfillment keyword AND matches one of these, classify -> None.
OPS_HINTS = re.compile(
    r"ship\s+this\s+week|pls\s+ship|please\s+ship|can\s+we\s+(change|sub|replace|offer|guarantee|make|ship)"
    r"|request\s+change|going\s+to\s+arrive|arrive\s+on\s+time|rerouting|repeat\s+box|change\s+it\s+to",
    re.I,
)

_ORDER_NUM = re.compile(r"#\s*(\d{5,6})\b")                     # 6-digit Shopify order_number
_ORDER_LINK = re.compile(r"orders/\d+\|#?(\d{5,6})")           # Slack admin-link display "orders/<id>|160549"
_ORDER_ID = re.compile(r"/orders/(\d{10,})")                    # long Shopify order_id (fallback)
_GORGIAS = re.compile(r"gorgias\.com/app/(?:views/\d+/|ticket/)(\d+)")
_MENTION = re.compile(r"<@(\w+)\|([^>]+)>")
# concise-format record terminator: " [YYYY-MM-DD HH:MM:SS TZ]"
_TS = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ([A-Z]{2,4})\]")


@dataclass(frozen=True)
class ReshipRecord:
    order_number: Optional[int]
    issue: Optional[str]
    team: Optional[str]
    gorgias_id: Optional[int]
    created_ts: Optional[str]      # the Slack-post time (proxy for ticket creation)
    reporter: Optional[str]
    raw: str


# "can't/cannot/don't/without/avoid delay" and "delay it further" are expedite
# REQUESTS about a not-yet-failed order, NOT a transit-delay failure (Kurt 2026-07-27,
# #165420 shipped fine but "we can't delay it further" tripped the delay rule).
_NEG_DELAY = re.compile(
    r"can'?t\s+(?:\w+\s+){0,3}delay|can\s?not\s+(?:\w+\s+){0,3}delay|do\s?n'?t\s+(?:\w+\s+){0,3}delay"
    r"|do\s+not\s+(?:\w+\s+){0,3}delay|wo\s?n'?t\s+(?:\w+\s+){0,3}delay|will\s+not\s+(?:\w+\s+){0,3}delay"
    r"|without\s+delay|no\s+(?:further\s+)?delay|avoid\s+(?:\w+\s+){0,2}delay"
    r"|delay\w*\s+(?:it|this|them|the\s+\w+)\s+further|further\s+delay"
)


def classify(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (canonical_issue, team). (None, None) for ops requests / no match."""
    t = text.lower()
    for pat, issue, team in ISSUE_RULES:
        if re.search(pat, t):
            if issue == "Shipping::Delayed in transit" and _NEG_DELAY.search(t):
                continue  # expedite request, not a delay failure
            return issue, team
    return None, None


def _first(pat: re.Pattern, text: str, group: int = 1) -> Optional[str]:
    m = pat.search(text)
    return m.group(group) if m else None


def parse_record(raw: str, created_ts: Optional[str] = None) -> Optional[ReshipRecord]:
    """Parse ONE Slack message. Returns None if it isn't a trackable ticket.

    created_ts: pass the authoritative receipt timestamp (ISO 'YYYY-MM-DD HH:MM:SS')
    from the Slack API message; if omitted, fall back to the [..] stamp in the
    concise-dump text. Receipt date is what the weekly window filters on.
    """
    issue, team = classify(raw)
    if issue is None:
        # no failure keyword — drop if it's clearly an ops request, else skip
        return None
    onum = _first(_ORDER_LINK, raw) or _first(_ORDER_NUM, raw)
    order_number = int(onum) if onum else None
    gid = _first(_GORGIAS, raw)
    if created_ts is None:
        ts = _TS.search(raw)
        created_ts = ts.group(1) if ts else None
    mentions = _MENTION.findall(raw)
    return ReshipRecord(
        order_number=order_number,
        issue=issue,
        team=team,
        gorgias_id=int(gid) if gid else None,
        created_ts=created_ts,
        reporter=mentions[0][1] if mentions else None,
        raw=raw.strip()[:500],
    )


def parse_concise_blob(blob: str) -> list[ReshipRecord]:
    """Parse the slack_read_channel concise dump (records separated by leading ': ').

    Production wiring will pass real Slack API message dicts to parse_messages();
    this helper lets the prototype run on captured fixtures verbatim.
    """
    # split into records: each starts at a line beginning with ': '
    records: list[ReshipRecord] = []
    chunks = re.split(r"\n(?=: )", blob)
    for c in chunks:
        c = c.strip()
        if not c.startswith(":"):
            continue
        rec = parse_record(c[1:].strip())
        if rec:
            records.append(rec)
    return records


def epoch_to_iso(ts: str | float, tz_name: str = "America/Chicago") -> Optional[str]:
    """Slack 'ts' epoch seconds -> 'YYYY-MM-DD HH:MM:SS' in the report tz (CST/CDT)."""
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ZoneInfo(tz_name))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def parse_detailed_blob(blob: str) -> list[ReshipRecord]:
    """Parse the slack_read_channel DETAILED dump verbatim (zero transcription).

    Each record is a `=== Message at ... ===` block whose `Message TS:` line gives
    the authoritative epoch receipt time — exactly what the weekly window filters on.
    This is the canonical no-token path: paste the plugin output to a file, run.
    """
    records: list[ReshipRecord] = []
    blocks = re.split(r"^=== Message at .*? ===\s*$", blob, flags=re.MULTILINE)
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        m = re.search(r"Message TS:\s*([\d.]+)", b)
        ts_iso = epoch_to_iso(m.group(1)) if m else None
        body = re.sub(r"^Message TS:.*$", "", b, flags=re.MULTILINE).strip()
        rec = parse_record(body, created_ts=ts_iso)
        if rec:
            records.append(rec)
    return records


def parse_messages(messages: list[dict]) -> list[ReshipRecord]:
    """Production entrypoint: list of Slack API message dicts ({text, ts, user}).

    Stamps receipt time from the message's own `ts` (epoch) so the weekly window
    filters on true receipt date, not whatever is in the text body.
    """
    out: list[ReshipRecord] = []
    for m in messages:
        ts_iso = epoch_to_iso(m["ts"]) if m.get("ts") else None
        rec = parse_record(m.get("text", ""), created_ts=ts_iso)
        if rec:
            out.append(rec)
    return out


if __name__ == "__main__":  # quick smoke
    import json, sys
    blob = sys.stdin.read()
    recs = parse_concise_blob(blob)
    print(json.dumps([asdict(r) for r in recs], indent=1, ensure_ascii=False))
