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
- **Product Mix (T)** — transpose (metrics as rows, cohorts as columns; A1 = "Ship Week"). Below the
  metrics, grouped breakdowns **By Issue · By Carrier · Arrived Warm by State · Delayed by State**, each
  emitted TWICE: a `── … (%) ──` section (÷ cohort size) then a discrete-count section. Built AFTER
  writeTriage_ (with `flush()`) so its Unresolved/Potential reflect the current Triage, not last cycle.
- **Daily** — separate sheet `1VHzlyvFabVYUGpR71tgJfYDglI85KnCQOCYJFyZvGsI`.

**Tool menu (this is the "tool"):** `onOpen` builds a **Reship Report** custom menu on the sheet →
*Refresh now (full)* · *Refresh Product Mix + (T)* · *Refresh Triage only* · *Refresh Daily* ·
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
| **Mon** (ship day) | 0 | **exits** — one log line, no writes, no fetch |
| **Tue** (first real run) | 1 | Shopify-only; appends the new `_SHIP_` column |
| **Wed** | 2 | Shopify-only |
| **Thu–Sun** | 3+ | Shopify **+ PP rescue**, ≤200 calls/run, oldest-scan first |

The trigger UI cannot express any of the following, so all four live in `refreshCurrentColumn`:

- **Ship-day skip.** Cohort age `0` → log one line and exit. On a daily trigger the Monday run
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

### D16 — THREE-ROW model under `3+ Day`, and the monotonicity invariant (Kurt 2026-08-07)

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

### Cutover checklist (once preconditions clear)

(a) confirm Jdbc `SELECT 1` from GAS + RO user scoped; (b) implement the walk-forward current-column
refresh (`PivotAnalytics.gs`) reading telemetry via Jdbc + Shopify for assigned tags/box/state;
(c) add a **Reship Report** menu item + reuse the hourly trigger; (d) **verify against the local
builder's numbers on a matured cohort** before trusting the headless path (identical figures, not
"it ran"). Deploy = same REST `updateContent`/`clasp push` path as `Code.gs`.

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
  (Reship/Unresolved/Potential/Actual), Product Mix (T) transpose + By Issue/Carrier/State breakdowns
  (%+discrete), Parcel Panel carrier (Script Property `PARCELPANEL_API_KEY`), Reship Report custom
  menu, and the two post-classification overrides (expedite-request guard `c495842`, late-supersedes-
  warm `a20950c`). SSOT now matches the live `Code.gs` + `scratchpad/rebuild_mix_triage.py`.
