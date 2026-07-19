"""Generate the Gorgias-classifier spec MD for Demi.

Pulls the Slack-derived gold set from slack_tickets (loaded by sync.py), checks
each against the feedback table (was it ever categorized in Gorgias?), and emits
a hand-off doc: target taxonomy + phrase->label rules + worked examples + this
week's labeled tickets as a test set.

Read-only on feedback/fulfillments; reads slack_tickets. Writes one .md.
"""
from __future__ import annotations
import sqlite3, os, sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DB = Path(os.environ.get("APPYHOUR_DB_PATH") or (r"C:\AppyHourData\shipping.db" if os.path.exists(r"C:\AppyHourData\shipping.db") else str(Path(os.environ["APPDATA"]) / "AppyHour" / "shipping.db")))
OUT = Path(r"C:\Users\Work\Claude Projects\_outputs\reports\2026-06-26-gorgias-classifier-spec-for-demi.md")

# ---- canonical target taxonomy (the Gorgias Contact Reason values) ----------
TAXONOMY = [
    ("Shipping::Delayed in transit", "Carrier scanned but box is past expected delivery; still moving.", "shipping"),
    ("Shipping::Damaged in transit::Arrived Warm", "Box arrived warm / not cold; ice melted on arrival.", "shipping"),
    ("Shipping::Damaged in transit::Broken/Leaking Ice Pack", "Gel/ice pack leaked, broke, or exploded in the box.", "shipping"),
    ("Shipping::Damaged in transit::Box damaged", "Crushed/torn/damaged outer box.", "shipping"),
    ("Shipping::Lost in Transit/Misdelivered::Lost", "Tracking dead / never delivered / lost by carrier.", "shipping"),
    ("Shipping::Lost in Transit/Misdelivered::Misdelivered", "Delivered to wrong address / wrong person.", "shipping"),
    ("Shipping::Cannot be delivered", "Carrier says undeliverable (bad address, access, returned).", "shipping"),
    ("Order::Missing item", "One or more items missing from the box.", "fulfillment"),
    ("Order::Wrong item", "Wrong item packed / substitution error.", "fulfillment"),
]

# ---- phrase -> label rules (ordered; first match wins) ----------------------
RULES = [
    ("melted ice / ice pack melted",                 "Shipping::Damaged in transit::Arrived Warm"),
    ("ice pack OR gel pack + leak/broke/exploded",   "Shipping::Damaged in transit::Broken/Leaking Ice Pack"),
    ("arrived warm / arrive warm / 'warm'",          "Shipping::Damaged in transit::Arrived Warm"),
    ("lost in transit / 'lost'",                     "Shipping::Lost in Transit/Misdelivered::Lost"),
    ("misdelivered / mis-delivered",                 "Shipping::Lost in Transit/Misdelivered::Misdelivered"),
    ("cannot be delivered / undeliverable",          "Shipping::Cannot be delivered"),
    ("delay in transit / delayed / 'delay'",         "Shipping::Delayed in transit"),
    ("damage box / box damaged",                     "Shipping::Damaged in transit::Box damaged"),
    ("missing N items / missing <type>",             "Order::Missing item"),
    ("wrong order / wrong item",                     "Order::Wrong item"),
]

# ---- worked examples (real CS phrasings seen in #reship-and-order-requests) --
EXAMPLES = [
    ("Delay in transit",                                  "Shipping::Delayed in transit"),
    ("Delayed in transit; Gourmet Bites",                 "Shipping::Delayed in transit"),
    ("Delayed",                                           "Shipping::Delayed in transit"),
    ("Arrived warm",                                      "Shipping::Damaged in transit::Arrived Warm"),
    ("Arrive warm (Gourmet Bites)",                       "Shipping::Damaged in transit::Arrived Warm"),
    ("arrive warm",                                       "Shipping::Damaged in transit::Arrived Warm"),
    ("melted ice packs",                                  "Shipping::Damaged in transit::Arrived Warm"),
    ("gel pack leak",                                     "Shipping::Damaged in transit::Broken/Leaking Ice Pack"),
    ("Ice pack leak (broke)",                             "Shipping::Damaged in transit::Broken/Leaking Ice Pack"),
    ("ice pack leaked (exploded accdg to customer)",      "Shipping::Damaged in transit::Broken/Leaking Ice Pack"),
    ("Damage box",                                        "Shipping::Damaged in transit::Box damaged"),
    ("Lost in transit",                                   "Shipping::Lost in Transit/Misdelivered::Lost"),
    ("Misdelivered",                                      "Shipping::Lost in Transit/Misdelivered::Misdelivered"),
    ("Cannot be delivered; one-time reship?",             "Shipping::Cannot be delivered"),
    ("Missing 2 items from limited release box",          "Order::Missing item"),
    ("wrong order, customer insisted she only ordered 3", "Order::Wrong item"),
]

# ---- posts that must NOT be categorized as failures (ops requests) ----------
EXCLUDE = [
    "pls ship this week / please ship this week",
    "Can we ship this through FedEx ... ? (reship request)",
    "can we change the jam to an accompaniment",
    "is this going to arrive on time? / can we guarantee Wednesday?",
    "would rerouting be an option here?",
    "customer has allergies ... change Tapas/Dutch Golden",
]


def gold_rows(con: sqlite3.Connection):
    rows = con.execute(
        "SELECT order_number, gorgias_id, issue, team, carrier, dest_state, ship_week, created_ts, raw "
        "FROM slack_tickets ORDER BY issue, carrier"
    ).fetchall()
    out = []
    for onum, gid, issue, team, carrier, st, sw, ts, raw in rows:
        # was this order ever categorized as a SHIPPING issue in feedback?
        hit = con.execute(
            "SELECT issue_type FROM feedback WHERE order_number=? AND issue_type LIKE 'Shipping%' LIMIT 1",
            (str(onum),),
        ).fetchone()
        out.append(dict(order=onum, gid=gid, issue=issue, team=team, carrier=carrier,
                        state=st, ship_week=sw, ts=ts, in_gorgias=bool(hit),
                        gorgias_had=hit[0] if hit else ""))
    return out


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = gold_rows(con)
    con.close()
    shipping = [r for r in rows if r["team"] == "shipping"]
    missed = [r for r in shipping if not r["in_gorgias"]]

    L = []
    w = L.append
    w("# Gorgias Shipping-Ticket Classifier — Spec & Gold Set")
    w("")
    w("**For:** Demi (CS automation) · **From:** Kurt · **Date:** 2026-06-26")
    w("")
    w("## Why this exists")
    w("")
    w("Shipping tickets are landing in Gorgias **uncategorized**: the Contact Reason / "
      "custom field is empty and the only tag is `auto-reply`. Verified 2026-06-26 on live "
      "tickets `278775358` and `278667512` → `tags:['auto-reply']`, `custom_fields:{}`.")
    w("")
    w("Because the weekly shipping report classifies off that Gorgias field, **~50% of real "
      "shipping issues never get counted** (last full week: 58 real vs 29 categorized). CS reliably "
      "types the diagnosis into the `#reship-and-order-requests` Slack post, but that never makes it "
      "onto the structured Gorgias field your automation is supposed to set.")
    w("")
    w("**What I need:** every shipping ticket auto-assigned the correct Contact Reason below. "
      "This doc gives you the target labels, the rules, worked examples, and a labeled test set. "
      "How you build/fix the classifier is yours — I just need my tickets correctly tagged.")
    w("")
    w("## 1. Target taxonomy (the only valid Contact Reason values)")
    w("")
    w("| Contact Reason | When to use | Team |")
    w("|---|---|---|")
    for label, desc, team in TAXONOMY:
        w(f"| `{label}` | {desc} | {team} |")
    w("")
    w("## 2. Classification rules (ordered — first match wins)")
    w("")
    w("Match case-insensitively against the CS note text. Most-specific first so e.g. "
      "*melted ice* and *ice pack leak* resolve before the generic *warm*.")
    w("")
    w("| # | If the note matches… | Assign |")
    w("|---|---|---|")
    for i, (cond, label) in enumerate(RULES, 1):
        w(f"| {i} | {cond} | `{label}` |")
    w("")
    w("## 3. Worked examples (real CS phrasings → correct label)")
    w("")
    w("| CS note (verbatim) | Correct Contact Reason |")
    w("|---|---|")
    for phrase, label in EXAMPLES:
        w(f"| {phrase} | `{label}` |")
    w("")
    w("## 4. Do NOT classify these as shipping failures (ops requests)")
    w("")
    w("These are routing/change requests, not delivery failures — leave out of shipping reasons:")
    w("")
    for e in EXCLUDE:
        w(f"- {e}")
    w("")
    w("## 5. Gold / test set — this ship week (`_SHIP_2026-06-22` window)")
    w("")
    w(f"{len(shipping)} shipping tickets surfaced in Slack. "
      f"**{len(missed)} of them are currently NOT categorized in Gorgias** "
      f"(`in_gorgias = NO`) — these are exactly what your classifier must catch. "
      "Carrier/state are the truth from our fulfillment join (for your validation, not input).")
    w("")
    w("| Order# | Gorgias ID | CS note → | Correct Contact Reason | Carrier | State | In Gorgias now? |")
    w("|---|---|---|---|---|---|---|")
    import re as _re
    con2 = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    raws = {r[0]: r[1] for r in con2.execute("SELECT gorgias_id, raw FROM slack_tickets").fetchall()}
    con2.close()
    def snippet(gid):
        t = raws.get(gid, "") or ""
        t = _re.sub(r"<[^>]+>|https?://\S+|\[20\d\d.*?\]", "", t)  # strip links/mentions/ts
        t = _re.sub(r"\s+", " ", t).strip(" -:")
        return t[:60]
    for r in shipping:
        w(f"| {r['order']} | {r['gid']} | {snippet(r['gid'])} | `{r['issue']}` | {r['carrier']} | {r['state'] or ''} | "
          f"{'✅ yes' if r['in_gorgias'] else '❌ NO'} |")
    w("")
    w("### Just the misses (your worklist)")
    w("")
    w("| Order# | Gorgias ID | Should be | Carrier |")
    w("|---|---|---|---|")
    for r in missed:
        w(f"| {r['order']} | {r['gid']} | `{r['issue']}` | {r['carrier']} |")
    w("")
    w(f"_Coverage right now: {len(shipping)-len(missed)}/{len(shipping)} "
      f"({round(100*(len(shipping)-len(missed))/max(len(shipping),1))}%). Target: 100%._")
    w("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"shipping tickets: {len(shipping)} | missed in Gorgias: {len(missed)}")


if __name__ == "__main__":
    main()
