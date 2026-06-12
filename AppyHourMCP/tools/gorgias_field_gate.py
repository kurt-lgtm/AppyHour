"""
GAP-01: Gorgias field-completion gate.

Scans recent tickets for operational issues (gate tags / subject keywords)
that were closed WITHOUT Contact Reason (58260) or Resolution (13284) set.
Those tickets are invisible to the ops sync (94% skip rate, $1.5-2.5k/wk
unreported reship cost per gorgias-gap-analysis.md 2026-04-04).

Outputs a markdown digest (capture rate + violation table) for the daily
scheduled task to post to CS. With --tag, also applies a `missing-fields`
tag in Gorgias so agents can filter their own cleanup queue.

Usage:
    python gorgias_field_gate.py [--days 3] [--tag] [--out PATH]

Read-only by default. ASCII-only stdout (cp1252-safe).
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

_MCP_DIR = Path(__file__).resolve().parent.parent
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

from tools._gorgias_internal import get_auth  # noqa: E402
from tools.gorgias_sheets_sync import (  # noqa: E402
    FIELD_CATEGORY,
    FIELD_RESOLUTION,
    KEYWORD_ISSUE_MAP,
    OPERATIONAL_GATE_TAGS,
    _gorgias_get,
)

GATE_TAG = "missing-fields"
DEFAULT_OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\reports")

_SUBJECT_KEYWORDS = tuple(kw for kws, _ in KEYWORD_ISSUE_MAP for kw in kws)


def _tag_names(ticket: dict) -> list[str]:
    raw = ticket.get("tags", [])
    if not isinstance(raw, list):
        return []
    return [(t.get("name", "") if isinstance(t, dict) else str(t)).lower() for t in raw]


# Tickets that are never operational issues, regardless of keywords.
_NOISE_TAGS = frozenset({"spam", "auto-reply"})
_NOISE_SUBJECT_HINTS = (
    "cancelled subscriptio",
    "unsubscribe",
    "product reviews -",
    "automatic reply",
    "out of office",
    "new review",
    "$",  # promo blasts ("$45 off the Father's Day box") — customers don't put $ in subjects
    "last few hours",
)


def _is_noise(ticket: dict, tag_names: list[str]) -> bool:
    if any(t in _NOISE_TAGS for t in tag_names):
        return True
    subject = (ticket.get("subject") or "").lower()
    return any(h in subject for h in _NOISE_SUBJECT_HINTS)


def _is_operational(ticket: dict, tag_names: list[str]) -> bool:
    """Gate-tag match (same gate the sync uses) or subject keyword hit."""
    if any(gt in tag_names for gt in OPERATIONAL_GATE_TAGS):
        return True
    if any(t.startswith(("reship -", "reship-")) for t in tag_names):
        return True
    subject = (ticket.get("subject") or "").lower()
    return any(kw in subject for kw in _SUBJECT_KEYWORDS)


def _body_is_operational(ticket_id: int, auth: tuple, base_url: str) -> str:
    """Keyword-scan the first customer message body.

    Contact-form tickets carry generic subjects ('Help with my order'),
    so the operational signal only exists in the body. Returns the first
    matched keyword, or '' if clean. One extra API call per candidate —
    only invoked for tickets already missing fields.
    """
    try:
        resp = _gorgias_get(
            f"{base_url}/tickets/{ticket_id}/messages",
            auth=auth,
            params={"limit": 5, "order_by": "created_datetime:asc"},
        )
        resp.raise_for_status()
        for m in resp.json().get("data", []):
            if (m.get("from_agent") is True) or (m.get("sender") or {}).get("id") is None:
                continue
            body = ((m.get("body_text") or m.get("stripped_text") or "")).lower()[:4000]
            for kw in _SUBJECT_KEYWORDS:
                if kw in body:
                    return kw
            return ""  # first customer message only
    except Exception:  # noqa: BLE001 — scan is best-effort, never fatal
        return ""
    return ""


def _apply_gate_tag(ticket_id: int, auth: tuple, base_url: str) -> bool:
    """Add GATE_TAG to a ticket, preserving existing tags."""
    import requests

    try:
        resp = requests.post(
            f"{base_url}/tickets/{ticket_id}/tags",
            auth=auth,
            json={"names": [GATE_TAG]},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return True
        print(f"  tag failed for {ticket_id}: HTTP {resp.status_code}")
        return False
    except requests.RequestException as e:
        print(f"  tag failed for {ticket_id}: {e}")
        return False


def scan(days_back: int) -> dict:
    auth, base_url = get_auth()
    since_dt = datetime.now() - timedelta(days=days_back)

    operational_total = 0
    complete = 0
    violations: list[dict] = []  # closed + missing field(s)
    pending: list[dict] = []  # still open + missing field(s)
    cursor = None
    done = False

    for _ in range(200):
        params = {"limit": 50, "order_by": "created_datetime:desc"}
        if cursor:
            params["cursor"] = cursor
        resp = _gorgias_get(f"{base_url}/tickets", auth=auth, params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            break

        for t in items:
            created_str = t.get("created_datetime", "") or ""
            try:
                ticket_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                ticket_dt = datetime.now()
            if ticket_dt < since_dt:
                done = True
                break
            if t.get("deleted_datetime"):
                continue

            tag_names = _tag_names(t)
            if _is_noise(t, tag_names):
                continue

            cf = t.get("custom_fields", {}) or {}
            has_reason = bool(cf.get(FIELD_CATEGORY, {}).get("value", ""))
            has_resolution = bool(cf.get(FIELD_RESOLUTION, {}).get("value", ""))

            operational = _is_operational(t, tag_names)
            matched_kw = ""
            if not operational and not (has_reason and has_resolution):
                # Generic subject + missing fields: the invisible cohort.
                # Body scan is the only signal left.
                matched_kw = _body_is_operational(t.get("id"), auth, base_url)
                operational = bool(matched_kw)
            if not operational:
                continue
            operational_total += 1

            if has_reason and has_resolution:
                complete += 1
                continue

            missing = []
            if not has_reason:
                missing.append("Contact Reason")
            if not has_resolution:
                missing.append("Resolution")
            assignee = ((t.get("assignee_user") or {}).get("email")) or "unassigned"
            entry = {
                "id": t.get("id"),
                "subject": (t.get("subject") or "(no subject)")[:70],
                "created": created_str[:10],
                "status": t.get("status", ""),
                "missing": " + ".join(missing),
                "assignee": assignee,
                "signal": matched_kw or "tag/subject",
                "already_tagged": GATE_TAG in tag_names,
            }
            if t.get("status") == "closed":
                violations.append(entry)
            else:
                pending.append(entry)

        if done:
            break
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    return {
        "since": since_dt.strftime("%Y-%m-%d"),
        "operational_total": operational_total,
        "complete": complete,
        "violations": violations,
        "pending": pending,
    }


def write_digest(result: dict, out_path: Path, subdomain: str) -> None:
    total = result["operational_total"]
    rate = (100.0 * result["complete"] / total) if total else 100.0
    lines = [
        f"# Gorgias Field Gate — {datetime.now():%Y-%m-%d}",
        "",
        f"Window: since {result['since']} | Operational tickets: {total} | "
        f"Fields complete: {result['complete']} | **Capture rate: {rate:.0f}%** (target 90%+)",
        "",
    ]
    for title, key in (("Closed with missing fields (violations)", "violations"),
                       ("Still open, fields not yet set (heads-up)", "pending")):
        rows = result[key]
        lines.append(f"## {title} — {len(rows)}")
        lines.append("")
        if rows:
            lines.append("| Ticket | Created | Missing | Signal | Assignee | Subject |")
            lines.append("|---|---|---|---|---|---|")
            for v in rows:
                link = f"https://{subdomain}.gorgias.com/app/ticket/{v['id']}"
                lines.append(
                    f"| [{v['id']}]({link}) | {v['created']} | {v['missing']} | {v.get('signal', '')} "
                    f"| {v['assignee']} | {v['subject']} |"
                )
        else:
            lines.append("_None — clean._")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="GAP-01 Gorgias field-completion gate")
    ap.add_argument("--days", type=int, default=3, help="lookback window (default 3)")
    ap.add_argument("--tag", action="store_true", help=f"apply '{GATE_TAG}' tag to closed violations")
    ap.add_argument("--out", type=str, default="", help="digest output path (.md)")
    args = ap.parse_args()

    result = scan(args.days)

    auth, base_url = get_auth()
    subdomain = "appyhour"
    if args.tag:
        tagged = 0
        for v in result["violations"]:
            if not v["already_tagged"] and _apply_gate_tag(v["id"], auth, base_url):
                tagged += 1
        print(f"Tagged {tagged} ticket(s) with '{GATE_TAG}'")

    out_path = Path(args.out) if args.out else (
        DEFAULT_OUT / f"gorgias-field-gate-{datetime.now():%Y-%m-%d}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_digest(result, out_path, subdomain)

    total = result["operational_total"]
    rate = (100.0 * result["complete"] / total) if total else 100.0
    print(f"Operational: {total} | Complete: {result['complete']} | Rate: {rate:.0f}%")
    print(f"Violations (closed): {len(result['violations'])} | Pending (open): {len(result['pending'])}")
    print(f"Digest: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
