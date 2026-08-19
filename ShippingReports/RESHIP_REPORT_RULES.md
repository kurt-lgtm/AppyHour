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

## Definitions (Kurt)

- **Lane = (carrier, origin hub, dest zip3)** — NOT carrier-alone, NOT a state, NOT zip5. When Kurt says
  "lanes," group by origin **hub + carrier + destination zip3** together (e.g. `OnTrac @ Anaheim → 950`),
  because the same carrier performs very differently out of different hubs AND across zip3 regions. A "bad
  lane" = that (carrier, hub, zip3) misses the 2-day SLA; the same carrier can be fine on another hub or
  zip3. State-only or carrier-only cuts hide the signal; zip5-exact over-fragments (tiny n).
- **TNT data — be specific about source + granularity** (this is how the engine determines lanes; match it):
  - **Historical carrier-hub trust** = the actuals late-rate layer that decides whether a regional carrier
    is safe → keyed **(carrier, hub, dest zip3)** — `ShipRouting/lib/engine.py:856`
    (`actuals.get((carrier, hub, zip[:3]))`). This is the grain for any "was this lane bad historically" cut.
  - **Forward TNT quote** (FedEx/UPS estimate from ShipEngine) → keyed **zip5** — `lib/carrier_tnt.py:60`
    (`cached_tnt(origin_zip5, dest_zip5)`, cache `{origin_zip5}>{dest_zip5}`, FedEx+UPS only, NOT OnTrac/Veho).
  - The real origin hub for a shipped box comes from carrier invoices (`shipments.hub`), not
    `delivery_status` (its `origin_hub` is usually blank); invoices lag ~1 week, so a just-shipped cohort
    can't be lane-cut at hub grain until its invoices land.
  - A FedEx pin is NOT always a TNT decision — a max-ice box (`!ExtraGel…!`) to a hot dest can be pinned
    FedEx for THERMAL/air reasons even when the ground (carrier,hub,zip3) lane is a proven 1-day. Don't read
    every `!ANY FedEx` tag as "the ground lane was too slow."

## ✅ Current shipped state (2026-07-27) — canonical sheet, tabs, tool menu, overrides

🔴 **The report you actually touch is the PIVOT sheet `1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU`**
(the old `1JgyYknIxJ3-…` "Reship Sheet" above is RETIRED — engine no longer writes it). Bound Apps
Script project **"Running Reship"**, scriptId `15K0MrUssFqacWybQAToz6CeHTouRU4IeNY4-DzZ4NeE1rBCCNGpGjAjv`,
hourly time-trigger on Kurt's account. Deploy = REST `updateContent` (or `clasp push -f`); source of
truth = `ShippingReports/appsscript/Code.gs`. Local `scratchpad/rebuild_mix_triage.py` = the immediate
manual mirror (reads `shipments.db` for carrier/transit); GAS is authoritative, the two stay in PARITY.

**Tabs (all self-maintaining):**
- **Raw Data** — walk-forward append-only ledger (R17), the entered reships. Issue in col D (SHORT label).
- **Triage** — Slack posts with NO reship entered yet (candidates). Cols: Key·Posted·Issue·Order·Ship
  Week·Box Type·Gorgias·Decision, + a by-ship-week "Unresolved" summary table to the right. Issue is
  CANONICAL (`Shipping::…`). A row with any Decision typed is dropped + remembered in hidden
  `_triage_decisions` so it doesn't reappear.
- **Product Mix** — per cohort: size, and per box type (Regular/Medium Tray/Large Tray) Reship count/%
  (COUNTIFS over Raw Data) + Unresolved count/% (COUNTIFS over Triage). Then **Potential** (=all Reship
  + all Unresolved) and **Actual** (=reships only), both %-over-cohort-size. **All % cells are
  TEXT-formatted strings** (`"1.70%"`) so new cohort columns render without manual formatting.
- **Reship** (renamed from `Product Mix (T)` by Dan 2026-08-12 — the ONLY write target; tab name lives in `PM_T_TAB`) — transpose (metrics as rows, cohorts as columns; A1 = "Ship Week"). Below the
  metrics, grouped breakdowns **By Issue · By Carrier · Arrived Warm by State · Delayed by State**, each
  emitted TWICE: a `── … (%) ──` section (÷ cohort size) then a discrete-count section. Built AFTER
  writeTriage_ (with `flush()`) so its Unresolved/Potential reflect the current Triage, not last cycle.
- **Daily** — separate sheet `1VHzlyvFabVYUGpR71tgJfYDglI85KnCQOCYJFyZvGsI`.

**Tool menu (this is the "tool"):** `onOpen` builds a **Reship Report** custom menu on the sheet →
*Refresh now (full)* · *Refresh Product Mix + Reship* · *Refresh Triage only* · *Refresh Daily* ·
*Backfill Gorgias + enrich*. Each runs the matching GAS function; first click asks Kurt to authorize.

**🔴 Carrier = Parcel Panel, NOT Shopify's fulfillment `tracking_company`** (that mislabels
OnTrac↔LaserShip/Veho). GAS fetches it per order from the PP API
(`open.parcelwill.com/api/v2/tracking/order`, header `x-parcelpanel-api-key` from **Script Property
`PARCELPANEL_API_KEY`** — name has NO underscores inside PARCELPANEL; value = `.env`
`PARCEL_PANEL_API_KEY`). Local rebuild reads `delivery_status.carrier`. UrlFetchApp's UA is accepted;
Python-urllib's default UA is Cloudflare-403'd. Without the Script Property, GAS carrier = `Unknown`.

**🔴 Two classification overrides — post-classification at build time, `parse.py` text classifier
UNTOUCHED (it has no transit):**
1. **Expedite-request guard** (`c495842`): "can't delay / do not delay / delay it further / avoid
   delay / no further delay" is a REQUEST, not a `Delayed in transit` failure — do NOT let the bare
   word "delay" classify it. Real delays ("delayed in transit", "it was delayed 5 days") still count.
2. **Late supersedes warm** (`a20950c`): a reship whose ORIGINAL order was delivered in **>2 transit
   days** (Parcel Panel; `delivery_date − pickup_date`, delivered only) reclassifies `Arrived Warm` →
   `Delayed in transit` — the delay is root cause. Applied to Raw Data (short label) via
   `enrichTransitOverride_` (re-applied every run because sweepAndEnrich_ re-derives issue) and to
   Triage (canonical) in `writeTriage_`; the (T) breakdown auto-follows Raw Data. `transit_days`
   cached in `_state`. Threshold strictly `> 2` (2-day = on-time).

**Deploy gotcha:** clasp refresh token dies ~weekly with `invalid_rapt` (Workspace reauth policy, NOT
Testing mode — consent screen is Internal, do NOT "publish"). Fix = run
`ShippingReports/appsscript/clasp_login_py.py` (narrowed to script.projects scope to dodge the policy).
[[clasp-invalid-rapt-workspace-reauth]] · [[clasp-node24-premature-close]]

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

15. **Headless owner = the sheet-bound Apps Script `Code.gs`** (2026-07-13 port). Hourly trigger on
    Kurt's account, cloud-side (PC-off safe). The local `reship_report_refresh.py` is the
    reference/backfill only and its schtask is DISABLED at cutover — **two writers race and clobber**
    (never run both). Membership = **every reship whose ORIGINAL order is in the last WEEKS_BACK+1
    ship weeks** (full window; the "23-for-07-06" decision) — NOT the retired `_seed`/`CUTOVER`
    model. All tabs reconcile because they count this one set.
16. **Slack parser parity gate.** The Triage + requested-fill JS port of `ingest.slack_reship.parse`
    MUST match the Python parser exactly — verified 2026-07-13 on 200 live messages (110 classified,
    0 mismatches, `scratchpad/parser_parity.py`). Re-run parity if either parser's ISSUE_RULES change.
17. **Pivot Raw Data is WALK-FORWARD APPEND-ONLY, 9 columns (Kurt 2026-07-13, Option 2).** Columns:
    Order · Requested · Created · Issue · Incoming week · Outgoing week · Status · Original · Box
    Type. **Override/Eff/Exclude columns REMOVED** — the writer appends each row once and only
    BACKFILLS BLANK Requested/Created cells (never overwrites what Kurt typed), so dates are edited
    IN PLACE and stick; Status/issue/cohort/box/original are refreshed each run. Append is gated by
    `PIVOT_WATERMARK` (max order # ever processed — monotonic+unique, no date-granularity hole).
    **To remove a reship: DELETE its row** → order# <= watermark → never reconsidered → permanent
    (re-add the # to undo). **To take out of counts: also DELETE** (no more Exclude `x`; the Count
    tabs + Product Mix count ALL rows, no filter). **No auto-aging** — rows accumulate until Kurt
    prunes; WEEKS_BACK/mondays gate only the cohort-summary tabs. First GAS run adopts the current
    sheet (floor = highest order# present) — no 92-day flood. Full-tab wipe → DM heads-up, NOT
    auto-restored. REJECTED: hide list, delete-detection/suppressed-set, dynamic-window+rewrite,
    override/eff/exclude columns (all deemed too much bookkeeping for a walk-forward ledger).

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

## 🔴 Cohort-analytics tabs — TnT2 · Lost in Transit · Routing Match (Kurt 2026-08-06)

These three tabs live on the SAME pivot sheet but are **NOT reships** — they are **cohort-wide**
shipping analytics over the whole `_SHIP_` cohort (~2,500 orders/wk). Columns = ship weeks; rows =
metric groups (Total, then By Hub / Carrier / State / Box). Built originally by a LOCAL Python
builder (`build_dan_tabs.py` full rebuild of matured cohorts; `fill_E_0803.py` current-column fill
from a cwill CSV + Shopify). The target end-state is a **headless GAS refresh** reading delivery
telemetry from DO MySQL via Jdbc — NO local script, NO per-order ParcelPanel calls.

### 🔴 COLUMN-CREATION PROCEDURE — format → header → values → assert (Kurt 2026-08-13)

Applies to **TnT2 · Lost in Transit · Routing Match · Reship**. In this order, no other:

1. **Copy the PREVIOUS column's formatting.** `prev.copyTo(new, {formatOnly:true})` over the FULL
   column, plus `setColumnWidth` (copyTo does NOT carry width). Carries number formats (counts
   NUMBER `"0"`, rate rows PERCENT `"0.00%"`), bold/font, fills, borders, alignment, indent.
2. **Stamp the header** (`_SHIP_yyyy-mm-dd`), then `SpreadsheetApp.flush()`.
3. **Write values** (owned-row-only, per cell, keyed off the column-A label).
4. **Assert** — and assert again after the write.

**Negatives — the failures this ordering exists to prevent:**
- 🔴 **Header LAST = a self-replicating corruption.** `paCurrentCol_` used to append a column and
  return its index without stamping row 1. A headerless column is invisible to
  `headers.indexOf(shipWeek)`, so the NEXT run appended *another* one. TnT2 grew two headerless
  `_SHIP_2026-08-10` columns (F: 3+ Day 1,622 pre-fix · G: 3+ Day 0 post-fix, hub/carrier 3+ cells
  blank) and no assert could tell which was current. Fixed `fdc531a`; F:G deleted by hand.
- 🔴 **Format LAST = an unformatted orphan on any partial failure.** Formatting first means a crash
  between steps leaves a column that at least *looks* like its neighbours instead of a raw one a
  reader can't distinguish from scratch space.
- 🔴 **`formatOnly` is load-bearing** — a plain `copyTo` would clone the previous column's ~65 rate
  formulas as relative refs. Those are Kurt-owned; copy their appearance, never their contents.
- 🔴 **Never `insertSheet` a missing tab silently.** Dan renamed `Product Mix (T)` → `Reship`;
  `writeTabTo_` minted an empty `Product Mix (T)` and fed the ghost for days while Dan's tab froze.
  Creation is now loud (Slack). Tab name lives in `PM_T_TAB`, never inline.

**Asserts that THROW before any write** (named `PA_ASSERT_*`, so a failure is greppable):

| Name | Invariant |
|---|---|
| `PA_ASSERT_HEADERLESS_COLUMN` | every data column (2..lastCol) has a row-1 header |
| `PA_ASSERT_DUPLICATE_SHIP_TAG` | exactly ONE column per ship tag |
| `PA_ASSERT_TOTAL_PARTITION` | 2 Day + 3+ Day + pending == Total |
| `PA_ASSERT_NOTARRIVED_PARTITION` | lost + still-active == Not Arrived |
| `PA_ASSERT_OBSERVATION_PARTITION` | the three observation rows sum to Not Arrived |
| `PA_ASSERT_PENDING_SUBSET` | pending sits INSIDE still-moving, never beside it |

### 🔴 BLOCKER / precondition gate — do NOT wire until BOTH clear (verify each, don't assume)

1. **`shipments` + `delivery_status` in the cloud DB with a scheduled OWNER.** 🔴 Per the p7
   coordinator (2026-08-06) the DO MySQL currently holds **only `shopify_orders`** — the delivery
   telemetry (`delivery_status`/`shipments`) is **NOT in the cloud DB yet** (the matrix DDL +
   any one-time `etl_history --load` snapshot is not a maintained cloud owner). Until that telemetry
   migrates with a scheduled owner + freshness assert, the **transit/lost/hub half stays fully
   blocked** — a refresh reading it now would either fail or walk-forward on stale/absent data
   ("stale replica worse than absent", DATA_CANON:57). Coordinate with the ingest epic
   ([[ingests-off-kurts-pc-epic]]); do NOT create/alter their tables. Also note invoices
   lag ~1wk (line 34–35): a just-shipped cohort has NO `shipments.hub` yet → Routing Match "actual
   hub" is blank for the newest column until its invoices land. Show blank, never guess a hub.
2. **Jdbc reachability.** DO managed MySQL firewall only allows the droplet jump box today (direct
   connect from here times out). Apps Script Jdbc egresses from Google IP ranges → those ranges must
   be added to DO **trusted sources**, a **read-only** DB user used (`shipping_appyhour` exists,
   role=normal — confirm it's SELECT-only on those two tables), and its creds put in **Script
   Properties** (`MYSQL_HOST/PORT/DB/USER/PASS`), never in code. `Jdbc.getConnection("jdbc:mysql://
   host:25060/appyhourbox-shipping-db?useSSL=true", user, pass)`. Verify a trivial `SELECT 1` from
   GAS before building the query layer.

### 🟢 Headless mechanism — SHOPIFY is the delivery truth (coordinator 2026-08-06, proven)

The blocker is mostly LIFTED without waiting on the telemetry migration: **Shopify GraphQL fulfillment
events carry delivery status + timestamps**, and Apps Script already reaches Shopify (`shopifyGql_`).
NO JDBC, NO ParcelPanel API, NO local dependency for most grains. Query per order:
`fulfillments(first:10){ displayStatus trackingInfo{company number} events(first:50,
sortKey:HAPPENED_AT){ status happenedAt } }` + `shippingAddress{provinceCode}` + `tags`.

- **Per-metric headless status:**
  - ✅ **NOW from Shopify:** TnT2 Total / 2 Day / 3+ Day, By Carrier, By State, By Box; Lost Arrived /
    Not Arrived (all breakdowns); Routing Match **Carrier** (assigned tag vs Shopify carrier).
  - ❌ **Still blocked → emit `n/a (immature)`, never invent:** **By Hub** and Routing Match **Hub** —
    actual origin hub = carrier invoices (`shipments.hub`), local-only, ~1wk lag.
- 🔴 **Multi-fulfillment gotcha (cost real numbers — do NOT repeat):** an order can have MULTIPLE
  fulfillments and the DELIVERED one is often NOT `fulfillments[0]` (#166044). Read `fulfillments(first:10)`,
  **scan ALL, let the delivered one win** — reading only the first mislabels delivered boxes as no-scan.
- ⚠️ **TNT basis caveat — label it, don't hide it:** TNT = delivery − **pickup scan**. PP `pickup_date`
  is the source of record; on ~17% of orders (367/2,183 @ 08-03) it is a full day EARLIER than Shopify's
  first `IN_TRANSIT` scan → a pure-Shopify build UNDERCOUNTS late (~146 vs 161 @ 08-03). Implement on the
  Shopify scan basis, **log the basis used**, and switch pickup to PP `pickup_date` when shipments/PP
  land in the cloud (the sole remaining JDBC use). Why PP is stale as a live source: its writer
  `GelPackCalculator/sync_logon.py` is logon-triggered + 12h-throttled → up to 19h-old delivery data,
  which is exactly why an "hourly headless" report needs Shopify, not PP.

### Locked definitions (Kurt 2026-08-06 — do NOT re-derive from adjacent sources)

- **TnT2** — late = `delivery_status.transit_days > 2` from **shipping.db timestamps**, NEVER the PP
  `transit_time` column (TNT HARD RULE, ShippingReports/CLAUDE.md). Split **2-Day vs 3+ Day**.
- **Lost in Transit** — **Arrived** = delivered OR invoice-confirmed (`shipments.delivery_date`);
  **Not Arrived** = neither. **Matured cohorts only** (a live cohort's "not arrived" is just in-flight).
  Veho quirk: `exception` status WITH a `delivery_date` = **delivered**, not lost.
- **Routing Match** — assigned hub/carrier parsed from `shopify_orders.raw_routing_tags`
  (`!ANY/NO <carrier> - <Hub>_AHB!`) vs **actual** (`delivery_status.origin_hub` / carrier). Incident
  motivating it: `_outputs/reports/2026-07-29-tag-mismatches-vF.csv` (RMFG shipped from a different
  hub than the routing tag — matured 07-13 hub-match ~32%). Box normalized: `TRAY`→Medium Tray,
  `TRAY_LARGE`→Large Tray, else Regular Box.
- **Carrier** = ParcelPanel/`shipments` carrier normalized (LaserShip/Veho/OnTrac/FedEx/UPS), **NOT**
  Shopify `tracking_company` (mislabels OnTrac↔LaserShip/Veho — same rule as R69 above).

### Walk-forward rules (negatives-first)

- **A1. Matured columns are FROZEN — write once, never recompute.** Only the CURRENT (rightmost)
  cohort column refreshes each run. Rewriting a matured column re-derives history off a telemetry
  copy that has since changed and silently rewrites numbers Dan already read. The refresh MUST target
  the current column by ship-week header match, never a full-tab rebuild.
- **A2. Append a NEW column only when the ship week ROLLS.** On roll, freeze the prior current column
  and add rows for any new By-Hub/Carrier/State/Box key values seen (never drop an existing row —
  gaps read as zero, not missing).
- **A3. Current-column source is the cloud MySQL telemetry (precond 1), NOT ParcelPanel per-order and
  NOT a CSV.** CSVs (cwill backfill) are **backfill only** — one-time seeding of a matured column that
  predates cloud telemetry, never the refresh mechanism.
- **A4. Lost/TnT2 for the CURRENT column are provisional** (cohort not matured; invoices lagging).
  Label the current column maturity in the header/provenance cell so a partial "Not Arrived" isn't
  read as loss. Lost-in-Transit totals are only final once the cohort matures.
- **A5. Blank ≠ zero.** Missing actual hub (invoices not in yet) → blank cell, never a fabricated hub
  or a silent 0 in the match rate.
- **A6. 🔴 NEVER write over rows the builder does not own (Kurt via coordinator 2026-08-06).** Dan
  hand-adds his OWN rows/formulas to these tabs (e.g. a blank-label late-% row under "3+ Day
  Shipments" = `=3+Day/Total` → 6.32% / 7.18% / 4.71%), and more over time. The refresh keys off the
  row **LABEL** and computes a value ONLY for builder-owned metric rows: **Total · 2-Day · 3+ Day ·
  Arrived · Not Arrived · `{value} · 2 Day|3+ Day|Arrived|Not Arrived` · Routing Matched - Hub|Carrier
  · the "By X" section headers** (+ their By-Hub/Carrier/State/Box key rows). Any row it does NOT
  recognize — blank-label rows, Dan's formula rows — is **SKIPPED, cells untouched**. Do NOT write a
  full contiguous column; issue a **per-cell batch that touches only owned rows**, so Dan's formulas
  keep recomputing off the data cells. Mirror the interim local builder exactly
  (`fill_E_0803.py`: `value_for()` returns `None`/null for unrecognized labels; writer batches only
  non-None cells).

### Directives 1–3 (Kurt via coordinator 2026-08-06) — parity across local builders + GAS

- **D1. ~~MERGE OnTrac → LaserShip.~~ 🔴 SUPERSEDED 2026-08-07 by D5 — the canonical name is
  `OnTrac`, LaserShip is the alias.** The merge itself still holds (one bucket, never two); only the
  bucket's NAME reversed. Do not restore `LaserShip` as an output label.
- **D2. CODIFY the rate row — BLANK-LABEL, POSITIONAL, after EVERY pair (both tabs).** Dan's
  hand-added %-rows were inconsistent/broken (`=B4/B2`, `=SUM(B24+B22)/B21`). Replaced with a
  GENERATED, builder-OWNED **blank-label** rate row directly after each group's good→bad count pair —
  Total AND every By-Hub / By-Carrier / By-State / By-Box value (~64 rate rows/tab). Structure per
  value: `{key} · {good}` row, `{key} · {bad}` row, then the BLANK-LABEL rate row (same after the
  Total pair). Formula = **`=IF(good+bad>0, bad/(good+bad), "")`**, a LIVE per-column formula
  referencing the two rows above. Grains: **TnT2** good=`2 Day`/`2 Day Shipments`, bad=`3+
  Day`/`3+ Day Shipments`; **Lost** good=`Arrived`, bad=`Not Arrived`. 🔴 These blank rows USED to be
  Dan's and are **builder-owned now** — the walk-forward writer recognizes them **positionally** (a
  blank row whose r-1 matches bad and r-2 matches good) and maintains them, while still SKIPPING any
  OTHER unrecognized blank/row Dan adds elsewhere (A6 holds). GAS: `writeRateFormulasAndFormat_`.
- **D3. Number format by ROW TYPE — real bug guard, not cosmetics.** Count rows = NUMBER `"0"`,
  rate rows = PERCENT `"0.00%"`, applied via `spreadsheets.batchUpdate` repeatCell (GAS:
  `setNumberFormat` in the rate/format pass). After the restructure, rows that were previously Dan's
  %-formatted rows held COUNTS and rendered `"100.00%"`/`"2600.00%"` for a count of 1/26. **Always
  set BOTH, never inherit** the cell's prior format.
- **D4. 07-27 (col D) mismatch → EVEN HAIRCUT to the column total, keep 105 (Kurt: "paper over it").**
  Col D was reconciled by an even haircut so every dimension sums to the column total (3+ = **105**
  across Hub/Carrier/State/Box; 2-Day = 2110). 🔴 A naive rebuild from current shipping.db recomputes
  07-27 to **126** and would UNDO the haircut — so the refresh MUST be **walk-forward, matured
  columns FROZEN** exactly as specced. Do NOT rebuild a matured column. (Supersedes the earlier
  "rebuild to 126" note — Kurt chose the haircut; the local builder was deliberately NOT re-run for
  this column.)

### Directives 5–12 (Kurt 2026-08-07) — proven on `_SHIP_2026-08-03`, negatives first

Every rule below is a bug that reached a number before it was caught. GAS port: `PivotAnalytics.gs`.

- **D5. Canonical carrier name = `OnTrac`; LaserShip is the ALIAS.** Both spellings fold to
  `OnTrac`; no LaserShip bucket exists anywhere. Reverses D1's naming.
- **D6. Delivered = Shopify DELIVERED event ∪ ParcelPanel `status='delivered'`. NEITHER SOURCE IS
  COMPLETE.** PP hid **224** deliveries Shopify had (its writer `GelPackCalculator/sync_logon.py` is
  logon-triggered + 12h-throttled); Shopify's feed was missing OnTrac's final scan on **#166228** and
  **#166660**, both confirmed delivered on OnTrac's own tracking pages. Undelivered only if BOTH are
  silent. Supersedes the "Shopify IS the delivery truth" framing above.
- **D7. `displayStatus` is a LAGGING ROLLUP — a DELIVERED scan event beats it.** Three wk0803 boxes
  read DELAYED / OUT_FOR_DELIVERY with a DELIVERED scan. Also scan **ALL** `fulfillments(first:10)` —
  the delivered one is often not index 0; reading index 0 lost 26 deliveries.
- **D8. `happenedAt` is UTC — convert to `America/New_York` BEFORE taking the date.** Skipping this
  adds a phantom day to every evening delivery: it shifted 471 rows and DOUBLED late (146 vs 62).
- **D9. Survivorship — late is measured over the WHOLE cohort, and Lost in Transit is INSIDE 3+ Day.**
  `3+ Day` = delivered-late **+ all undelivered**, so `2 Day + 3+ Day == Total Shipments` and the rate
  row divides by the full cohort naturally. Never-collected boxes COUNT as late. A nested
  `of which: Lost in Transit` sub-row sits under `3+ Day` (non-blank label, no rate row beneath, and
  **nothing may sum it** — double counting turns 114 into 166 of 2,305).
- **D10. Lost vs ACTIVE.** Of the undelivered, a box with a real scan in the last **24h** is ACTIVE
  (super-late, demonstrably moving) and is EXCLUDED from the `of which: Lost in Transit` count; zero
  scans, or silent ≥24h, stays LOST ("unverified" ≠ "known not lost"). Recency is clock-dependent —
  recompute every run, never cache the class. 🔴 **`CONFIRMED` fires at LABEL CREATION and is NOT a
  scan** — "has events" is the wrong filter. A real scan is `IN_TRANSIT` / `OUT_FOR_DELIVERY` /
  `ATTEMPTED_DELIVERY` / `READY_FOR_PICKUP` / `PICKED_UP` / `DELIVERED`.
- **D11. Routing tags: fullmatch per tag, drop `!NO `, require EXACTLY ONE assignment.** Format is
  `!<Carrier> <Service> - <Hub>_AHB!` (`!ANY FedEx - <Hub>_AHB!` for pins). An order carries ONE
  assignment plus several `!NO <carrier> - <Hub>_AHB!` EXCLUSIONS, so a `.search()` over the joined
  tag string returns an **excluded** hub — **209 of 2,305** orders carry exclusions ONLY and would
  have been stamped with a hub the engine explicitly ruled out. Those are `(no routing tag)` (the
  sheet's `Unknown` row), never a guessed hub. **By Hub is the ASSIGNED/intended hub** — header reads
  `By Hub (assigned)`. **`Routing Matched - Hub` stays `n/a (immature)`**: it compares assigned vs
  ACTUAL, actual needs carrier invoices (~1wk lag), and filling it from the tag compares the tag to
  itself and always reads 100%. Orders with no assignment are **uncomparable, not "matched"** — they
  leave the Routing-Match denominator (counting them as matched inflated it to a false 96.6%).
- **D12. Join on `order_number`, never `tracking_number`, and window-guard every date.** FedEx
  REUSES tracking numbers. **#166740** carries BOTH `_SHIP_2026-08-03` and `RMFG_20260728` and its
  only shipped fulfillment is the 07-28 one delivered 07-30 — the newest-delivered-fulfillment rule
  scored a **July** shipment as this cohort's TNT 2. **Cohort-pin dedupe rule (Kurt):** an order
  pinned to one cohort column must NEVER be counted in another cohort/RMFG column later; #166740
  stays in 08-03 only. Reject out-of-window dates and SURFACE them — never silently drop the order
  (that changes Total Shipments, which is a human decision).
- **D13. Rows are never appended or inserted by the writer.** A row insert shifts every formula
  reference below it. A bucket with no row is REPORTED for a human to add (Chicago's 307 orders had
  no row until Kurt approved one). Writes stay owned-row, per-cell, keyed off the column-A label;
  blank-label rate rows are never overwritten.

🔴 **GAS global-namespace hazard.** Apps Script loads every `.gs` into ONE global scope, so a
duplicated top-level name silently overrides across files — an "inert" file gated behind a property
is NOT inert once it is deployed. `normCarrier_` was defined in BOTH `Code.gs:954` (OnTrac as its
own bucket) and `PivotAnalytics.gs` (merged into LaserShip), which would have silently changed the
live hourly reship report's carrier bucketing **the moment PivotAnalytics was first pushed**.
Verified 2026-08-07: the deployed project contains only `appsscript`, `Code`, `Exceptions` — neither
`PivotAnalytics.gs` nor `PivotSheet.gs` had ever been pushed, so nothing was live-broken; this was
caught before deployment, not after. All `PivotAnalytics.gs` symbols are now `pa`-prefixed.
**Still latent (pre-existing, NOT introduced by the analytics port): `refresh` and `iso_` are
duplicated between `Code.gs` and the also-unpushed `PivotSheet.gs`** — `iso_` is identical so it is
harmless, but the two `refresh` bodies differ. Pushing `PivotSheet.gs` as-is would hijack `refresh`.

### D14 — DAILY cadence guards (Kurt 2026-08-07)

The refresh runs on a **daily** time-trigger, not weekly — weekly was too slow. Trigger =
**Time-driven → Day timer, an EVENING hour** (after the day's last meaningful carrier scans).
**The first executing run of each week is TUESDAY EVENING** (Kurt: "we can start tuesday evening")
— that falls out of the ship-day guard rather than being configured, since the Monday run exits.

| day | cohort age | what runs |
|---|---:|---|
| **Mon** (ship day) | 0 | **current leg** exits — one log line, no writes, no fetch. 🔴 **The previous-week reconcile leg STILL RUNS** (D20 — it used to exit the whole invocation, freezing last week's column every Monday) |
| **Tue** (first real run) | 1 | Shopify-only; appends the new `_SHIP_` column |
| **Wed** | 2 | Shopify-only |
| **Thu–Sun** | 3+ | Shopify **+ PP rescue**, ≤200 calls/run, oldest-scan first |

The trigger UI cannot express any of the following, so all four live in `refreshCurrentColumn`:

- **Ship-day skip.** Cohort age `0` → log one line and skip **the current leg only** (D20 — never
  `return` out of the invocation). On a daily trigger the Monday run
  fires while boxes are still being handed to carriers: nothing has moved, so every box reads
  undelivered and — under the survivorship rule (D9) — **LATE**. Anchored on cohort **age**, not
  day-of-week, so a shifted ship day (holiday week) cannot defeat it.
- **PP leg gated by cohort age.** Age `<3d` (Tue/Wed) → Shopify-only, log `PP: skipped (cohort age
  <3d)`. PP budget is **2,500 calls/week** (standing) and Exceptions' hourly job is the dominant
  consumer. Early in the week the rescue set ≈ the whole cohort (~2,300), so one uncapped Tuesday
  run could eat the week. Deliveries stream via Shopify fine mid-week and PP reconciles later.
- **Hard cap 200 PP calls/run**, any day, **oldest-scan first** — the box silent longest is the one
  most worth rescuing. Logs `PP: capped, skipped N candidates` when it bites. A silent truncation
  would read as "PP found nothing", which is the failure this whole leg already burned us on.
- **Steady state ≈ 45 × 4 ≈ 180 PP calls/week** against 2,500.

🔴 **The cohort is resolved from the CALENDAR + Shopify, never from the sheet's rightmost header.**
Reading the header pins the script to whatever column already exists, so it could never discover a
new cohort and would refresh last week's column forever — the walk-forward would silently stall.
`paCurrentShipWeek_` walks back week by week from the most recent Monday and takes the first tag
with orders, which also makes the Monday skip safe: first touch of a new cohort is Tuesday, and
`paCurrentCol_` appends the column then.

### D15 — MATURITY MODEL: script-owned 0–10d, frozen and Kurt-owned after (Kurt 2026-08-07)

**`PA_MATURITY_DAYS = 10`.** A cohort column is **script-owned** and self-heals on every daily run
from age 1 until age 10; at 10 days it **FREEZES** and the script refuses it forever after.

**Why it exists — stale wrongness.** A box frozen as `3+ Day` / `Not Arrived` can later prove
delivered. On 2026-08-07 alone `arrived` moved 2,253 → 2,256 → 2,260 → 2,262 across a single day.
Without reconciliation the column freezes at a number we already know is wrong.

**Shape.** Each daily run refreshes up to **two** cohorts: the current one, then the previous one
while it is still <10d. In practice both legs only run on roughly days 7–10 of the older cohort.

- The previous leg runs **SECOND** and is wrapped: a failure there (6-minute ceiling, a refused
  column) logs loudly and **never costs us the current column**, which is already written by then.
- Per-leg timings and PP usage are logged every run so headroom against the 360s ceiling is visible.
- The ≤200 PP calls/run cap is **shared across both legs**; oldest-scan-first unchanged; Tue/Wed
  still skip PP entirely.
- A previous cohort whose column does not exist is **reported, never appended** — appending would
  place an older cohort to the RIGHT of the current one.

🔴 **The freeze assert is bounded, not removed.** `paCurrentCol_` permits the rightmost column, or
the one immediately left of it, and only then:
- the age is **re-derived from that column's own header**, never from a parameter, so no caller can
  talk the writer into an old column by passing a friendly ship week;
- the two columns must be **distinct and adjacent**, so a header gap or a duplicated header cannot
  let the previous leg land on the current column;
- age ≥ `PA_MATURITY_DAYS` throws with the column, header and age named.

**The 105 story is the motivating case, and the reason the window is bounded at all.** Column D
(`_SHIP_2026-07-27`) was reconciled BY HAND — an even haircut to 105 (D4) — and a naive rebuild
recomputes it to 126. Under this model that column is long frozen and untouchable. Kurt's position
(accepted): hand edits only existed because the numbers were wrong; with union truth (D6) plus daily
self-heal they should not recur, and **a disagreement inside the 10-day window is a bug report, not
an edit**. His day-of hand edits live on OTHER sheets (vF routing), not this one, and his own
rows/notes here are label-skipped by the owned-row writer regardless.

### D16 — THREE OBSERVATIONS under `3+ Day`, and the monotonicity invariant (Kurt 2026-08-07)

> 🔴 **AMENDED BY [D27](#d27--still-moving-4-days-splits-on-each-boxs-own-promise-not-on-a-calendar-day-count-kurt-2026-08-19) (2026-08-19): FOUR observations, not three.** `still moving (4+ days)` is split
> into `Still Moving =< TNT2` / `Still Moving > TNT2` on each box's OWN promise (D18 clock) — a fixed
> calendar day-count label is wrong on a multi-leg week and read mid-week. The other two rows below
> are UNCHANGED and take precedence. Read D27 before touching this block.

**APPROVED MODEL** (Kurt's paste, verbatim):

```
3+ Day Shipments                     111
   still moving (4+ days)              3
   no scan in 24h+ (investigating)    13
   never picked up by carrier         27
```

Definitions: **still moving** = undelivered, last real movement scan <24h · **no scan in 24h+** =
scanned, then silent ≥24h · **never picked up** = zero carrier scans ever. The three **partition
Not Arrived**.

🔴 **The words "Lost in Transit" appear NOWHERE on TnT2** — that phrasing is what Dan reacts to.
The Lost in Transit **tab** keeps its own name and rows untouched.

All three sit INSIDE `3+ Day` and **none is summed into any total**. Asserted every run: the three
sum to `Not Arrived`; that SUM is **monotone non-increasing** per cohort (refuse to write on a
rise — a box leaves only by DELIVERING, and delivered cannot un-deliver); migration BETWEEN the
three is expected and logged by name; `2 Day + 3+ Day == Total` unchanged.

Superseded en route to this: a single `of which: Lost in Transit` row (churned, Kurt cleared it),
then a two-row split. Both are dead — the keys are gone, not merely unused.

#### History that motivated it

Kurt cleared the single nested cell himself — *"i removed it because it kept changing"* — because the
clock-recomputed lost number churned 49 → 39 → 40. His ruling: **the lost number "should go down,
not up."** Approved shape: *"fine we go with tnt3, tnt4+, still in transit"*.

```
3+ Day Shipments                        111   (unchanged — delivered-late + ALL undelivered)
   of which: 4+ Day, still in transit     1   (active: a real scan <24h — moving, not lost)
   of which: Lost in Transit             40   (never-collected + gone-dark)
```

- Both rows are **INSIDE** `3+ Day` and **neither may be summed** into any total.
- They **partition Not Arrived**: `still_in_transit + lost == Not Arrived` (LiT tab), asserted every
  run against the live cells.
- 🔴 **That SUM is monotone non-increasing within a cohort** — a box leaves only by DELIVERING, and
  delivered cannot un-deliver. Assert `new_sum <= previous_sum`; **refuse to write** on a rise.
- **Churn BETWEEN the two rows is expected and allowed.** A box going dark is a visible migration
  moving → lost with the sum unchanged, so `lost` may rise **only** when still-in-transit falls by
  at least as much — asserted separately so the log names the migration.
- 24h remains the boundary between the two rows. B/C/D blank on the new row: matured cohorts are
  never retro-classified.
- 🔴 Match each nested row by its **full label**. There are now TWO `of which` rows and a substring
  match grabs the first, silently comparing the wrong pair — that bug fired once, on the first
  three-row write, and is why the cross-tab assert is full-label now.

**The 39 → 40 that triggered this** (owed account): purely the 24h clock. Two OnTrac boxes crossed
the boundary with **identical scan timestamps at both reads** — `#168209` (`1LSDBVC001514UE`,
23.6h → 32.5h) and `#168332` (`1LSDBVC001514K4`, 21.2h → 30.1h) — while one previously-lost box
delivered, netting +1. No data changed for either box; six other boxes delivered and left the
undelivered set entirely. Under D16 that same event now shows as still-in-transit −2 / lost +2 with
the sum flat, instead of an unexplained headline bump.

### D17 — HUB ATTRIBUTION for exclusion-only orders (Kurt 2026-08-07)

> "if its one hub open, then just put it in the hub category"

An order with no assignment tag still carries its `!NO <carrier> - <Hub>_AHB!` fence stack.
Subtract the fenced lanes from the observed lane universe and count the hubs left standing:

- **exactly 1 hub open → that hub.** It is effectively assigned. Parking it in a residual bucket
  hid real volume: all **64** wk0803 cases are **Dallas**, and their FedEx long-hauls were always
  Dallas's late boxes (Dallas `3+ Day` 9 → 18 when they landed).
- **≥2 hubs open → the residual row**, renamed from `Unknown` to **`RMFG choice (2+ hubs open)`** —
  RMFG genuinely chose.

🔴 **The lane universe is DERIVED, never hardcoded.** Every `<carrier, hub>` pair seen in ANY
`_AHB!` tag across the cohort — assignment OR fence — is a lane, so a new hub (NJ next week) falls
in automatically. wk0803 universe: FedEx@{Anaheim,Chicago,Dallas,Nashville},
OnTrac@{Anaheim,Chicago,Dallas,Nashville}, UPS@Dallas — note UPS serves **only** Dallas, which is
why an order fencing `!NO UPS - Dallas` loses UPS entirely.

🔴 **Never special-case tag SHAPE.** 9 orders carry an engine `!ANY - Dallas_AHB!` intent expressed
on Shopify as a fence stack; classifying purely by open-hub count lands them correctly.

🔴 **The rename is SECTION-SCOPED.** `Unknown · …` exists in BOTH the By Hub and the By Carrier
blocks. Only the By Hub rows are renamed — there is never an unknown CARRIER, and that row keeps
its name and its zero. B/C/D on the renamed rows are preserved (matured cohorts are not
re-attributed): `[11, 37, 678]` and `[1, 3, 26]` survived the rename untouched.

**Validation against the analysis session's `_outputs/reports/2026-08-08-wk0803-unknown-bucket.csv`:
209/209 in-cohort orders match exactly.** The CSV has 213 rows; the 4 extra (#166331, #166981,
#168366, #168374) are tagged **`Reship`** and are excluded from the cohort by design, so the
residual row reads **145**, not the 149 quoted from the CSV.

**Result on the sheet:** Anaheim 468 · Chicago 307 · Dallas **584** (was 520) · Indianapolis 0 ·
Nashville 801 · `RMFG choice (2+ hubs open)` **145** (was 209) — 2,305 total, By Hub still sums to
`2 Day 2,194 / 3+ Day 111`.

### D18 — THE PROMISE CLOCK IS PER BOX, FROM ITS OWN PICKUP (Kurt 2026-08-14)

> **Kurt, annotating `TnT2!F3` (`_SHIP_2026-08-10`, `3+ Day Shipments` = 220):**
> *"This number is wrong. 55 from Monday pickup, reviewing Tuesday pickup"*

🔴 **What NOT to do:** never measure a box's 2-day promise from the COHORT's ship Monday. A ship week
is **MULTI-LEG** — a Monday pickup plus a Tuesday (Dallas) leg is standard, not an exception — so the
cohort clock steals a full day from every Tuesday-leg box and flips it to late while it is still
inside its promise. `3342d84` added the survivorship maturity gate as `late = !ontime && (arrived ||
cohortAge > PA_SLA)`; the gate was right, its clock was not.

🔴 **A box with no pickup ANYWHERE is `pending`, never late.** Under the cohort clock every
never-collected box scored as a miss — it cannot be counted against a promise the carrier never
started. It is already visible on the `never picked up by carrier` observation row; counting it as
late too double-narrates the same failure and inflates the headline the ops read.

**The rule.** `late = !ontime && (arrived || boxAge > PA_SLA)`, `boxAge = todayET − that box's own
pickup date`; everything undelivered and not late is `pending`.

**Pickup authority (standing doctrine, do not re-derive):**
1. ParcelPanel `pickup_date` is CANONICAL.
2. The Shopify first-movement scan is the fallback **only when PP has none**.
3. Convert to `America/New_York` BEFORE any date math (a raw UTC `.date()` adds a phantom day to
   every evening event and once doubled the late rate).
4. Movement = `IN_TRANSIT` · `OUT_FOR_DELIVERY` · `ATTEMPTED_DELIVERY` · `DELIVERED`.
   **`CONFIRMED` is NOT movement** — it fires at label creation, so counting it makes every
   never-collected box look like it moved.
5. PP is earlier than the Shopify scan on ~17% of boxes and **never later**. Any box where Shopify
   reads earlier is LOGGED BY NAME; the expected count is **zero**, and a moving counter means an
   upstream feed changed — investigate, do not adjust the number.

**Scope — the corrected clock applies to every derived figure:** TnT2 `2 Day` / `3+ Day` / pending,
the three observation rows, and `moved` (which is now literally "has a pickup"). Lost in Transit's
`Arrived` / `Not Arrived` are arrival measures and are clock-independent — they must NOT shift.

**Assert change (same six named asserts, one bound corrected):** `PA_ASSERT_PENDING_SUBSET` was
`pending <= still-moving`, which held only because the cohort clock could produce no other kind of
pending box. Never-picked-up boxes are now pending too, and they live on a different observation row,
so the bound is `pending <= Not Arrived` plus "no pending box is arrived". The old bound would throw
on every multi-leg week.

**Measured on `_SHIP_2026-08-10` (fresh Shopify pull 2026-08-14, one pull scored both ways):**

| clock | 2 Day | 3+ Day | pending | late rate |
|---|---|---|---|---|
| cohort (old) | 2,216 | **102** | 0 | 4.40% |
| per box (new) | 2,216 | **77** (61 Mon pickup · 16 Tue) | **25** (never picked up) | 3.32% |

Replayed as of **2026-08-13**, the day the sheet's 220 was written: old 102 → **new 61, every one of
them a Monday pickup**; the Tuesday leg (`boxAge` 2) was correctly pending. That reproduces Kurt's
"55 from Monday pickup" to within intraday timing. The sheet's own `220 = 2,318 − 2,098` — literally
every undelivered box at that instant, which is the cohort clock's signature.

### D19 — A NEW HUB'S ROWS ARE ADDED BY A HUMAN-INVOKED TOOL, NEVER BY THE REFRESH (2026-08-14)

**D13 is UPHELD, not relaxed: the refresh writer still never inserts a row.** The general case is
solved by making the insert a one-click *maintenance* action instead of a hand-edit.

🔴 **The failure this exists to stop — a bucket that is computed, warned about, and written NOWHERE.**
Swedesboro (the NJ hub, live for `_SHIP_2026-08-10`) fell out of the DERIVED lane universe correctly
(`FedEx@Swedesboro`, `OnTrac@Swedesboro`) and `paValues_` emitted it correctly, but neither cohort tab
had a `By Hub (assigned)` row for it. The dry run reported `hub → Swedesboro · 2 Day=569 · 3+ Day=12`
(TnT2) and `· Arrived=570 · Not Arrived=34` (Lost in Transit) — **604 boxes, ~26% of the cohort,
silently absent from every hub cut** while the tab still footed to Total. The warning was correct and
was not enough: a warning in a log nobody opens is a silent failure with extra steps.

**Why auto-insert on the refresh path is still forbidden.** The refresh runs unattended on a daily
trigger. A structural edit there would (a) shift every reference below it while nobody is watching,
(b) fire on a TYPO'd or one-off hub name straight out of a tag, minting a permanent row, and (c) make
the tab's shape a function of one day's data. Row inserts stay a deliberate, observed action.

**The tool** (`PivotAnalytics.gs`, generic over hub name — the next new hub needs no code change):

| Function | Does |
|---|---|
| `previewAddSwedesboroRows()` | logs exactly where the rows land. Writes nothing. |
| `addSwedesboroRows()` | inserts them. Idempotent — re-running finds the rows and no-ops. |
| `auditRateRows()` | read-only integrity check of EVERY rate row on both tabs. |
| `fillRateFormulasCurrentColumn(dry)` | fills MISSING rate formulas in the rightmost column only. |

Rules it enforces, each one a way this could corrupt the tab:
- **Placement:** real hubs alphabetical; `RMFG choice (2+ hubs open)` is the residual bucket and stays
  LAST regardless of alphabet (it was `Unknown` before D17 renamed it — a catch-all, not a hub). So
  Swedesboro lands after Nashville, BEFORE RMFG choice.
- **History stays BLANK, never 0.** A `0` in a matured column asserts "this hub shipped nothing that
  week"; the hub did not exist. Only the rightmost (live) cohort column gets the rate formula —
  writing one into a frozen column puts a live formula inside Kurt-owned history.
- **Formats cloned row-for-row from a sibling hub group** (counts NUMBER `"0"`, rate PERCENT
  `"0.00%"`, indent/label style), never invented. Labels are `   {hub} · {grain}` — three leading
  spaces, `·` = U+00B7 — because the writer keys on `trim()`ed column A and the key must match what
  `paValues_` emits (`hub||Swedesboro · 2 Day`) or the next run warns again.
🔴 **D19a (2026-08-17) — A RATE ROW'S PAIR IS RESOLVED BY LABEL, NEVER BY POSITION.** "The pair is the
two rows directly above the rate row" holds for every By-Hub / By-Carrier / By-State / By-Box block and
is FALSE for the TnT2 top block, because D16 inserted the three observation rows between
`3+ Day Shipments` (row 4) and its rate row (row 8). Rows 5–7 are a nested PARTITION of Not Arrived,
not a rate pair.

- **Symptom:** `paAuditRateRows_` reported `TnT2!B8..F8` as mis-pointed — **5 false positives, all five
  formulas correct** — and that count refused the D19 hub insert with `PA_INSERT_PRE_AUDIT_FAILED`,
  blocking a real maintenance action on a phantom. 🔴 **A verifier that cries wolf gets the insert
  disabled, which costs the protection the verifier existed to give.**
- 🔴 **The same assumption was on the WRITE side and was the dangerous one.**
  `fillRateFormulasCurrentColumn` computed `good = i-1, bad = i` too. It only fills EMPTY cells, and a
  freshly appended cohort column has NO rate formulas at all (D19), so the top-block cell IS empty and
  eligible — it would have written the HEADLINE late rate as
  `no-scan-24h / (no-scan-24h + never-picked-up)`. On column F that is **45.65% instead of 3.31%**.
- **Damage check — NONE.** `TnT2!F8` renders **3.31% = 76/(2217+76)**, so it points at rows 3+4;
  E8 4.82% = 111/(2194+111) likewise. A mis-pointed formula would render 45.65%. ⚠️ Verified from
  RENDERED VALUES (the Sheets read available here cannot return formula text) — `auditRateRows()` now
  checks the formula text itself and is the confirming run.
- **The rule:** walk UP from the blank rate row past nested rows to the nearest BAD-grain row and
  require a GOOD-grain row immediately above it. Grain is matched on the LABEL — `3+ Day Shipments`,
  `Not Arrived`, or any `{key} · 3+ Day` / `{key} · Not Arrived` — so one resolver
  (`paRatePairFor_`) serves the top block and every dimension block on both tabs with no special case.
  Walk is bounded (`PA_PAIR_WALK_MAX = 6`); **unresolvable is REPORTED, never silently passed, and the
  filler SKIPS rather than writing a guess.**
- **Generalized:** the sheet's row order is DATA, not structure. Any rule of the form "row N−1 is X"
  breaks the next time a directive adds a nested row — and D16 already did it once.

- 🔴 **Rate-row verifier runs BEFORE and AFTER, and it reads the FORMULA TEXT** — a re-pointed
  formula still looks right. It asserts every blank-label rate row references exactly the two rows
  directly above it, in its own column. A pre-existing mis-point REFUSES the insert
  (`PA_INSERT_PRE_AUDIT_FAILED`); a regression after it THROWS (`PA_INSERT_POST_AUDIT_FAILED`), as
  does any rate-row loss (`PA_INSERT_ROW_COUNT`). `paAssertColumns_` runs both sides too.

🔴 **Separate defect this surfaced — a freshly appended cohort column has NO rate formulas at all.**
`paCopyFormatFromPrev_` copies `{formatOnly:true}` on purpose (a plain `copyTo` would clone ~65
relative formulas Kurt owns), so the appended column inherits the rate rows' APPEARANCE and none of
their contents. On column F (`_SHIP_2026-08-10`) **every** rate row on TnT2 and Lost in Transit is
empty — the late-% column reads blank for every hub, carrier, state and box. `fillRateFormulasCurrentColumn()`
closes it: rightmost column only, EMPTY cells only, never overwrites an existing formula, dry by
default.

### D20 — THE SHIP-DAY GUARD SKIPS THE CURRENT LEG, NEVER THE INVOCATION; AND A REFUSED RUN IS LOUD (2026-08-17)

🔴 **The failure — every Monday silently froze last week's column for a day.** D14's ship-day skip was
implemented as a `return` out of `refreshCurrentColumn`, evaluated on the CURRENT cohort *before* the
D15 two-column reconciliation. Observed live Monday 2026-08-17:

```
=== refreshCurrentColumn _SHIP_2026-08-17 (age 0d) — WRITING ===
SKIP — cohort ships today (age 0d); nothing to measure yet.
```

The whole run exited. `_SHIP_2026-08-10` — age 7d, still inside `PA_MATURITY_DAYS` and therefore still
**script-owned and self-healing** — never executed. A brand-new cohort having nothing to measure says
nothing about a 7-day-old one, and the reconcile leg is precisely what the maturity model exists for.

**The rule.** On a ship day the run logs the skip for the CURRENT cohort and then proceeds to the
previous-week leg exactly as on any other day: same `allowAppend=false`, same shared ParcelPanel
budget, previous leg SECOND and non-fatal, per-leg timings logged. All six named asserts, the
walk-forward freeze at `PA_MATURITY_DAYS`, format-copy-from-previous on column creation, label-keyed
owned-row writes, Dan's rows and Kurt's note cells, and the PP per-run cap / weekly counter are
unchanged. The D14 table's Monday row now reads **"current leg exits; previous-week reconcile still
runs."** Tuesday remains the first run that touches the NEW column.

🔴 **Generalized: a guard whose condition is about ONE leg must not be evaluated at the top of a
multi-leg entry point.** Guard placement is a correctness property, not a style choice.

#### D20b — the NOTE-COLUMN decision: keep the assert STRICT, make the refusal LOUD

**The recurring problem.** Kurt annotates cells next to our data. A note in a column with no row-1
header (`TnT2!G7` = "investigate", 2026-08-16) trips `PA_ASSERT_HEADERLESS_COLUMN`, which throws
BEFORE any write — so the run refuses, the column keeps the previous run's numbers, and **nothing
anywhere says so**. Stale and fresh numbers look identical. That is how wk0810 sat at Friday's
figures across Saturday and Sunday.

**Decision (implemented): option (c).** Not (a) "treat a text-only headerless column as a note column
and warn", not (b) "a reserved `Notes` column". Rejected because:

- (a) classifies a structural hazard by CELL CONTENT — the exact "close enough" inference this
  operation has been burned by. It is also not sufficient: a note column sitting to the RIGHT of the
  live cohort column defeats `paCurrentCol_`'s "rightmost or rightmost−1" freeze bound regardless of
  what the cells contain, so the run would still refuse — while the assert that names the real cause
  had been downgraded to a warning nobody reads.
- (b) asks Kurt to change an annotation habit. Habit-dependent guardrails do not hold, and D19 already
  recorded the lesson that *"a warning in a log nobody opens is a silent failure with extra steps."*
- The assert itself is load-bearing: header-last once produced a **self-replicating** corruption (two
  headerless `_SHIP_2026-08-10` columns, `fdc531a`). Relaxing it trades a loud, recoverable stall for
  a quiet, compounding one. **A refusal to write is the correct behavior; the silence around it was
  the defect.**

**Mechanism.** `refreshCurrentColumn` is now a thin wrapper: any throw DMs Kurt privately via
`slack_` (Code.gs — bot DM, email fallback; never the public `SLACK_WEBHOOK`), naming the matched
`PA_*` invariant and, for `PA_ASSERT_HEADERLESS_COLUMN`, the fix ("clear the cell or give the column
a row-1 header"), then **RETHROWS** so the execution still registers as failed. The previous-leg
`catch` — which swallows by design so a reconcile failure cannot cost us the current column — alerts
there too, or a previous column can freeze for its entire remaining window unnoticed. **No assert is
weakened.** Detection latency becomes one daily run (same evening), not days.

**Tradeoff, stated:** Kurt still cannot leave a note in a headerless column — the run still refuses
while the cell is there. We buy visibility, not tolerance. If tolerance is later wanted, the right
shape is a **deliberately headered** note column (a real row-1 header the writer's label-keyed
owned-row logic skips anyway, placed LEFT of the cohort columns so the rightmost/rightmost−1 bound is
untouched) — a structural, human-invoked change on the D19 maintenance path, never a content
heuristic on the refresh path.

### D21 — "POLL ONLY THE UNDELIVERED" (Kurt 2026-08-17) — DESIGN ACCEPTED, IMPLEMENTATION **HELD**

**Kurt's observation is correct and it is the right optimization:** a DELIVERED box is TERMINAL. Its
pickup date, delivery date and TNT bucket can never change, so re-polling it every night is pure
waste. At ~2,300 orders/cohort and two legs, the current refresh pages nearly the whole cohort twice.

**Status: designed, NOT implemented.** The gate is "totals must be provably identical to a full
recount," and that cannot be established from here — Apps Script cannot be executed by an agent, so a
cached-vs-fresh parity run is a Kurt click. Shipping a cache that silently drifts would corrupt the
one number Dan reads. Implementation waits on the parity harness in the checklist below.

**Design (settle these; do not re-derive):**

- **Store = a hidden sheet tab `_pa_verdicts`, NOT Script Properties.** Properties cap at 9KB/property
  and ~500KB total; 2,300 orders/week × several weeks does not fit. (Same reasoning that put `_state`
  on a tab, R15.)
- **Key = `shipWeek || order_number`.** 🔴 NEVER `tracking_number` — FedEx REUSES tracking numbers
  (D12), and a reused number would import a stale verdict from another shipment.
- **A verdict is written ONLY when the box is TERMINAL** — i.e. delivered under the D6 union. One row:
  `shipWeek · order_number · delivered_at(ET) · pickup_date(ET) · pickup_source(pp|shopify) · tnt_days
  · bucket(2 Day|3+ Day) · hub · carrier · state · box · verdict_written_at`. Undelivered boxes are
  never cached: pending / still-moving / no-scan-24h / never-picked-up are all **clock-dependent**
  (D10 recomputes recency every run and never caches the class), and a never-picked-up box acquiring
  a pickup scan is exactly the transition the wk0810 recount just measured (23 of 25 moved in a week).
- **Column totals = cached-final ∪ freshly-polled**, then EVERY existing assert runs against the
  merged set unchanged. The merge is a union over the cohort's live membership, so an order that has
  LEFT the cohort contributes nothing even if a verdict row survives for it.
- **Invalidation — a cached verdict is dropped, not trusted, when:** the order is no longer in the
  live cohort query (`tag:'_SHIP_…' -status:cancelled -tag:'Reship'`) — covers cancellation, a
  `Reship` tag appearing, and cohort re-pinning (D12 #166740); the ship week's column is refused or
  re-created; or the verdict predates a change to the bucketing rules. **Stamp a
  `PA_VERDICT_SCHEMA_VERSION` in the tab and drop the whole cache on a bump** — a directive change
  (D18's clock rewrite is the worked example) must never be served from a cache computed under the
  old rule.
- **Interaction with the walk-forward freeze:** the cache is an input to the refresh, so it can only
  ever affect columns the script still owns (age < `PA_MATURITY_DAYS`). At freeze the cohort's rows
  become inert; prune them once past maturity to bound tab growth.
- 🔴 **Mandatory drift control — a WEEKLY FULL RECOUNT that ignores the cache and compares.** Any
  disagreement is logged BY ORDER NUMBER and alerted via the D20b path; the fresh recount wins. Without
  this the cache is unfalsifiable, and "an optimization you cannot falsify" is how a silent-degrade
  bug lives for months (the PP leg failing invisibly on 2,303/2,305 orders is the precedent).
- **Expected win — to be MEASURED, not asserted.** Directionally: Shopify order pages drop from
  ~93/run toward the undelivered remainder (wk0810 today: 32 of 2,317 not arrived, ~1.4%), and the PP
  rescue set shrinks with it. 🔴 The before/after must come from an instrumented run, not an estimate —
  never optimize against an uninstrumented baseline.

**Implementation checklist (do in this order):** (1) write verdict rows in SHADOW on the normal path,
reading nothing from them; (2) run a full recount and a cache-backed recount over the same cohort and
assert every figure identical, including the three observation rows; (3) only then let the cache
suppress polling; (4) leave the weekly full recount permanently on.

### D22 — `TNT1` IS A NESTED SUBSET OF `2 Day Shipments`, NEVER A SIBLING BUCKET (Kurt 2026-08-17)

> Kurt, verbatim: **"I want to get TNT0 and TNT1 shipments in there (just label the row tnt1)"**

**Definition (the only one).** `TNT1` = boxes DELIVERED with **TNT ≤ 1 calendar day**, measured
per box as `delivery date − that box's OWN pickup date`, ET, on the **D18 per-box clock** — the same
`r.tnt` every other row here uses (PP `pickup_date` authoritative, Shopify's first movement scan only
when PP has none). **TNT 0 (same-day) is INCLUDED** — it is genuine on short intra-region lanes
(42 boxes on `_SHIP_2026-08-10`) — and gets **no row of its own**, by Kurt's instruction.

🔴 **The failure this prevents: reading TNT1 as a bucket beside 2 Day.** It is a strict SUBSET.
`2 Day Shipments` still counts **TNT ≤ 2 including every TNT1 box** and is NOT changed; TNT1 is
summed into NOTHING — same rule as the D16 observation rows. `PA_ASSERT_TNT1_SUBSET` throws before
any write if `TNT1 > 2 Day`, because a TNT1 count that exceeded its parent would mean the clock or
the threshold drifted, and the row would then misrepresent the headline.

**On-sheet shape** (TnT2 top block, after the insert) — TNT1 sits at the same nesting level as the
D16 observations, one indent under its parent:

```
   2 Day Shipments            <- TNT <= 2, UNCHANGED (includes the TNT1 boxes)
        TNT1                  <- TNT <= 1 (incl. TNT 0). Subset. Summed into nothing.
   3+ Day Shipments
        still moving (4+ days) / no scan in 24h+ / never picked up   (D16 observations)
   (blank-label rate row)     <- pair is still 2 Day + 3+ Day
```

🔴 **The insert lands BETWEEN a rate row's good and bad rows** — the first time that has happened.
`paRatePairFor_` (D19a) previously required the good row to sit **directly above** the bad row; it now
walks up past nested rows on **that side too**, skipping only labels on the explicit `PA_NESTED`
allowlist (`PA_OBS` + `TNT1`). Skipping "anything that is not a grain row" was rejected: it would let
the resolver stroll out of the block and pair rows from two different sections — the exact bug family
this resolver exists to kill. Verified offline pre- and post-insert: the top-block pair resolves
3+4 before and **3+5 after**, and every dimension block is unaffected.

**Row insert = human-invoked, idempotent, verified on both sides (D13/D19 discipline).**
`previewAddTnt1Row()` writes nothing and logs the landing row; `addTnt1Row()` inserts it. Pre- and
post-insert `paAuditRateRows_` must pass (a pre-audit failure REFUSES the insert), and the post check
requires rate rows **and** formula cells UNCHANGED — TNT1 adds a count row, not a pair. The unattended
refresh still never inserts a row.

**Which columns fill.** Values come from the normal refresh, into non-frozen columns only:
`_SHIP_2026-08-10` (age 7d, still script-owned) and each new cohort from `_SHIP_2026-08-17` on.
🔴 **Frozen/matured columns (age ≥ `PA_MATURITY_DAYS`) stay BLANK forever — never 0.** A 0 would
assert "no box arrived in ≤1 day that week"; we simply never measured it. Kurt-owned, not rewritten.

**Scope: top block only** (decided) — ⚠️ **SUPERSEDED FOR HUBS by D22b below** (Kurt asked for the hub
grain the same day). Carrier / State / Box remain top-block-only.

#### D22b — the SAME nested TNT1 row per HUB (Kurt 2026-08-17)

> Kurt, on a screenshot of the `By Hub (assigned)` block: **"we want tnt1 rows for these hubs too"**

Every group in `By Hub (assigned)` on **TnT2** gains `   {hub} · TNT1`, directly under `{hub} · 2 Day`
and above `{hub} · 3+ Day` — the same position, indent (3 leading spaces), separator (U+00B7) and
semantics as the top block: TNT ≤ 1 calendar day on the **D18 per-box pickup clock**, TNT 0 included,
a strict **SUBSET** of that hub's `2 Day`, summed into nothing.

- **`PA_ASSERT_TNT1_SUBSET` now runs at HUB grain too** (in `paValues_`, before any write). The
  top-block assert can hold while one hub's TNT1 exceeds its own `2 Day` — that would mean the clock
  or the hub attribution drifted *for that hub*, and the row would misrepresent that hub's headline.
- **Hub list comes from the SHEET, never a roster.** `paAddHubTnt1_` reads `paHubGroups_`, so the next
  new hub needs no code change: give it its 3 rows with `paAddHub_`, then re-run `addHubTnt1Rows()`.
  `RMFG choice (2+ hubs open)` is included (a real bucket of boxes); Indianapolis is included and its
  row simply stays empty — the hub is closed. **Blank ≠ 0**, as everywhere else.
- 🔴 **TnT2 ONLY.** Lost in Transit's grains are `Arrived` / `Not Arrived` — arrival measures with no
  transit-time meaning — so a TNT1 row there is a category error, not a missing feature.
- 🔴 **Zero-filled per hub, unlike its sibling dim rows.** `paValues_` emits `· 2 Day` / `· 3+ Day`
  only when non-zero, so a bucket that empties keeps LAST run's number on the sheet. TNT1 emits `0`
  for every hub that emitted a `· 2 Day` key (a TNT1 with no parent is meaningless, so the parent set
  is the honest denominator) and emits NOTHING for a hub with no 2-Day boxes at all.

🔴 **The risk is the INSERT, not the number** — ~6 inserts, each re-pointing every reference below it.
Three defences, all required:

1. **Bottom-up** (descending row order). Top-down would let the first insert invalidate every
   not-yet-processed row number and silently write a label into the wrong hub group.
2. **`paAuditRateRows_` before AND after**, reading **formula TEXT** — a re-pointed formula still
   *looks* right. A pre-existing mis-point REFUSES the whole run (`PA_INSERT_PRE_AUDIT_FAILED`); a
   regression after THROWS; rate-row count and formula-cell count must be **unchanged**
   (`PA_INSERT_ROW_COUNT`) because this adds COUNT rows, never a pair.
3. **Idempotent** — a group that already has its TNT1 row is skipped, so a re-run cannot double-insert.

**Two resolver consequences, both real bugs if skipped:**

- `paRatePairFor_`'s nested-row allowlist is now a PREDICATE (`paIsNested_`): exact match on the
  top-block labels **or** the explicit ` · TNT1` suffix. Still an allowlist — "anything that is not a
  grain row" stays rejected for the D19a reason (it lets the resolver stroll out of its own block).
- `paHubGroups_` no longer assumes the bad row sits **directly** below the good row. It is what
  `paInsertHubRows_` (adding the NEXT new hub) reads; a strict test would report **zero** groups and
  throw `PA_INSERT_NO_HUB_SECTION` on a perfectly healthy tab.

**Tool:** `previewAddHubTnt1Rows()` (writes nothing; logs each landing row and the BEFORE audit) then
`addHubTnt1Rows()`. Values arrive from the ordinary refresh; the unattended refresh still never
inserts a row (D13/D19).

**Verified offline** (label-column simulation of TnT2, real `paRatePairFor_` / `paHubGroups_` pulled
from the source): all 14 rate rows resolve to their own pair BEFORE and AFTER the insert, and all 7
hub groups parse with the TNT1 row present. ⚠️ **Not executed against the live sheet** — GAS cannot be
run from here; the pre/post audit inside `addHubTnt1Rows()` is the confirming run.

**Measured per hub (`_SHIP_2026-08-10`, cached snapshot, ZERO ParcelPanel calls):**

| hub | 2 Day | TNT1 | of which TNT0 | TNT1/2Day |
|---|---|---|---|---|
| Anaheim | 404 | 306 | 9 | 75.7% |
| Chicago | 288 | 143 | 23 | 49.7% |
| Dallas | 541 | 250 | 6 | 46.2% |
| Nashville | 412 | 154 | 4 | 37.4% |
| Swedesboro | 569 | 449 | 0 | 78.9% |
| RMFG choice (2+ hubs open) | 2 | 0 | 0 | 0.0% |
| **total** | **2,216** | **1,302** | **42** | 58.8% |

Indianapolis is absent (closed — zero boxes), so its row stays BLANK. The totals reproduce D22's
top-block figures exactly, which is the cross-check that the hub split is the same population.

**Carrier / State / Box — NOT done, Kurt's call.** Same one-line `paValues_` change plus rows, but the
row cost is ~4 carriers + ~45 states + 2 boxes ≈ **51 inserts on TnT2**, and mirroring it on Lost in
Transit would be a category error (see above). Recommendation: **Carrier yes if he wants it** (4 rows,
and "which carrier actually delivers next-day" is an operating question); **State no** (45 rows of a
thin denominator — most states carry too few boxes for a 1-day rate to mean anything); **Box no**
(size does not change transit time; it changes cost and hub choice, which are measured elsewhere).

**Measured at adoption (`_SHIP_2026-08-10`, cached snapshot, ZERO new ParcelPanel calls):**
cohort 2,318 · arrived 2,256 · 2 Day (TNT ≤ 2) 2,216 · **TNT1 (TNT ≤ 1) 1,302** — of which TNT 0 = 42.
TNT distribution: 0→42, 1→1,260, 2→914, 3→40.

### D23 — `Routing Match` IS WRITE-ONCE PER COHORT; IT DOES **NOT** FOLLOW THE 10-DAY MATURITY MODEL (Kurt 2026-08-17)

> Kurt, verbatim: *"for Routing match, let's do this walk forward or something 8/10 is already
> matured. we shouldn't refresh this."* … *"matured on the carrier end. Shopify had the wrong tags so
> the data is wrong."*

**The failure this prevents (negatives first).** `Routing Match` measures **routing TAG vs EXECUTED
CARRIER**. The executed carrier is settled at ship time — but the TAG is **mutable after ship**:
corrective retagging runs after the cohort goes out (`_SHIP_2026-08-10` alone logged **376 tag writes**
in `_outputs/logs/wk0810_corrective_delta.jsonl`), and RMFG / drift-in fixes land later still. So a
recompute on day 5 or day 9 compares day-0 actuals against tags that are **no longer what the engine
assigned at ship time**. Unlike the delivery tabs, this number **DEGRADES with age instead of
converging** — a later refresh is not a better reading, it is a corrupted one. Precedent: the
34 / 31 / 35 reconciliation in `_outputs/reports/HANDOFF-2026-08-07-reship-coordinator.md` — one rule
measured at three different times, giving three different answers. That drift IS this bug.

**Why this differs from D15.** TnT2 / Lost in Transit measure the box against the WORLD (did it
arrive, when). The world only gets *more* known with age, so re-running self-heals and the 10-day
window is correct there. Routing Match measures a **ship-time snapshot** against a mutable input, so
its only valid reading is the first one. `PA_MATURITY_DAYS` stays **10** and still governs TnT2 /
Lost in Transit **unchanged** — D23 is a per-tab rule, not a change to the maturity constant.

**The rule.**
1. A `Routing Match` cell holding a **MEASUREMENT** is **FROZEN** — never overwritten, never
   blanked-and-rewritten, regardless of column age.
2. A cell holding a **PLACEHOLDER** stays writable forever. Placeholder = blank, `n/a (immature)`
   (the `Routing Matched - Hub` row's deliberate state — hub actuals need carrier invoices, ~1wk lag),
   or `n/a` (nothing eligible to compare). Measurement = a number (these cells are percent-formatted,
   so `98.0%` lands as `0.98`) or a bare numeric/percent string. **Freezing on "non-empty" would nail
   the Hub row to `n/a (immature)` forever** — the exact opposite of what that placeholder means.
3. A new cohort column is written **once**, on the first run that can measure it.
4. Every run LOGS, per cell: written (with the prior placeholder) vs skipped-as-frozen (with the held
   value).
5. `PA_ASSERT_ROUTING_FROZEN` — a named throw, twice: pre-write (a measured cell may not be in the
   write set) and post-flush (no measured cell may have changed). It **refuses**, never repairs: the
   ship-time reading is unrecoverable once overwritten.

**Implementation:** `paRoutingIsMeasured_` (the placeholder/measurement predicate), `paColumnByKey_`
(reads the column keyed exactly as `paWriteOwned_` keys its writes, section-aware), and
`paWriteRoutingFrozen_`, which replaces the direct `paWriteOwned_` call on the routing tab in
`PivotAnalytics.gs`. `paCurrentCol_`'s D15 age gate is untouched — D23 sits INSIDE it, so a column
that D15 still considers script-owned (wk0810 at age 7d) is nonetheless left alone here.

**State at adoption:** `_SHIP_2026-07-13` 89.3% · `07-20` 91.6% · `07-27` 90.4% · `08-03` 98.00% ·
`08-10` 100.00% (Carrier row, all frozen). The Hub row is `n/a (immature)` on `07-27`/`08-03`/`08-10`
and remains writable.

**Open, NOT built (recommendation only):** stamping WHEN each column was measured. See "measured-at"
in the change-log entry below.

### Cutover checklist (once preconditions clear)

(a) confirm Jdbc `SELECT 1` from GAS + RO user scoped; (b) implement the walk-forward current-column
refresh (`PivotAnalytics.gs`) reading telemetry via Jdbc + Shopify for assigned tags/box/state;
(c) add a **Reship Report** menu item + reuse the hourly trigger; (d) **verify against the local
builder's numbers on a matured cohort** before trusting the headless path (identical figures, not
"it ran"). Deploy = same REST `updateContent`/`clasp push` path as `Code.gs`.

### D24 — `Notifications` tab: WHERE EACH ROW'S NUMBER COMES FROM (Kurt 2026-08-18)

Negatives first. Everything below was live-verified 2026-08-18; `appsscript/Notifications.gs` is the
implementation and carries the same rules in its header.

- 🔴 **`Order Placed` IS NOT KLAVIYO.** Kurt's ruling: the order-confirmation email is sent by
  **Shopify**. The live flow list agrees — 26 flows, 21 live / 5 draft, **0 archived**, and none is
  an order-placed flow, so this is a fact about the account and not a status-filtered listing. The
  row is read from **Shopify order events**: the `BasicEvent` message
  `"Order confirmation email was sent to <name> (<email>)."`. Never resolve this row from a Klaviyo
  name regex — there is nothing to match and a regex would silently bind to a different stage.
- 🔴 **That row is at ORDER grain; the Klaviyo rows are at DISTINCT-PROFILE grain.** They are not
  reconcilable line-for-line and the gap is the repeat-customer count. Do not "fix" a difference.
- 🔴 **`events()` on an order must be asked for `sortKey: CREATED_AT, reverse: false`.** The default
  returns NEWEST first, so a small page on a months-old subscription order fills with August
  fulfillment chatter and the June confirmation event falls off the end. That defect measured
  **271/400 (68%)** and looked like a deliverability problem; ascending gives **397/400**.
- 🔴 **The match regex is anchored** (`/^order confirmation email was sent/i`). Unanchored, it also
  catches `"Confirmation #ABC was generated…"` and the RMFG app's
  `"… sent a shipping confirmation email to …"` — the latter belongs to the SHIPPED stage.
- 🔴 **`Order Shipped` / `Order Delivered` ARE Klaviyo, with the flows PINNED IN CODE** —
  `XYFE5N` *Shipping Notification - In Transit (Parcel Panel)* and `Tu67r6` *Shipping Notification -
  Delivered (Shopify)*. Script properties `KLAVIYO_FLOW_SHIPPED` / `KLAVIYO_FLOW_DELIVERED` override.
  **The name regex was REMOVED**: `/ship(ped|ping|ment)/` matches four live flows and `/deliver/`
  matches both the Delivered and the Out-for-Delivery flow — a coin flip dressed as a resolution.
- 🔴 **`VC8dJp` *Out for Delivery (Shopify)* gets NO row** (Kurt's call). Its counts are LOGGED only.
  Measured: **zero sends of any channel in August**, so it is live but silent.
- 🔴 **THE METRIC NAMES ARE ACCOUNT-SPECIFIC AND THE OBVIOUS GUESS IS WRONG.** This account has **no
  `Received SMS` and no `Clicked SMS`** — the first version of the file looked for both and would
  have written a silent 0 on every SMS row. Correct: `SMS Sent` = **`Received Text Message`**
  (delivered to the handset) — *not* `Sent Text Message`, which is dispatch and here counts only
  1,479 events vs 17,922, so it is not even the superset it sounds like. `SMS Engaged` =
  **`Clicked Text Message`**, **clicks only**: Klaviyo publishes no reply/inbound metric, so this row
  UNDERSTATES engagement and must never be read as "responded".
- 🔴 **The two `Email Sent` rows for Shipped/Delivered cannot be filled from Apps Script.**
  `/events/` has no flow filter, so one flow's email count means paging every `Received Email` event:
  **206,381 events / 28d ≈ 1,032 pages at ~3.0s/page ≈ 52 minutes** against a 360s ceiling. The SMS
  metrics (90 and 48 pages) do fit. `ntMetricVolume_` prechecks with one `/metric-aggregates/` POST
  and DECLINES an impossible sweep instantly instead of burning four minutes to fail. **Raising
  `NT_MAX_PAGES` does not fix this.** If Kurt wants those rows: a job without the 360s ceiling, or
  Klaviyo's flow-series report — which is **account-wide per week, NOT cohort-joined**, and must
  never be written into a cohort column as though it were.

**Measured 2026-08-18** (sends never exceed cohort size — the sanity gate passes):

| | `_SHIP_2026-08-17` (2324 orders / 2289 emails) | `_SHIP_2026-08-10` (2316 / 2304) |
|---|---|---|
| Order Placed — Email Sent | **2291** (98.58%) | **2285** (98.66%) |
| Order Shipped — SMS Sent / Engaged | **657 / 222** | **938 / 354** |
| Order Delivered — SMS Sent / Engaged | **209 / 64** (cohort 1 day old) | **954 / 290** |
| Order Shipped/Delivered — Email Sent | BLANK (sweep declined) | BLANK (sweep declined) |

Cross-check vs Klaviyo's own flow-series report (account-wide, weekly): In-Transit SMS delivered
**670** in the week of 08-17 vs our **657** in-cohort, and **969** in the week of 08-10 vs our
**938** — the flow mapping is right.

## Non-goals

- Not a refund tracker (refund requests ≠ reship issues — [[feedback_refund_not_issue]]).
- Not the weekly shipping issue report (that stays per `~/.knowledge/ops/Weekly Shipping Issue Report.md`); the issue table here reuses its format, not its scope.
- No auto-tag cleanup in Gorgias (Demi's work item); no writes to Shopify/Gorgias ever.

## Change log

- 2026-07-09 — initial draft (Claude, from Kurt/Dan/Jessa Slack thread C0A6185SY0Z + 7/08 session findings). Awaiting Kurt approval.
- 2026-07-13 — headless port to the pivot sheet (R15–R17); local schtask disabled.
- 2026-08-06 — spec'd the walk-forward headless refresh for the three cohort-analytics tabs (TnT2,
  Lost in Transit, Routing Match): locked definitions, walk-forward rules A1–A5 (freeze matured /
  refresh current column only / MySQL-not-PP-not-CSV / provisional current / blank≠zero), JDBC
  mechanism, and the two-part precondition BLOCKER (scheduled cloud owner for shipments+delivery_status
  — today manual/stale — and DO firewall allowlist + RO user for Apps Script Jdbc). Doc-before-code
  gate; no GAS wired yet (blocked on the ingest epic).
- 2026-07-27 — added "Current shipped state" section: canonical = pivot sheet, Product Mix
  (Reship/Unresolved/Potential/Actual), Reship (ex-Product Mix (T)) transpose + By Issue/Carrier/State breakdowns
  (%+discrete), Parcel Panel carrier (Script Property `PARCELPANEL_API_KEY`), Reship Report custom
  menu, and the two post-classification overrides (expedite-request guard `c495842`, late-supersedes-
  warm `a20950c`). SSOT now matches the live `Code.gs` + `scratchpad/rebuild_mix_triage.py`.
- 2026-08-14 — **D18: the 2-day promise clock is PER BOX, from that box's own pickup** (Kurt: "This
  number is wrong. 55 from Monday pickup, reviewing Tuesday pickup"). Multi-leg ship weeks are
  standard; the cohort-Monday clock aged every Tuesday-leg box by a day and flipped it to late, and
  it counted never-picked-up boxes as late against a promise the carrier never started (they are
  `pending`). Pickup authority = PP `pickup_date`, Shopify first movement scan only when PP is
  absent, ET before any date math. `PA_ASSERT_PENDING_SUBSET` rebounded to `pending <= Not Arrived`.
  Also recorded: the live Apps Script project was found holding ONLY `[appsscript, Code]` —
  `Exceptions.gs` and `PivotAnalytics.gs` had been DELETED by an all-files PUT (gotcha #16 executed);
  both restored, Exceptions from its last-deployed bytes.
- 2026-08-17 — **D20: the ship-day guard skips the CURRENT LEG, not the invocation** (observed live
  Monday 08-17: the run logged the age-0 skip for `_SHIP_2026-08-17` and exited, so `_SHIP_2026-08-10`
  at age 7d — still script-owned inside `PA_MATURITY_DAYS` — never reconciled; every Monday silently
  froze the previous week's column for a day). **D20b: the note-column decision — keep
  `PA_ASSERT_HEADERLESS_COLUMN` strict, make the refusal LOUD** via a `slack_` DM on any throw from
  `refreshCurrentColumn` plus the previous-leg `catch`; content-heuristic and reserved-column options
  rejected with reasons. **D21: "poll only the undelivered" — design accepted, implementation HELD**
  pending a cached-vs-fresh parity run (verdict tab `_pa_verdicts`, key `shipWeek||order_number`,
  terminal-only caching, schema-version drop, mandatory weekly full recount).
- 2026-08-17 — **D22: `TNT1` row on TnT2** (Kurt: "I want to get TNT0 and TNT1 shipments in there
  (just label the row tnt1)"). TNT ≤ 1 calendar day on the D18 per-box clock, TNT 0 included, nested
  UNDER `2 Day Shipments` as a strict subset (`2 Day` unchanged at TNT ≤ 2; `PA_ASSERT_TNT1_SUBSET`).
  Human-invoked `previewAddTnt1Row` / `addTnt1Row` with the rate-row verifier on both sides;
  `paRatePairFor_` extended to walk past nested rows BETWEEN the good and bad rows (allowlisted
  labels only). Top block only. Frozen columns stay blank. wk0810: TNT1 = 1,302 of 2,216 2-Day.
- 2026-08-17 — **D22b: the same TNT1 row per HUB on TnT2** (Kurt, on the By Hub screenshot: "we want
  tnt1 rows for these hubs too"). Hub list read from the sheet (generic over hub name);
  `PA_ASSERT_TNT1_SUBSET` extended to hub grain; nested-row allowlist became the `paIsNested_`
  predicate (` · TNT1` suffix) and `paHubGroups_` learned that a group may be 4 rows, without which
  the NEXT hub's insert would throw `PA_INSERT_NO_HUB_SECTION`. Human-invoked
  `previewAddHubTnt1Rows` / `addHubTnt1Rows`, bottom-up, idempotent, rate-row audit both sides.
  Carrier/State/Box deliberately NOT inserted (~51 rows) — Kurt's call.
- 2026-08-17 — **D23: `Routing Match` is WRITE-ONCE per cohort** (Kurt: "for Routing match, let's do
  this walk forward or something 8/10 is already matured. we shouldn't refresh this." … "matured on
  the carrier end. Shopify had the wrong tags so the data is wrong."). Tag-vs-executed-carrier is a
  ship-time snapshot measured against a MUTABLE input (376 corrective tag writes on wk0810 alone), so
  it degrades with age rather than converging — the opposite of the delivery tabs. First measurement
  wins forever; `PA_MATURITY_DAYS` stays 10 for TnT2 / Lost in Transit. Placeholders (`blank`,
  `n/a (immature)`, `n/a`) stay writable so the Hub row cannot freeze as a placeholder.
  `PA_ASSERT_ROUTING_FROZEN` refuses pre-write and post-flush. **Measured-at timestamp: recommended,
  NOT built** — recommendation is ONE footnote row at the bottom of the tab, `Measured at (ship-time
  snapshot)`, filled with the same write-once rule, rather than per-cell notes (invisible until
  hovered, lost on copy) or a per-column extra row (doubles the tab's height). It needs a human-invoked
  row insert (D19: the refresh never adds rows) and Kurt's approval, so it is parked as a one-row ask.

### D25 — A TRANSIENT `Address unavailable` MUST NOT KILL A RUN; RETRY THE CONNECTION CLASS ONLY (Kurt 2026-08-19)

**The burn.** 2026-08-19, Kurt's Slack:

```
4:09 AM  Reship report (Apps Script) FAILED: Exception: Address unavailable: https://504ac4.myshopify.com/admin/api/2026-04/graphql.json
5:09 AM  (same)
```

The 08:08 run succeeded with no code change (`Triage` stamp `REFRESHED 2026-08-19T08:08:41`), and
Google's own failure digest confirms the same class on 8/16 04:08 for `refresh`. This is
Google-to-Shopify transient unreachability, **not a code fault** — but nothing retried, so one blip
killed a whole run, alarmed Kurt, and cost an hour of coverage.

🔴 **`Address unavailable` is NOT an HTTP status.** `UrlFetchApp.fetch` *throws* it when Google
cannot reach the host at all, so `muteHttpExceptions: true` never sees it and every
`getResponseCode()` check downstream is skipped. Any retry written as a response-code check is
therefore blind to the exact failure it was written for — it has to be a `try`/`catch` around the
fetch itself.

**`netFetch_(url, params, tag)` in `Code.gs`** — drop-in for `UrlFetchApp.fetch`, 3 attempts,
~1s/2s/4s plus jitter. Wired into the single-fetch helpers only: `shopifyGql_`, `gorgiasGet_`,
`slack_`, the Slack `conversations.history` pull (Code.gs), `excSlackPost_` + `excSlackHealth_`
(Exceptions.gs), `ntGet_` + `ntMetricVolume_` (Notifications.gs).

🔴 **CONNECTION CLASS ONLY — a 4xx must still fail loudly and immediately.** Retryable = a thrown
connection error (`Address unavailable`, DNS, timeout, connection reset/refused) **or** HTTP 429 /
5xx. A 400/401/403 is a real fault — a broken token or a malformed query — and retrying it buries
the diagnosis under 7 seconds of silence. `netRetryable_` reads the HTTP code out of the message
FIRST and lets it decide, so a 400 whose *response body* happens to contain the word "unavailable"
can never be mistaken for a connection failure.

🔴 **Every retry is LOGGED** (`net retry N/2: ... on <label>`), URL query-string stripped so a token
in a query param cannot reach the log. A flaky window must be visible afterwards, not silently
smoothed over — the alert to Slack now fires only once retries are exhausted, so a real outage
still shouts and a blip no longer does.

**NOT applied to `UrlFetchApp.fetchAll` call sites** (Code.gs:760, Exceptions.gs `excPpFetch_`,
PivotAnalytics.gs:337) — a different API with per-request results, and `excPpFetch_` already
carries its own throttle-aware backoff. Wrapping those means restructuring; do it deliberately, in
its own change, or not at all.

### D26 — A TRIAGE DECISION IS A CLOSED VOCABULARY, AND `no action` / `cs error` ARE NOT SHIPPING FAILURES (Kurt 2026-08-19)

**The failure this prevents:** a bad CUSTOMER-SERVICE call — CS reshipped or credited when nothing
was wrong with the shipment — counted as a reship and **overstated shipping failure**. Kurt:
*"it was customer service making the wrong call … make sure its not counted."*

**Second failure this prevents:** before today, **ANY** non-blank string in Triage col H suppressed
the row. A typo (`no acton`, `resip`) therefore **silently deleted a live failure** from the
unresolved count — an under-count with no signal anywhere.

**Rules.**
1. Col H accepts a CLOSED vocabulary, case-insensitive, whitespace/underscore-normalized
   (`normalizeTriageDecision_`, Code.gs). Canonical values and their accepted synonyms:
   - `reship` ← reship · reships · re ship · re-ship · reshipped · reship sent
   - `refund` ← refund · refunded · refund issued
   - `no action` ← no action · noaction · no-action · none · na · n/a
   - `cs error` ← cs error · cs · cs mistake · cs-error · cserror · cs fault · cs issue ·
     customer service error · customer service mistake
2. **Unrecognized text is NOT a decision.** The row stays ACTIVE (still counted), and the run posts
   a Slack `:warning:` naming the key and the text. Never silently suppress.
3. **Counting.** Every reship/refund figure reaches Triage only through the UNRESOLVED path, and a
   recognized decision removes the row from the tab, hence from all of it:
   - `Product Mix` `Regular/Medium/Large Box Unresolved` (F/K/P) = COUNTIFS over `Triage`!E/F
   - `Product Mix` `Potential Reship` (R) = reships + those unresolved
   - `Reship` tab (ex-`Product Mix (T)`) = transpose of `Product Mix` — inherits both
   - `Triage` col J/K "Unresolved reships by ship week" = the active entries only
   - `Product Mix` `Actual Reship` (T) = COUNTIFS over `Raw Data` — **real reship orders, never
     fed by col H**, so a `cs error` can never inflate it. Do not "fix" this by wiring H into it.
4. `no action` and `cs error` are resolutions that are **NOT shipping failures** and must never be
   counted as a reship or refund anywhere. `reship` / `refund` keep today's behavior.
5. **`cs error` is tallied on its own** so the CS-error rate is visible rather than merely invisible:
   `Triage` col J/K, below the Total row — per-value counts, resolved total, and
   `CS error rate (of resolved)`. **No new tab** (Kurt).
6. **Persistence** stays the existing hidden `_triage_decisions` tab (the mechanism that already
   survives every refresh incl. the walk-forward freeze). Schema widened to
   `A=key B=decision C=ship week D=issue E=decided-at`; 2-column legacy rows still load.
7. **Walk-forward is asserted, not assumed:** `assertTriageOut_` throws BEFORE any write if a
   resolved key was re-added to the active list, if a row is not 11 wide, or if the A/H header
   labels moved.
8. **Dry run:** menu → *Preview Triage decisions (writes NOTHING)* shows what would be removed,
   the tally, and any unrecognized text, without touching the sheet. Run it before a refresh.

### D27 — `still moving (4+ days)` SPLITS ON EACH BOX'S OWN PROMISE, NOT ON A CALENDAR DAY COUNT (Kurt 2026-08-19)

**AMENDS D16.** The observation block under `3+ Day Shipments` goes from THREE rows to FOUR.

Kurt, reading `_SHIP_2026-08-17` on a **Wednesday**, verbatim:

> **"1396 4+ days still moving is misleading. especially with some pickups on tuesday"**
> **"also, its only wednesday"**
> **"Still Moving =<TNT2 and Still Moving >TNT2"**

**The failure this fixes (negatives first).** `still moving (4+ days)` hardcoded a **fixed calendar
day count** into a row label. That count was coined when the only cohort on screen was old, and it is
wrong in two independent ways the moment it is read mid-week:

- **A ship week is MULTI-LEG.** A Tuesday (Dallas) pickup is standard here. "4+ days" describes
  cohort age, which for a Tuesday-leg box is not that box's transit at all — the same mistake D18
  already fixed on the headline and which survived in this label.
- **It is read before the cohort is old.** Two days into `_SHIP_2026-08-17` the row measured **1,251**
  (Kurt saw 1,396 on an earlier read the same day) — boxes almost entirely in **ordinary transit,
  inside their own 2-day promise**. Rendering that as a single alarming number is how a report trains
  its reader to ignore it.

**The model (four rows, on-sheet order):**

```
3+ Day Shipments
   Still Moving =< TNT2              undelivered, movement scan <24h, INSIDE its own 2-day promise
   Still Moving > TNT2               undelivered, movement scan <24h, PAST its own 2-day promise
   no scan in 24h+ (investigating)   unchanged (D16)
   never picked up by carrier        unchanged (D16)
```

- **The clock is the D18 per-box clock**, never cohort age and never a calendar day count: deadline =
  **that box's own pickup + 2 days (ET)**, pickup from ParcelPanel `pickup_date` (canonical) with the
  Shopify first-movement scan as the fallback ONLY when PP has none.
- **Kurt's spelling `=<` is his label text and is deliberate. Do not "correct" it to `<=`.**

**PRECEDENCE — first match wins, which is what makes the four mutually exclusive:**

1. a real carrier scan within the last 24h (`active`) → a **Still Moving** row, split by that box's
   own deadline: inside it (`pending`) = `=< TNT2`, past it (`late`) = `> TNT2`;
2. otherwise → `no scan in 24h+` or `never picked up`, by whether the box was ever picked up.

So **a box silent >=24h NEVER appears on a Still Moving row**, however new the cohort is. The two D16
rows keep their definitions and **win**.

**PARTITION + MONOTONICITY (D16, extended).** `PA_ASSERT_OBSERVATION_PARTITION` now requires the
**four** rows to sum to `Not Arrived`, and the refuse-to-write-on-rise gate is unchanged: the SUM is
monotone non-increasing within a cohort — a box leaves only by DELIVERING, and delivered cannot
un-deliver. Migration *between* rows is expected and logged (a box crossing its own deadline moves
`=< TNT2` → `> TNT2` with the sum unchanged). While a sheet still carries the old three-row shape the
gate logs "partial prior row set — first write, gate skipped" rather than comparing against a baseline
that means nothing.

**RECONCILIATION AGAINST THE HEADLINE — asserted, not assumed** (`PA_ASSERT_MOVING_OUT_SUBSET`,
`PA_ASSERT_MOVING_IN_SUBSET`), verified on measured wk0817 and wk0810 before shipping:

- `Still Moving > TNT2` is undelivered and past its own deadline → a **subset of the late-undelivered
  population already counted in `3+ Day Shipments`**.
- `Still Moving =< TNT2` is undelivered and inside its deadline → a **subset of `pending`** (D18).
- 🔴 **What is NOT true, and was checked rather than assumed:** `no scan in 24h+` is *not* a subset of
  late. On `_SHIP_2026-08-17` all 12 no-scan boxes were still inside their own promise while
  late-undelivered was 0. Late-undelivered therefore equals `Still Moving > TNT2` **plus the late
  part** of the no-scan / never-picked rows — never those whole rows.

**MECHANICS — one relabel + one insert, D19/D19a discipline** (`previewSplitStillMoving` writes
nothing; `splitStillMovingRows` applies it; idempotent):

- **Pre-audit both tabs or refuse** (`PA_INSERT_PRE_AUDIT_FAILED`); **post-audit both tabs from
  FORMULA TEXT** — a re-pointed formula still LOOKS right. Rate-row and formula-cell counts must be
  UNCHANGED: this adds a COUNT row, never a pair.
- The **relabel happens first** (it shifts nothing), then the single insert lands directly beneath it —
  the bottom-up rule is satisfied trivially. Formats and indent are cloned from the surviving sibling
  observation row (`no scan in 24h+`), never from a guessed space count.
- `paIsNested_` gained the `Still Moving ` stem so the label-based pair resolver still walks past both
  rows. It stays an **allowlist** (D19a) — the stem, not "anything that isn't a grain row". Labels
  reaching that predicate are already trimmed, so the stem is a PREFIX test. Verified offline against
  the real resolver pulled out of the deployed file: 12 rate rows on each tab resolve their own pair,
  before and after the split.
- **The inserted row stays BLANK in every existing column, never 0** — those weeks predate the split
  and a 0 would assert we measured `> TNT2` then. Values arrive only from the ordinary refresh into
  non-frozen columns.
- 🔴 **The relabelled row's existing history is NOT touched.** Those frozen numbers are the *unsplit*
  total now sitting under the `=< TNT2` label. Blanking a frozen cell is Kurt-owned (D15), so the
  preview PRINTS them and leaves the call to him.

**Measured before the click** (read-only mirror `scratchpad/split_stillmoving.py`, 0 ParcelPanel calls
— wk0817 is skipped by the age guard, wk0810 came from the existing local cache):

| cohort | age | Not Arrived | `=< TNT2` | `> TNT2` | no scan 24h+ | never picked up |
|---|---|---|---|---|---|---|
| `_SHIP_2026-08-17` | 2d | 1,276 | **1,251** | **0** | 12 | 13 |
| `_SHIP_2026-08-10` | 9d | 16 | **0** | **0** | 16 | 0 |

That is the whole point of the split: the 1,251 that read as catastrophe are boxes inside their own
promise, and **zero** boxes on either cohort are actually still moving past their promise.

⏱️ `_SHIP_2026-08-10` freezes at age 10 on **2026-08-20** (D15). If the split is to appear in that
column at all, the insert and a refresh must happen **before then**; after that the column is
Kurt-owned forever and the new row stays blank there.
