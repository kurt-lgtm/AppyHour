# RESHIP_REPORT_RULES.md — Reship Tracking Report (SSOT)

**Single source of truth for the durable reship report — change rules HERE first.**
🔴 **PRE-CHANGE GATE:** read this doc before ANY change to the report, its refresh script, or its sheet. Code that contradicts a rule here does not ship without updating this doc in the same commit.

> **STATUS: APPROVED by Kurt 2026-07-09** (North Star confirmed; denominator = live Shopify tag count excl. cancelled).

## 🧭 NORTH STAR (draft — Kurt to confirm wording; Kurt-only to edit after)

Dan (or anyone) can answer **"for boxes that shipped this week, are we doing better on reships than last week?"** by opening one sheet — without asking anyone, without eyeballing Shopify, and without a number panic. Every figure is self-explaining: its source, its denominator, and how mature it is. (From Dan, Slack C0A6185SY0Z 2026-07-09: "How can I check on this without asking" / "How often is it refreshed".)

## What it is

Google Sheet `1JgyYknIxJ3-UJxJOX-y78rf8cPNhT0uPy5FUw2zO9wE` ("Reship Sheet").
One tab per ship week (`_SHIP_<Monday>`), refreshed daily by a scheduled task; a Summary tab with the running per-cohort rate; a Flags tab for Dan-owned decisions.

**Inputs:** Gorgias tickets (bodies, not tags), Slack `#reship-and-order-requests` (corroboration), Shopify orders (GraphQL, live), `shipping.db` fulfillments (cohort tags, read-only via `connect_ro()`).
**Output:** sheet tabs above + Slack DM to Kurt ONLY on parameter breach (anomaly-first, no daily noise).

## 🔴 Counting rules — the failures these prevent, negatives first

1. **NEVER count Gorgias tags as reships.** Rule 81603 keyword-spams `Reship req` onto cancels, ads, reviews, skip requests — 34 tagged vs ~5 real on 7/06–08 → phantom CEO panic. Tag counts may not appear anywhere in the report. ([[gorgias-tag-counts-invalid-rule-81603]])
2. **NEVER classify from subjects or fields.** Subject triage misclassified 4 of 7 candidates on 7/08 (missed moldy-meat + melted; false-flagged tracking-inquiry + gift question); issue-type custom fields sit EMPTY. A ticket counts as a reship request only after its **body** confirms it (mispick audits: 45% subject-triage gap).
3. **A reship attributes to the ORIGINAL order's ship cohort, never the reship order's tags or entry date.** Original = customer's most recent prior `_SHIP_`-tagged order, confirmed by ticket where possible. The 7/06–08 "surge" was 64 orders remediating the 06-29 cohort — zero from 07-06.
4. **NEVER conflate requested date (ticket created) with entered date (Shopify order created).** Dan froze entry 7/05, batch-entry 7/06–08 made a 5-day backlog read as a 3-day surge — the exact panic this report exists to kill. Both dates appear side by side; week-over-week comparisons use REQUESTED date only.
5. **Shopify order sweeps must use `status:open`** (or exclude cancelled/closed explicitly). `fulfillment_status:unfulfilled` alone matches cancelled 2023 ghosts — read 104 when the true open queue was 69 (7/08).
6. **Count orders (deduped IDs), never tag matches** — every reship order carries ≥2 `Reship*` tags; tag-match doubles the count. State it in the panel: "N orders".
7. **Denominator = full cohort size EXCLUDING reship orders, stated in the tab.** Rates only, never raw counts ([[feedback_cs_metrics_normalize]]). **Source = LIVE Shopify tag count** (`tag:'_SHIP_<Mon>' -status:cancelled -tag:'Reship'`, GraphQL, snapshotted with timestamp each refresh — Kurt 2026-07-09; reship exclusion Dan 2026-07-09: replacements carry the outbound cohort tag and would inflate its denominator). NOT the local `fulfillments` table (sync can die silently — the 7/07 dead-cadence class) and NOT RMFG email counts (weekly IMAP; kept as reconciliation cross-check only). Never derived silently ([[feedback_ask_cohort_count]]).
8. **Week-over-week comparison is same-day-offset or maturity-adjusted, never raw partial vs final.** A cohort's requests keep arriving for ~7+ days post-ship ("assumed tail %"): show `day-N vs last week's day-N` AND `projected final = to-date ÷ historical CDF(day N)`. Comparing day-2 this week to day-7 last week fabricates improvement.
9. **Tail curve is seasonal.** Fit the request-lag CDF from summer cohorts for summer weeks; never blend across the Apr/Nov boundary ([[seasonal-coldchain-baselines]]).
10. **Slack channel = corroboration, NOT source of record.** Posting discipline varies and entry can be frozen; join Gorgias by customer email; reconcile counts in a footer: `Gorgias N / Slack M / Shopify orders K`.
11. **Every number carries provenance** — source query + refresh timestamp on each tab. Unresolvable rows → `UNKNOWN — needs manual check`, never estimated or dropped silently.
12. **Warm ≠ routing in the issue table.** Arrived Warm/Burst = packaging bucket; Delayed/3+Days = routing bucket; never merged ([[gorgias-warm-delay-schema]]).
13. **Drop pre-cache 5-digit order#s to a "stale" bucket** — they're 2024-25 orders with re-stamped Gorgias dates; they pollute current-week counts ([[feedback_5digit_orders_are_old_shopify]]).

14. **Raw Data ownership split (Kurt 2026-07-09):** cols A–I = script-owned (rewritten hourly, hand
    edits there are CLOBBERED); cols **J–M = user-owned** (`Override Issue/Incoming/Outgoing`,
    `Exclude`='x') — the script preserves them across refreshes and applies them to EVERY computed
    tab (Pivots, RS, Summary, Flags) so overridden numbers never diverge (the 124-vs-130 class).
    Pivots are live QUERY formulas over the effective columns N–P — a Raw Data edit recomputes them
    instantly. Overrides are corrections of record, not the place to hide bad weeks.

## Refresh & write discipline

**Owner = Google Apps Script bound to the Reship Sheet** (respec 2026-07-09 — cloud-side so
refreshes don't depend on Kurt's machine being awake; the local Python runner becomes the
backfill/debug tool and its 12:18 schtask is DISABLED once the Apps Script trigger is live).

### Apps Script port spec

- **Trigger:** time-driven, HOURLY (Dan 2026-07-09). Enrichment stays incremental so most
  hourly runs are cheap (denominator counts + new-order sweep only); the tail-CDF and pivots
  recompute from state each run.
- **HTTP:** all three APIs via `UrlFetchApp` — Shopify GraphQL (`X-Shopify-Access-Token`),
  Gorgias REST (Basic auth + custom `User-Agent` header — Cloudflare 1010 blocks default UA),
  Slack incoming webhook (breach alerts) or `chat.postMessage` w/ bot token for DMs.
- **Secrets in Script Properties ONLY** (`SHOPIFY_TOKEN`, `GORGIAS_USER/KEY`, `SLACK_WEBHOOK`) —
  never in code, never in a sheet cell. Note: whoever can edit the script can read them; keep the
  script owned by Kurt's account, editors = Kurt only.
- **State = hidden `_state` tab** (one row per reship order: entered/requested/ticket/original/
  original_cohort/total), NOT PropertiesService (9KB/prop limit). Same idempotent
  full-tab-rewrite semantics as the Python runner.
- **6-minute execution cap:** enrichment (original-order + Gorgias joins) is incremental — only
  rows missing fields, oldest first, hard-capped ~150 lookups/run with a continuation flag; a
  backlogged first run catches up over successive days (or seed `_state` from
  `_outputs/cache/reship_report_state.json` once).
- **Same counting rules (R1–R13) apply verbatim** — the port changes the host, not one rule.
  The ship-Monday-precedes-complaint attribution guard MUST be ported (misattribution bug,
  2026-07-08).
- **Fail loud:** wrap main in try/catch → Slack webhook `[CRITICAL] reship report failed: …`;
  Apps Script's own failure emails stay on as backup. Freshness cell `Summary!A1` timestamp
  remains the reader-side assert; local `freshness_sweep.py` keeps watching the state cache
  mtime ONLY until cutover, then switches to reading `Summary!A1` via the Sheets API.
- **Cutover checklist:** (1) seed `_state`, (2) dry-run menu item writes to a `TEST_` tab,
  (3) enable trigger, (4) disable local schtask `reship-report-refresh`, (5) point
  freshness sweep at `Summary!A1`.

### Local runner (fallback/backfill)

- `ShippingReports/reship_report_refresh.py` — kept for backfills and as the reference
  implementation; **CLI, never MCP tools in scheduled runs** (silent no-op risk). Manual re-run
  must be idempotent (full tab rewrite, no append-dup).
- **shipping.db is READ-ONLY** (`appyhour_lib.db.connect_ro()`, never raw `sqlite3.connect`, never a writer — WAL corruption 6/27 + 7/01).
- **Writer-ownership gate:** the refresh task is not "shipped" until (a) schtask owner exists and (b) the sheet gets a freshness cell that a reader can assert on + coverage in `freshness_sweep.py`. A silently-dead refresh must fail loud (dead-cadence class: shopify_orders sync, feedback sync — both went stale unnoticed).
- **Gorgias paced ≤~0.8 req/s** (reuse `_gorgias_get`); Shopify GraphQL nested page sizes ≤50.
- Sheet writes via appyhour MCP `sheets_write` interactively; the scheduled path uses the Sheets CLI/service-account wrapper.

## Pivots tab (Dan 2026-07-09 — mirrors the 7/08 xlsx deliverable)

Four blocks over the swept window, unit = deduped reship orders:
1. **Reship Created** — count by Shopify entry date
2. **Reship Requested** — count by Slack/Gorgias ticket date (`(blank)` = UNKNOWN, never estimated)
3. **Reship Outgoing ship week** — count by the replacement's `_SHIP_` tag
4. **Reship Incoming ship week** — count by ORIGINAL order's cohort + running reship rate per
   ship week (rate vs that cohort's reship-excluded denominator, R7)

## Flags tab (Dan-owned decisions, from 7/09 policy discussion — pending Dan/Jessa confirmation)

- Reship where original order value > $150 → NOT CS-approvable; route to Dan (his 7/07 process post).
- Request > 14 days after delivery → flag (policy: likely no reship; Wildgrain-style deadline TBD).
- Subscriber already cancelled AND < 3 boxes lifetime → flag (Dan: no reship).
- Full-reship reasons: lost / misdelivered / gel burst. Warm/delayed → partial (Jessa 7/09). Crackers/jams/nuts excluded from reships unless damaged (all-summer 3×48oz config).
- #160051-class rows (no ticket, paid total, stale tag) → `UNKNOWN`, listed until resolved.

## Non-goals

- Not a refund tracker (refund requests ≠ reship issues — [[feedback_refund_not_issue]]).
- Not the weekly shipping issue report (that stays per `~/.knowledge/ops/Weekly Shipping Issue Report.md`); the issue table here reuses its format, not its scope.
- No auto-tag cleanup in Gorgias (Demi's work item); no writes to Shopify/Gorgias ever.

## Change log

- 2026-07-09 — initial draft (Claude, from Kurt/Dan/Jessa Slack thread C0A6185SY0Z + 7/08 session findings). Awaiting Kurt approval.
