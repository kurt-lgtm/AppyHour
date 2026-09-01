# RESHIP_REPORT_RULES.md — Reship Tracking Report (SSOT)

**Single source of truth for the durable reship report — change rules HERE first.**
🔴 **PRE-CHANGE GATE:** read this doc before ANY change to the report, its refresh script, or its sheet. Code that contradicts a rule here does not ship without updating this doc in the same commit.

> **STATUS: APPROVED by Kurt 2026-07-09** (North Star confirmed; denominator = live Shopify tag count excl. cancelled).

> 📖 **Per-tab reader's guide:** [`TAB_NORTH_STARS.md`](TAB_NORTH_STARS.md) — one NORTH STAR + gotchas section per visible tab of the Running Reship sheet (cites the rules here; update it when a cited rule changes).

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
hourly time-trigger on Kurt's account. Source of truth = `ShippingReports/appsscript/*.gs`.

🔴 **Deploy = `appsscript/gas_swap.py` — NEVER `clasp push` (and never `clasp push -f`).** The REST
`projects/{id}/content` PUT **replaces the whole file set**, so a push carrying a subset DELETES
every file it omitted. `.claspignore` here is a two-file allow-list (`**/**`, then `!Code.gs`,
`!appsscript.json`), so `clasp status` reports exactly `[appsscript.json, Code.gs]` as the push
payload — on 2026-08-14 that push deleted `Exceptions.gs` and `PivotAnalytics.gs` from the live
project. `gas_swap.py` is the gate: it GETs live, swaps in only the files you name, then after the
PUT re-reads live and asserts the file SET is unchanged and every untouched file came back
byte-identical. Adding a file the live project lacks is a CREATE and needs `--allow-create` by hand.

```bash
cd "C:\Users\Work\Claude Projects\AppyHour\ShippingReports\appsscript" && python gas_swap.py get
```

- `get` — read-only. Lists every live file with `live=`/`local=` sha + SAME/DIFF, and snapshots live
  into `appsscript/live/` (gitignored + claspignored; regenerable, never source, never edit).
- `diff <Name>` — read-only unified diff, live vs local, one file, no extension (`Notifications`).
- `push <Name> [<Name2> ...]` — deploy those files; everything else preserved byte-for-byte.
  `push <Name>=<path>` pins the bytes to an explicit file (restore from a snapshot, not from a
  worktree that may hold uncommitted work). `--allow-create` is the ONLY way to add a file absent
  from live.

Creds: reads `~/.clasprc.json` and refreshes the token in place — never copy or commit that file.
`gas_swap.py` lives beside `.clasp.json` and hardcodes the matching `SCRIPT_ID`/`SRC`; clasp ignores
it (verified via `clasp status`), so its presence beside the `.gs` files cannot widen a push.

Local `scratchpad/rebuild_mix_triage.py` = the immediate manual mirror (reads `shipments.db` for carrier/transit); GAS is authoritative, the two stay in PARITY.

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
  Apps Script's own failure emails stay on as backup.
  🔴 **Destination (directive P8, `EXCEPTIONS_ALERT_RULES.md` — SSOT for the routing rule).** As
  built this does NOT use the webhook: `slack_` posts to the **appyhour-ops-reader DM**
  (`U08R19137UL` → `D0BG1541F0A`) via `chat.postMessage`, never the public `SLACK_WEBHOOK`
  (#reships) and **never #exceptions** (`C0BLKKPAW8P` — Dan's customer-ping channel; infra noise
  there gets it muted, and a muted #exceptions makes every real box problem silent). Every `slack_`
  caller in `Code.gs`/`PivotAnalytics.gs` is ops-class: FAILED run, empty Raw Data, ghost-tab
  creation, unrecognized Triage decision, breach warning, `PA_ASSERT_*` refusal. 🔴 **`Code.gs` has
  no ping-class helper and must never get one** — it must have no way to reach #exceptions.
  Destination resolves through `excChannelOps_()` (Exceptions.gs) behind a `typeof` guard, so
  Script Property `EXC_CHANNEL_OPS` re-routes both files without a code push, and a project that
  has lost `Exceptions.gs` still alerts. Unset → `KURT_SLACK_ID`, today's behavior. Freshness cell `Summary!A1` timestamp
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
| `PA_ASSERT_SECTION_SUM` | every `By X` block sums to the headline it breaks down (**D34** — runs on the sheet AFTER the write, never before) |

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
  <3d)`. 🗑️ **The budget half of this rule is DELETED by P12 (2026-08-20).** The
  "2,500 calls/week" it rationed against never existed — that number is Kurt's average weekly
  ORDER count misread as an API budget; the real limit is **120 requests/minute per API key**
  (`x-ratelimit-limit`), ≈ 1.2M/week. A ~2,300-box Tuesday cohort is **23 minutes** of paced
  fetching, not a week's allowance. ✅ **The age gate itself SURVIVES on its own merits:** at
  <3d nothing has been scanned yet, so PP has nothing to add — it is work avoidance, not
  rationing, and no box goes unchecked because of it. Deliveries stream via Shopify fine mid-week
  and PP reconciles later.
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

> 🔴 **SCOPE NARROWED BY [D32](#d32--maturity-is-kurts-delivery-sla-ship--4--friday-and-the-measurement-window-closes-there-kurt-2026-08-20) (2026-08-20): this 10 is PivotAnalytics' number, and the
> `Notifications` tab NO LONGER SHARES IT.** `NT_MATURITY_DAYS = 4` — Kurt's delivery SLA (ship
> Monday → due delivered Friday), not a reconciliation window. Copying the 10 across was the mistake
> D32 corrects. The 10 still governs TnT2 / Lost in Transit / `arrived` here, and D32 explains why
> the two tabs legitimately differ: a late DELIVERY self-heals into a column, a late NOTIFICATION is
> a failure. `Notifications!Arrived` is mirrored from `Lost in Transit`, so it keeps following THIS
> 10-day clock (`NT_MIRROR_MATURITY_DAYS`) even though the rest of its column freezes at 4.

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

> 🔴 **AMENDED BY [D36](#d36--routing-match-a-fence-is-not-a-prediction--fenced-boxes-leave-both-sides-of-the-rate-kurt-2026-08-26) (2026-08-26): fences are EXCLUDED from the Carrier match from
> `_SHIP_2026-08-24` forward** (bare `!ANY - <Hub>` was being scored as a permanent mismatch — a
> category error). The wk0824 cell was re-measured under the new basis as the ONE Kurt-directed
> exception to the freeze, with the old reading (69.40%) reproduced and recorded in D36 first.
> Columns ≤ 08-17 keep their old-basis readings frozen; a `Carrier n (committed / fenced)` row now
> carries the denominator. Everything else in D23 stands unchanged.

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
"it ran"). Deploy = the same gated `gas_swap.py push` path as `Code.gs` (see "Current shipped state"; never `clasp push`).

### D24 — `Notifications` tab: WHERE EACH ROW'S NUMBER COMES FROM (Kurt 2026-08-18)

Negatives first. Everything below was live-verified 2026-08-18; `appsscript/Notifications.gs` is the
implementation and carries the same rules in its header.

> 🔴 **AMENDED BY [D32](#d32--maturity-is-kurts-delivery-sla-ship--4--friday-and-the-measurement-window-closes-there-kurt-2026-08-20) (2026-08-20): every row below is now measured to a
> DEADLINE.** The Klaviyo windows close at ship + 4 days (the Friday), not ship + 12. Where this
> section describes a row's source that is unchanged; where it implies a row counts every send that
> ever arrived, it does not — a send after the Friday is a LATE BOX and is excluded and logged.
>
> 🔴 **AMENDED BY [D29c](#d29c--total-shipments-and-arrived-are-script-owned-kurt-2026-08-19) (2026-08-19):
> `Total Shipments` and `Arrived` are SCRIPT-OWNED.** Where this section and the file header called
> them "NOT OURS / owned elsewhere / read-only here", that is **superseded** — Kurt handed both rows to
> the script. Everything else in D24 stands unchanged.

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
- 🔴 **SUPERSEDED FOR THE SHIPPED ROW BY [D30](#d30--source-of-truth-for-each-email-sent-row-and-the-filter-that-hid-the-shipping-event-kurt-2026-08-19):**
  `Order Shipped → Email Sent` is now sourced from a **Shopify order event** and is written
  (2,323 of 2,324 on wk0817). Only `Order Delivered → Email Sent` still waits on Klaviyo. The
  bullet below remains true of the DELIVERED row only.
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

### D28 — 🗑️ DELETED (was: `ppLookup_` REPORTS ITS PARCELPANEL SPEND TO THE SHARED LEDGER)

🔴 **SUPERSEDED BY [`EXCEPTIONS_ALERT_RULES.md`](EXCEPTIONS_ALERT_RULES.md) DIRECTIVE P12/P13
(Kurt GO 2026-08-20). Read those before touching any ParcelPanel call path in this project.**

**Why it is gone: the premise was false.** D28 metered `ppLookup_` against a **"2,500 calls/week,
ACCOUNT-WIDE"** ParcelPanel quota. That number is Kurt's average weekly **ORDER count** (the
10,000/month plan quota ÷ 4), misread as an API request budget. ParcelPanel's plan quota counts
**orders tracked** — *"1 order = 1 quota"*, *"Order lookups do not consume quota … unlimited
lookups"*. The only real limit is **120 requests per minute per API key**, ~1.2M/week, and it is
reported in `x-ratelimit-limit` / `x-ratelimit-remaining` on **every response**.

**What that means for the reship report specifically:** `ppLookup_`'s measured draw of ~4,141
calls/week — the number D28's ledger surfaced, and the one that got it branded the consumer that
"starved" the exceptions sweep — is **0.35% of what the account serves.** It never starved anything.
What starved the sweep was `excBudgetTake_` subtracting from a phantom balance.

🔴 **DELETED here:** the `excBudgetCharge_(charged, 'rpt')` call and the `EXC_PP_RPT_ALLOC = 200`
allocation it was measured against. There is no ledger, no allocation, and no consumer that yields.

🔴 **KEPT, and re-justified so nobody deletes it as budget scaffolding:** the **F1 `_pp_cache`**
(directive P11, `_pp_cache` tab, `PP_CACHE_GIVEUP_DAYS = 21`). Its argument never depended on the
budget:

1. **The terminal memo.** `transit_days` is set only for a **DELIVERED** box, and a delivered box's
   pickup→delivery span and its carrier are **immutable**. Re-asking can only ever return the
   identical answer. 🔴 **Re-buying a fact that cannot change is waste at any price** — it was waste
   when we thought calls were scarce and it is waste now that they are free, because it also costs
   wall-clock time inside a 6-minute execution ceiling that *is* genuinely scarce.
2. **The 21-day give-up.** 169 boxes aged **45–52 days**, never delivered, were being re-fetched
   **24×/day forever**. An order past the report's own 3-cohort window that still has no transit is
   never going to get one. Same class as the P9 quarantine, on the report side. It fires only after
   at least one real fetch, so the carrier we display is captured before the order retires and
   nothing blanks.

**What replaces the metering:**

- **The limiter.** `ppLookup_` no longer fires `UrlFetchApp.fetchAll(slice(i, i + 50))` with no
  pause. 🔴 That 50-wide burst was the actual bug — 42% of a minute's allowance dispatched in one
  instant against a 120/min bucket — and `Exceptions.gs` had documented the identical failure
  (*"batches of 50 with no pause and no retry: 780 of 900 fetches failed"*) long before this call
  site was written. It now paces **batch 10 per 6.0 s cycle ≈ 100 req/min**, measured from the start
  of the fetch so the fetch's own duration counts against the cycle.
- **No dropped 429s (P13).** `if (code === 429 || code === 503) return;` silently left that order's
  carrier and `transit_days` missing from Dan's column for the run. A refused request is **not an
  answer**: it is retried in-run (`Retry-After`, else 2/4/8/16/32 s jittered, 5 attempts), and if the
  retries are exhausted the order is **left unstamped in the cache** so the next run asks again.
  🔴 Never stamp `asked` on a 429 — stamping it would suppress the re-ask for the rest of the day and
  turn backpressure into a permanently missing value.
- **Counting survives as a health metric, never a balance.** `excBudgetCharge_` → `excPpRecordCalls_`,
  writing a **daily** `PP_CALLS_TODAY`. 🔴 Nothing reads it to decide anything. Knowing each
  consumer's call rate is what found the 31.9× waste in the first place, so the observability is
  worth keeping; the subtraction is not.

⏳ **Still interim.** A ParcelPanel → DigitalOcean webhook replaces polling outright. Retire the
limiter with the polling, not before — and note that the cache above survives the cutover on its own
merits, since re-reading an immutable fact is waste against a database too.

### D29 — THE KLAVIYO SWEEP IS RESUMABLE; A PARTIAL SWEEP WRITES NOTHING (Kurt 2026-08-19: "can't we just slow it down?")

🔴 **The failure this closes.** The 2026-08-19 13:40 live `ntRefreshCurrentColumn` on
`_SHIP_2026-08-17` spent **242.7s of the 360s ceiling and wrote one cell** — the Shopify-sourced
`Order Placed`. Both SMS metrics, **79 and 40 pages, well under the 120-page cap**, died on TIME,
threw every page away and reported INCOMPLETE. The next run repeated the same 242.7s and threw it
away again, forever. Nothing was wrong with the cap: **the run had no memory.**

- 🔴 **ONE RUN CANNOT BE MADE TO FINISH — this is arithmetic, not tuning** (measured live
  2026-08-19 against the cohort's real window, read-only):

  | metric | events | pages @200 | s/page | total |
  |---|---|---|---|---|
  | `Clicked Text Message` | 7,974 | 40 | 2.75 | **110s** — fits in one run |
  | `Received Text Message` | 15,720 | 79 | 4.59 | **363s** — exceeds the whole 360s ceiling alone |
  | `Received Email` | 165,999 | 830 | — | declined, unchanged (D24) |

  No budget, cap, or optimisation fits 363s into a 360s invocation. Resumption is the only way that
  metric ever completes.
- 🔴 **THE "129s ON VOLUME PROBES" FIGURE WAS AN INFERENCE AND IT WAS WRONG.** The three
  `/metric-aggregates/` POSTs were assumed to be the waste because they are the only thing logged
  between the window line and the sweep. Timed directly they are **0.5s + 0.5s + 0.6s = 1.6s**. They
  are now cached anyway (once per cohort, and skipped entirely once a sweep is under way) because it
  is free and makes the feasibility decision once — but **the cost is pages, not probes**, and
  anyone optimising this file must attack paging. *A cost attributed by elimination is not a
  measurement* — same class as the stage-timer burn.
- 🔴 **A PARTIALLY SWEPT METRIC WRITES NOTHING.** Its rows stay BLANK and the log states progress
  (`pages 62/79, 78% — resuming next run`). A half-swept count looks finished on the sheet and is
  indistinguishable from a real one. **Blank ≠ zero** applies to partial exactly as it does to absent.
- 🔴 **STATE LIVES IN THE HIDDEN `_nt_sweep` TAB, NOT A SCRIPT PROPERTY.** One cohort's accumulated
  sets measured **178 KB / 3,409 profiles** in test and ~137 KB live. A Script Property value caps at
  **9 KB** (store 500 KB, shared with three other files) so it cannot hold this at all; CacheService
  caps at 100 KB/key and **expires** — a checkpoint that can silently vanish mid-sweep is the same
  failure with extra steps. Rows are `key | kind | seq | payload`, chunked at 40,000 chars (cell limit
  50,000), payload `'#'`-prefixed because `setValue` on a leading `=` makes a FORMULA. Entries are
  `flowId <TAB> email <TAB> epochSeconds` — tab and newline are both illegal in an email address, so
  the delimiter cannot collide with the data (a comma or semicolon eventually would).
- 🔴 **A MISSING CHUNK IS DISCARDED, NOT PARSED.** Half a blob parses cleanly into a
  smaller-but-plausible set, which would then be published as a finished count. The chunk count is
  stored and verified on load; short means drop the record and re-sweep.
- 🔴 **`sort=datetime` (ASCENDING) IS LOAD-BEARING** (verified live: 200, ascending; the default is
  descending). Newest-first inserts an event created between run 1 and run 2 **ahead** of the stored
  cursor, so a resumed sweep completes having silently missed it. Ascending appends behind the cursor.
  Verified live: a stored `links.next` replayed **after a 90s gap** returned 200 and continued exactly
  where the previous page ended. ⚠️ Only a 90s gap was tested — a cursor reused across a **day** is
  unverified; the signature discards state on a new cohort anyway.
- 🔴 **INVALIDATION IS BY SIGNATURE**: `{version, cohort tag, window lo/hi, sorted tracked flow ids,
  metric names}`. Any change discards the state. The flow ids are in it because the sweep **drops
  every untracked flow as it reads** — re-pointing a pin cannot be repaired by continuing a sweep
  that already threw the new flow's events away. A **COMPLETE** result is reused for the rest of the
  same **ET calendar day** and re-measured the next: this tab is a daily walk-forward artifact, so the
  day is its natural grain, and a duration TTL would let a 23:50 sweep be reused at 01:10 and freeze a
  day's movement.
- **Cheapest metric first.** With a hard budget, sweeping the 830-page metric first starves two that
  fit. Ordering by measured pages means run 1 finishes `Clicked Text Message` and both `SMS Engaged`
  rows land immediately; `SMS Sent` and `Time of Sending` land when `Received Text Message` finishes.
- **`NT_MAX_PAGES` (120) is now a TOTAL across runs, and is still the feasibility cap.** Resumption
  exists to make a sweep already under the cap actually finish — it is not licence to raise it.
  Raising it is a decision about how many runs a cohort costs, so it is Kurt's, not a tuning knob.
- Diagnostics: `ntCheckSweepState()` (progress, no writes/fetches) and `ntResetSweepState(tag)`
  (manual escape hatch when the stored pages are suspect but the signature did not move).

⚠️ **UNVERIFIED.** The per-run fixed overhead — the Shopify cohort pull (2,324 orders at 25/page ≈ 93
GraphQL calls) plus `/metrics/` and `/flows/` — was **not** measured; those credentials live in Script
Properties and are unreachable from a local probe. It comes off the same 240s budget, so
`Received Text Message` needs **3 runs at 60s of overhead and 5 at 120s**. The first live run settles
it: read `total …s of the 360s ceiling` next to the `pages n/79` line. The file is also **unexecuted**
— Apps Script cannot be run from here; the checkpoint path was verified by a Node self-test
(`scratchpad/nt_state_selftest.js`, 27 assertions) that loads the real file and exercises
serialize → chunk → sheet round-trip → resume, proving a resumed sweep equals one uninterrupted sweep.

### D29b — `Notifications!Total Shipments` IS A HAND-TYPED MIRROR NOBODY OWNS (found 2026-08-19)

> 🔴 **SUPERSEDED THE SAME DAY BY [D29c](#d29c--total-shipments-and-arrived-are-script-owned-kurt-2026-08-19).**
> The *finding* below stands and is why D29c exists; the *conclusion* ("read, do not write — who owns
> the row is a Kurt decision") was answered: **Kurt said the script owns it.** Read this for the
> evidence, then D29c for the rule.

The 08-19 run's `⚠️ Total Shipments is blank on this column — no percents written` is **not a bug in
`Notifications.gs` and not a stale job**:

- Read back with `valueRenderOption=FORMULA`, `Notifications!Total Shipments` and `Arrived` are
  **literal numbers, not formulas** — nothing links them to anything.
- **No code in the script project writes them.** `paValues_` (PivotAnalytics.gs) emits
  `Total Shipments`, but only onto `TnT2` and `Lost in Transit` (`PA_TABS`); the string does not occur
  in `Code.gs` or `Exceptions.gs` at all, and this file's header has always declared the row "NOT OURS".
- So it is a **manual mirror that stopped after `_SHIP_2026-08-03`**. Measured 2026-08-19:

  | tab | 07-13 | 07-20 | 07-27 | 08-03 | 08-10 | 08-17 |
  |---|---|---|---|---|---|---|
  | `Notifications` | 2025 | 2075 | 2227 | 2305 | *(blank)* | *(blank)* |
  | `TnT2` (script-filled) | 2025 | 2075 | 2227 | 2305 | **2316** | **2324** |

  Identical on all four overlapping columns; TnT2 has the two the Notifications tab is missing.
- 🔴 **FIX IS THE SMALLER ONE: read, do not write.** When this tab's own cell is blank the denominator
  is read from `TnT2` for the same cohort column and the source is logged and noted. The row is **not**
  filled: D24 and the file header both declare it somebody else's, and quietly taking ownership of a
  row a human maintains is how two writers end up disagreeing. **Who owns that row is a Kurt
  decision** — filling it for real is a one-line change once he says whose it is.

### D29c — `Total Shipments` AND `Arrived` ARE SCRIPT-OWNED (Kurt 2026-08-19)

**Kurt's decision, answering D29b:** *"the script should own the `Total Shipments` row on the
Notifications tab (and `Arrived` too if that row is in the same hand-mirrored state) — stop
reading-when-blank as a workaround and write it properly."* This **supersedes** the "NOT OURS / owned
elsewhere / read-only here" framing in D24 and in the `Notifications.gs` header; both have been fixed.

- 🔴 **`Arrived` WAS in the same state, and it had already SILENTLY GONE WRONG.** Both rows are
  hand-typed literals (verified `valueRenderOption=FORMULA`) that no code wrote, and both stop after
  `_SHIP_2026-08-03`. But unlike `Total Shipments`, the `Arrived` mirror had **drifted**:

  | row | 07-13 | 07-20 | 07-27 | 08-03 | 08-10 | 08-17 |
  |---|---|---|---|---|---|---|
  | `Notifications!Arrived` (typed) | 2011 | 2066 | 2217 | **2075** | *(blank)* | *(blank)* |
  | `Lost in Transit!Arrived` (script) | 2011 | 2066 | 2217 | **2268** | 2300 | 1063 |

  **193 low on 08-03** — copied mid-flight and never refreshed as the cohort finished delivering.
  That is the argument for owning these rows: *a hand mirror of a still-moving number goes stale
  silently, and nothing ever says so.* `Total Shipments` matched on every overlapping column only
  because it stops moving once the cohort is tagged.
- 🔴 **`Total Shipments` IS RECOMPUTED, NOT COPIED.** `ntFetchCohort_` already runs the **identical**
  Shopify query TnT2 is built from — `tag:'<ship>' -status:cancelled -tag:'Reship'`, the same string
  in `paFetchCohort_` — so `cohort.length` **is** that population, already in hand, at zero extra
  cost. Live proof: this file measured 2324 / 2316 where TnT2 published 2324 / 2316. A cell-copy
  would inherit TnT2's write timing and break if its row moved; recomputing cannot. TnT2 is still
  read, **purely as an independent cross-check**, and a mismatch is reported — the denominator must
  not come from the thing being measured.
- 🔴 **`Arrived` IS MIRRORED FROM `Lost in Transit`, AND MIRRORING IS THE SAFER CHOICE FOR THIS ROW.**
  `arrived` is not a Shopify-only fact: PivotAnalytics derives it from fulfillment event trees **plus
  ParcelPanel** (🗑️ the "2,500 calls/week account-wide" quota this cited is DELETED by
  P12 — it never existed; the real limit is 120 req/min per key, and lookups consume no plan
  quota at all). This file's cohort query is deliberately light and fetches no fulfillments, so
  recomputing would mean rebuilding that pipeline **and paying its latency a second time** — the
  argument for mirroring was never really about spend, and any drift between two derivations would
  put two different numbers under the same label on two tabs. One derivation, published once,
  mirrored. `Lost in Transit` is refreshed by the same daily walk-forward job, so the mirror inherits
  a fresh value, not a stale one.
- 🔴 **KEYED BY HEADER TEXT + COLUMN-A LABEL, NEVER BY INDEX.** Both the ship-week column and the row
  are looked up **by name on every read** (`ntMirrorCell_`), so inserting a row or a cohort column on
  the source tab cannot silently re-point this at the wrong number — the lookup simply fails and
  writes nothing.
- 🔴 **FILL BLANKS ONLY; A DISAGREEMENT IS REPORTED, NEVER CORRECTED.** A value already in the cell was
  typed by a human and is **never** overwritten — on any column, current or frozen, not just in
  backfill mode (`NT_BLANK_ONLY_KEYS`). When the computed value differs, the run logs
  `DISAGREEMENT on "Arrived" (column N): the sheet holds 2075, this run computes 2268 (+193)` and
  **keeps the typed value**. Silently correcting it would destroy the evidence that the mirror
  drifted, which is the most useful thing these two rows have to say. To hand a column to the script,
  clear the cell.
- 🔴 **BLANK ≠ ZERO.** An empty cohort writes no `Total Shipments` (a 0 would become a denominator and
  make every percent read `0.00%`); a missing source column/row/cell leaves `Arrived` blank. Cohorts
  that predate a source tab stay empty.
- 🔴 **`ntBackfillFrozen` COULD NOT REACH A SINGLE ONE OF ITS DOCUMENTED TARGETS** (found while wiring
  this). It advertises "one-time fill of an already-frozen column (B–E)", but every frozen column is
  by definition **not** the rightmost, and `NT_ASSERT_NOT_RIGHTMOST` rejected all of them — the
  function could only ever target the column it exists to avoid. It was unusable as shipped and
  nothing said so. Fixed with an `allowOlder` flag passed **only** by `ntBackfillFrozen`, which names
  its target explicitly, is double-gated on `NOTIFICATIONS_BACKFILL=1` **and** `NOTIFICATIONS_WRITE=1`,
  defaults to dry, and writes empty cells only. **The daily-refresh path still asserts exactly as
  before** — the guard's real job was stopping the refresh landing on an old column, and that is
  untouched.
- 🔴 **THE HOLD IS SCOPED TO MATURED COLUMNS** — see the addendum below; a blanket fill-blanks-only
  rule would freeze a mid-flight `Arrived` and recreate this very bug.
- **The percent path needs no change.** `ntPct_` was always gated on `denomOk`; it was starved of a
  denominator, not broken. Once `Total Shipments` exists the percents populate on their own.

#### D29c addendum — HOLD APPLIES TO MATURED COLUMNS ONLY, AND THAT DISTINCTION IS THE WHOLE POINT

Caught while wiring D29c, before it shipped: a blanket *fill-blanks-only* rule on these two rows
**recreates the exact bug D29c exists to fix.** `Arrived` on a live cohort is **mid-flight** — 1063 of
2324 on `_SHIP_2026-08-17` two days in — so "write once, then never touch" would stamp a mid-flight
number and freeze it there forever. That is precisely how the typed **2075** landed on 08-03 while the
authority moved on to **2268**.

So the hold is scoped by column maturity, which is the tab's existing walk-forward doctrine:

| column | path | behaviour |
|---|---|---|
| current, age < `NT_MATURITY_DAYS` | `ntRefreshCurrentColumn` (`emptyOnly=false`) | **refreshes in place**, like every other owned row — the number is still moving |
| matured / frozen | `ntBackfillFrozen` only (`emptyOnly=true`, double-gated, dry by default) | **holds** what is there; reports the gap if ours differs |

A "disagreement" is therefore only ever raised where it means something — against a value that has
stopped moving and may be a human's. Raising it on the current column would just narrate normal daily
movement as if it were drift, and noise of that kind trains the reader to ignore the real ones.


### D30 — SOURCE OF TRUTH FOR EACH `Email Sent` ROW, AND THE FILTER THAT HID THE SHIPPING EVENT (Kurt 2026-08-19)

Kurt, from a live order: the shipping confirmation is a **Shopify order event**, exactly like the
placed one — so `Order Shipped → Email Sent` never needed the Klaviyo sweep.

```
RMFG Shopify Translator sent a shipping confirmation email to Lindsay Marshall (…).   5:00 PM
RMFG Shopify Translator marked 12 items as fulfilled from RMFG.                       5:00 PM
```

**The four `Email Sent` rows, and where each number comes from:**

| row | source | grain | wk0817 | wk0810 |
|---|---|---|---|---|
| `Order Placed → Email Sent` | Shopify order event, `^order confirmation email was sent` | ORDER | **2301** (98.99%) | **2289** (98.83%) |
| `Order Shipped → Email Sent` | Shopify order event, `sent a shipping confirmation email to` | ORDER | **2323** (99.96%) | **2316** (100.00%) |
| `Order Delivered → Email Sent` | Klaviyo `Received Email` — **no Shopify equivalent exists** | profile | BLANK | BLANK |
| `Order Shipped/Delivered → SMS *` | Klaviyo, resumable sweep (D29) | profile | see D29 | see D29 |

- 🔴 **`query:"confirmation"` HID THE SHIPPING EVENT, AND WOULD HAVE SHIPPED A PLAUSIBLE LIE.** The
  existing selector was `events(first:10, sortKey:CREATED_AT, reverse:false, query:"confirmation")`.
  Counting the shipping confirmation through it returns **21 of 2,324 orders (0.90%)**; through an
  unfiltered selector the same cohort returns **2,323 (99.96%)**. Wiring the new regex into the old
  selector would have written **21** — small enough to read as a real deliverability problem, large
  enough not to look broken. *Measuring with an idealised query and shipping a narrower one is the
  same class as auditing the fix you queued instead of the artifact that left.*
- 🔴 **THE WINDOW MUST BE 40, NOT 10 OR 15.** The shipping confirmation lands **after** address
  updates and fulfillment-location changes, not among the first few events. Measured on wk0817:
  `first:15` no-query finds **829 (35.67%)**; `first:40` no-query finds **2,323 (99.96%)**. A page
  size chosen by intuition would have undercounted by two thirds and looked fine.
  Ascending (`sortKey: CREATED_AT, reverse:false`) is still load-bearing for the placed row — the
  2026-08-18 burn (271/400 newest-first vs 397/400 ascending) is unchanged.
- 🔴 **DECLARED SIDE-EFFECT: `Order Placed` RISES 2291 → 2301** on wk0817, because the same filter
  was costing it 10 orders. Stated here rather than left to be discovered as unexplained drift.
- 🔴 **MATCH THE PHRASE, NOT THE LINE START, AND NEVER THE ACTOR.** The shipping message carries an
  actor prefix where the placed one does not, so it cannot be anchored to `^`. It must also not be
  anchored to `RMFG`: this cohort is 100% `RMFG Shopify Translator`, but the account also fulfils
  **from COG**, and a COG / Woburn / Dallas-leg actor has to match too. The two matchers were checked
  against the **complete** email-mentioning vocabulary of the account (6 shapes over a 400-order
  sample) and are disjoint — including the near-misses `Order edited email was sent to …` and
  `<human> sent an order confirmation email to …`. There is **no** passive
  `Shipping confirmation email was sent to …` variant; the regex was written against the enumerated
  vocabulary, not a guess.
- **Known, unchanged undercount:** `<human> sent an order confirmation email to <name>` (a manual
  resend, 8 per 400 sampled) does **not** match the anchored placed regex. Left as-is rather than
  widened silently — widening it is a Kurt call because it changes a shipped number.
- 🔴 **ORDER GRAIN, DISTINCT ORDERS.** Only the FIRST match per order is kept, so several
  fulfillments cannot inflate the row. Measured, events == orders on both cohorts (2323/2323 and
  2316/2316), and `NT_ASSERT_SHIPPED_OVERCOUNT` refuses to write if it ever exceeds the cohort.
- 🔴 **COVERAGE IS NOT RMFG-ONLY.** Both cohorts are 100% `FULFILLED` with ≥1 fulfillment object, and
  the event is present on 2323/2324 and 2316/2316. The single wk0817 miss is `#173444` (fulfilled,
  no shipping-confirmation event).

#### 🔴 THE TWO MESSAGES ARE NOT THE SAME, AND THE ROW NOW MEANS THE SHOPIFY ONE

Answering the question directly rather than swapping definitions quietly:

- **This row = Shopify's shipping-confirmation email**, sent by the fulfiller **at fulfillment** —
  the event is stamped the same minute as `marked N items as fulfilled`. ~Every order gets one
  (2,323 of 2,324).
- **Not the same as** Klaviyo flow `XYFE5N` *Shipping Notification - In Transit (**Parcel Panel**)*,
  which fires off a ParcelPanel tracking webhook when the **carrier scans the parcel into transit** —
  a later, carrier-driven event. Its weekly flow-series figure is in the hundreds, not ~2,300, which
  is the scale difference you would expect between "we fulfilled it" and "the carrier picked it up".
- Counting the Klaviyo In-Transit email as well would be a **separate row**, never a re-point of this
  one. The file header states this at the row definition.

#### `Order Delivered` — checked, and the trick does NOT work

Kurt's earlier statement was "in transit and delivery both come through klaviyo". Verified rather
than assumed: **every** order event on both cohorts was scanned for the substring `deliver` —
**zero matches** across 2,324 and 2,316 orders. Shopify emits no delivery notification event, so
`Order Delivered → Email Sent` stays Klaviyo-only and stays BLANK until a job without the 360s
ceiling can sweep it.

#### Cost — this is not free, and it comes off the Klaviyo budget

The cohort pull runs every invocation and is charged to the **same 240s budget** as the resumable
sweep (D29). Measured on wk0817, 93 pages each:

| selector | placed | shipped | wall | verdict |
|---|---|---|---|---|
| `first:10` + `query:"confirmation"` (old) | 2291 | **21 (0.90%)** | 117.6s | wrong |
| `first:15` no filter | 2300 | **829 (35.67%)** | 153.2s | wrong |
| `first:40` no filter (**shipped**) | **2301** | **2323 (99.96%)** | **169.4s** | correct |

🔴 **THIS TAKES ~52s/RUN AWAY FROM THE KLAVIYO SWEEP, AND THAT CHANGES D29's RUN COUNTS.** With the
fetch budget at 240s and a 25s checkpoint reserve, the sweep now gets roughly **240 − 169 − 25 ≈ 46s**
of paging per run. At the measured 4.59s/page, `Received Text Message` (79 pages) goes from ~3–5 runs
to roughly **8**, and `Clicked Text Message` (40 pages @ 2.75s) to about **3**. Correctness was worth
buying, but the bill lands on the sweep.

Two ways to buy it back, **both deliberately NOT taken here** — they are tuning decisions about how
close to a hard 360s kill we run, and D29 already says that class of call is Kurt's:
1. **Raise `NT_TIME_BUDGET_MS`.** The 08-19 run used 242.7s of 360s, so ~117s is going unused. 300s
   would restore ~4 runs, at the cost of a thinner margin before the kill.
2. **Cache the cohort pull for the ET day** in the existing `_nt_sweep` store. Removes 169s from
   every resumed run — but it FREEZES `Order Placed` and `Order Shipped` mid-day, which is the
   staleness class D29c exists to stop. Would need the same matured-vs-current scoping.

The first live run settles the arithmetic — read `total …s of the 360s ceiling` next to `pages n/79`.

---

### D31 — THE KLAVIYO SWEEP'S TRANSPORT WAS 4.9× MORE EXPENSIVE THAN IT NEEDED TO BE, AND THE RUN COUNTS WERE STILL 4.7× OPTIMISTIC (measured live 2026-08-19)

🔴 **Everything in D29's cost model was measured against an untested `page[size]=200` and a profile
side-join nobody had checked was necessary.** Neither was a limit; both were assumptions. This
directive records what the endpoint actually does, what does NOT exist so nobody re-derives it, and
why `Order Delivered → Email Sent` is still blank after all of it.

#### What does NOT exist — probed live, do not look again

| candidate | result |
|---|---|
| flow filter on `/events/` — `equals($flow,…)`, `equals(flow_id,…)`, `equals(event_properties.$flow,…)`, `equals($flow_id,…)`, `equals(properties.$flow,…)`, `equals(flow,…)` | **all 400** `"… is not a filterable field for this resource"` |
| `/flows/{id}/flow-messages/` | **404** — not a path on revision `2024-10-15` |
| `/metric-aggregates/` `by:["$flow_id"]` | **400** `"not a valid choice for 'by'"` |
| parallel day-sliced fetching (`UrlFetchApp.fetchAll` shape) | works, **1.36×**, and does **not** improve from 3→6 workers — Klaviyo throttles per account. Same result byte-for-byte as the sequential sweep, so it is correct, just not worth the checkpoint redesign. Declined. |

Two account-wide endpoints DO work and are **still not usable as cell values**:
`/metric-aggregates/` with `by:["$flow"]` (0.6s, exact per-flow counts) and `/flow-values-reports/`
(per-flow-message `recipients`). Both answer *"how many Delivered-flow emails went out"* and neither
can answer *"how many of THIS COHORT'S customers got one"*. 🔴 **Never write either into a cohort
column.** They are feasibility probes and ceilings only.

#### What did pay — all live-measured, all now in the file

1. **`page[size]` is 1000, not 200.** `2000` → 400 `"Page size must be an integer <= 1000"`, and
   `links.next` carries the size forward. 5× fewer round trips for the same rows.
2. **The email metric does not need `include=profile` at all.** The recipient is already in the
   event as `event_properties["Recipient Email Address"]`. Verified against the profile join on
   **3,864 tracked-flow events: 100% present, 0 mismatches, 0 gaps either way.** 🔴 The SMS metrics
   are the opposite — **0 of 2,758** `Received Text Message` and **0 of 1,590** `Clicked Text
   Message` tracked-flow events carry any address (they carry `To Number`, a phone), so those two
   keep the join. `ntNeedsProfileInclude_` is the one place that decides.
3. **A shorter window for the email metric only.** It feeds exactly one row now (`Order Delivered →
   Email Sent`), and a Delivered notification cannot precede the shipment, so it starts at
   ship-date−1 instead of ship-date−9: **213,266 → 135,665 events** for `_SHIP_2026-08-10`.

| request shape | s/page | ms/event |
|---|---|---|
| `size=200`, `include=profile` (what shipped before) | 2.15 | 10.75 |
| `size=1000`, `include=profile` | 5.60 | 5.60 |
| **`size=1000`, no include** | **2.54** (3.46 sustained over 65 pages) | **2.54** |

Production is ~2.13× slower than the workstation these were measured on (this file measured *itself*
at 4.59 s/page at size 200 = 22.95 ms/event), so the shipped shape is estimated at **~7.4 ms/event**
— `NT_RATE_MS_PER_EVENT`. Net: **~22.95 → ~7.4 ms/event over ~36% fewer events ≈ 4.9× cheaper.**

#### 🔴 The cap is now counted in EVENTS, and nothing was loosened

`NT_MAX_PAGES = 120` at 200 rows a page was exactly **24,000 events**. Raising the page size to 1000
while leaving "120 pages" in place would have quintupled the real budget to 120,000 without anyone
deciding to — a silent 5× loosening hidden inside a transport tweak. So the cap is now
**`NT_MAX_EVENTS = 24000`**, which is exact parity, and it is immune to the next transport change.

#### 🔴 AND THE RUN COUNTS WERE STILL WRONG — the cohort pull is charged first

D30 measured that the Shopify cohort pull is **169.4s of every 240s run**, before a single Klaviyo
page. Any run-count computed against the whole fetch budget is therefore **4.7× optimistic**.
`ntRunsFor_` now subtracts `NT_COHORT_PULL_MS` and the checkpoint reserve, leaving **~46s of paging
per run**. 🔴 That constant is not a lever — lowering it does not create time, it only makes the
estimate lie.

| cohort | `Received Email` in its window | runs, as the code stands | runs if the cohort pull were cached |
|---|---|---|---|
| `_SHIP_2026-08-17` | ~135,000 at maturity (13,997 so far) | 22 | 5 |
| `_SHIP_2026-08-10` | 135,665 | 22 | 5 |
| `_SHIP_2026-08-03` | 159,225 | 26 | 6 |
| `_SHIP_2026-07-27` | 160,555 | 26 | 6 |
| `_SHIP_2026-07-20` | 176,562 | 29 | 7 |
| `_SHIP_2026-07-13` | 218,613 | 36 | 8 |

**Read the third column.** `NT_MAX_EVENTS = 250000` makes every cohort *finishable*, but at **~161
runs to fill all six columns** — months on the daily trigger. Saying that plainly is the point of
the table; 4.9× cheaper is not the same as cheap.

**The fourth column is a proposal that is deliberately NOT taken here.** D30 listed caching the
cohort pull and declined it because it would freeze `Order Placed` / `Order Shipped` mid-day — the
staleness class D29c exists to stop. 🔴 **That objection does not apply to a FROZEN column:** a
matured cohort's placed/shipped counts are final, so a backfill run has nothing to go stale. Caching
the cohort pull **for frozen columns only** is the single highest-leverage change left here — 161
runs → ~37 — and it is written down rather than done because D30 already ruled this class of
decision Kurt's.

#### 🔴 Klaviyo retention is NOT what blocks the backfill

Checked 2026-08-19: `/events/` returns real data on page 1 for **every** target window back to
`_SHIP_2026-07-13`, and a probe at `2026-05-12` still returns events. All five older columns are
**queryable**; what stops them is the per-column run cost above, not missing data. Retention is a
plan-level setting that can change — re-probe before promising a backfill of anything older.

#### 🔴 Fail-closed on the one thing that would fail silently

If Klaviyo ever stops populating `Recipient Email Address`, the email sweep would not error — it
would just return a **smaller number**, and a quiet undercount on a published row is the worst
outcome available. `ntAssertAddressCoverage_` throws `NT_ASSERT_ADDRESS_COVERAGE` when more than
0.5% of *tracked-flow* events resolve no address, on the live response shape (the only shape that
can regress — a fixture would pass forever). Measured today: **0 of 3,864.** Do not relax the
tolerance; find out what Klaviyo stopped sending.

#### Also changed

- `NT_STATE_VER` 2 → 3: every stored checkpoint is discarded on sight. The state now carries
  `fetched` / `trackedSeen` / `noEmail`, and the signature carries **every metric's window** rather
  than one shared `lo`/`hi` — the email window moves independently now, and a single pair could not
  detect that. A set accumulated over a different span is a wrong answer, not a partial one.
- `ntCheckSweepState()` reports events (not pages), runs remaining, and the no-address counter.

### D32 — MATURITY IS KURT'S DELIVERY SLA (ship + 4 = FRIDAY), AND THE MEASUREMENT WINDOW CLOSES THERE (Kurt 2026-08-20)

Negatives first.

> 🔴 **`NT_MATURITY_DAYS = 10` WAS NEVER A BUSINESS RULE.** It was `PA_MATURITY_DAYS` (D15), copied
> into `Notifications.gs` because it was there — a PivotAnalytics *reconciliation* window borrowed as
> a *completeness* rule. Kurt, verbatim: **"Orders have to be delivered by 8/14 at the latest. any
> email after that is an issue"** · **"it should be mature on the friday"** · **"FRIDAY 8/14 NOT
> thursday"**. A cohort tagged `_SHIP_2026-08-10` ships that Monday and every box is DUE DELIVERED by
> Friday 2026-08-14 = **ship + 4 days**.

#### The rule

- **`NT_MATURITY_DAYS = 4`.** Derived from the cohort's OWN ship date (`ntCohortAgeDays_` subtracts
  the date in the `_SHIP_` tag), never from a weekday name — a shifted ship week still lands on
  ship+4 without anything knowing what day it is.
- 🔴 **If a ship week ever starts on a day other than Monday it matures four days after ITSELF** — a
  Tuesday cohort matures Saturday, not Friday. That is the correct reading of "delivered within four
  days of shipping". If Kurt ever means *always Friday, whatever day we shipped*, that is a DIFFERENT
  rule (weekday-anchored, so a Thursday cohort would mature the next day) and it needs his word — do
  not infer it from this one. The **Tuesday Dallas sub-cohort is not such a case**: it carries the
  same Monday `_SHIP_` tag and is already measured against that Monday's Friday.
- **Every Klaviyo window closes at `ntMaturityEndIso_(shipWeek)`** = midnight **ET** at the END of the
  maturity day (`2026-08-15T04:00:00Z` for `_SHIP_2026-08-10`). Exact windows, all half-open `[lo,hi)`:

  | metric | feeds | window |
  |---|---|---|
  | `Received Email` | `Order Delivered → Email Sent` only | `[ship − 1d 00:00Z, maturityEnd)` |
  | `Received Text Message` | Shipped + Delivered `SMS Sent` | `[ship − 9d 00:00Z, maturityEnd)` |
  | `Clicked Text Message` | both `SMS Engaged` rows | `[ship − 9d 00:00Z, maturityEnd)` |

  **The LEADS did not change** and must not: email keeps its 1-day lead (a Delivered mail cannot
  precede the shipment, D31); SMS keeps 9 days because it also feeds the SHIPPED rows, which fire
  early. **Only the tail moved**, from a flat `+12d` to the deadline.
- 🔴 **The boundary is ET, not UTC, and that is worth four hours of Friday.** Closing at
  `2026-08-15T00:00:00Z` would end the window at 8pm ET / 5pm PDT and call a Friday-evening delivery
  notification LATE. The freeze clock is already ET (`NT_TZ`) and the deadline is a business day. The
  ET edge is a strict superset of the UTC one, so nothing on time is lost to a timezone artifact.
  Measured cost of the choice: 3 delivered-email customers on 08-10, 2 on 07-27, 1 on 07-20, 3 on
  07-13 sit in that band.

#### What it changed on the published rows (measured, not assumed)

Re-slicing the SAME swept events over the new tail (`nt_mature.py`, 2026-08-20 — no re-fetch, so the
two are directly comparable). Rows move **DOWN**, because `+12d` was sweeping up late boxes:

| cohort | `Delivered → Email Sent` +12d | at maturity | late-only customers |
|---|---|---|---|
| `_SHIP_2026-08-10` | 2,181 | **2,153** | 28 |
| `_SHIP_2026-08-03` | 2,142 | **2,132** | 10 |
| `_SHIP_2026-07-27` | 2,020 | **1,678** | **342 (15.8%)** |
| `_SHIP_2026-07-20` | 1,529 | **1,517** | 12 |
| `_SHIP_2026-07-13` | 1,149 | **1,127** | 22 |

`_SHIP_2026-07-27` read a healthy 93% under the old window. Measured to its own Friday it is 1,678
of 2,169. **These rows are now a delivery-PERFORMANCE number, not an eventually-delivered one.**
Delivered-SMS late-only over the same cohorts: 15 / 6 / 135 / 3 / 9.

#### 🔴 The late count is EXCLUDED, not lost — and there is no cheap way to publish it

Sends after the deadline are the signal Kurt asked for. They are **logged** (`ntLateSignalNote_`),
not written, because getting them into a cell costs a second sweep.

- 🔴 **DO NOT reach for `/metric-aggregates/`.** It is ACCOUNT-WIDE, and the days after one cohort's
  deadline are exactly the days the NEXT cohort is delivered on time. Its Delivered-flow count in the
  late span is **2,092 for `_SHIP_2026-08-10` against a true in-cohort 28**, and 3,107 vs 342 on
  07-27. It is not a proxy, it is a different quantity. Tried, measured, rejected.
- The two honest routes, priced: **(a)** sweep `[maturityEnd, ship + NT_LATE_HORIZON_DAYS)` and filter
  to the cohort — a second sweep the size of the tail just removed, which gives back the whole saving;
  **(b)** mirror it in from the local/cloud pipeline that has no 360s ceiling, the same shape `Arrived`
  already uses. **Whether the late count gets its own ROW is Kurt's call** — it is a new published
  number, not a tuning knob.
- Definition, when it is built: a customer is LATE if their FIRST Delivered-flow send lands after the
  deadline **and** they have none before it — so an on-time customer with a later duplicate is not
  double-counted.

#### 🔴 The two MIRROR rows do NOT freeze at maturity (`NT_MIRROR_MATURITY_DAYS = 10`)

`Arrived` is mirrored from `Lost in Transit`, which self-heals on the PivotAnalytics walk-forward
until `PA_MATURITY_DAYS = 10` (D15: `arrived` moved 2,253 → 2,256 → 2,260 → 2,262 inside one day).
Dropping notification maturity to 4 without this would **freeze the mirror six days before its
authority does**, stamping a mid-flight delivery count into a cell nothing revisits — the exact
failure D29c exists to stop. So ages **6–9 run MIRROR-ONLY** (`ntRefreshMirrors_`): `Arrived`
re-reads its authority, nothing else is touched, no cohort pull, no Klaviyo page.

#### 🔴 The FINAL full run is the day AFTER maturity, not the maturity day

`NT_FINAL_RUN_AGE = NT_MATURITY_DAYS + 1`. The window closes at midnight ET at the end of the
Friday, so a trigger firing ON the Friday sees only part of it. Freezing there would publish a
Thursday-night view of a Friday deadline. Because `ntCohortAgeDays_` counts ET days against an ET
midnight boundary, `age >= NT_MATURITY_DAYS + 1` is EXACTLY the predicate "now is past
`ntMaturityEndIso_`" — the same condition written two ways, not a fudge. Leg by age:
**0 skip · 1–5 FULL · 6–9 mirror-only · 10+ frozen.**

Consequence, handled: a completed sweep used to be discarded on every new ET day so the column walks
forward. That now applies **only while the window is open**. A sweep completed **on or after** the day
the window closed is KEPT — its event set cannot change, and with only one closed-window day to work
in, discarding it would mean the column can never be finished. 🔴 The test is *"was the sweep taken
after the window closed"*, not *"is the window closed now"*: `complete` means "reached
`links.next = null`", never "the window is over", so a day-2 complete sweep is still discarded.

#### Run cost: measure it, and it barely moves the decision

Live `/metric-aggregates/` per cohort per metric, old window vs new (2026-08-20), against
`NT_MAX_EVENTS = 24000`:

| cohort | metric | OLD ev / runs | NEW ev / runs | verdict |
|---|---|---|---|---|
| 08-10 | Received Email | 144,886 / 24 | 119,663 / 20 | declined → declined |
| 08-10 | Received Text Message | 22,908 / 4 | 14,305 / 3 | under → under |
| 08-10 | Clicked Text Message | 11,793 / 2 | 7,461 / 2 | under → under |
| 08-03 | Received Email | 161,678 / 27 | 44,762 / 8 | declined → declined |
| 08-03 | Received Text Message | 34,640 / 6 | 26,943 / 5 | declined → declined |
| 08-03 | Clicked Text Message | 17,275 / 3 | 13,326 / 3 | under → under |
| 07-27 | Received Email | 162,467 / 27 | 116,569 / 19 | declined → declined |
| 07-27 | Received Text Message | 31,813 / 6 | 25,378 / 5 | declined → declined |
| 07-27 | Clicked Text Message | 15,982 / 3 | 12,592 / 3 | under → under |
| 07-20 | Received Email | 178,106 / 29 | 60,182 / 10 | declined → declined |
| 07-20 | **Received Text Message** | 28,255 / 5 | **7,928 / 2** | 🟢 **NEWLY REACHABLE** |
| 07-20 | Clicked Text Message | 14,522 / 3 | 4,711 / 1 | under → under |
| 07-13 | Received Email | 219,920 / 36 | 158,956 / 26 | declined → declined |
| 07-13 | Received Text Message | 11,157 / 2 | 6,249 / 2 | under → under |
| 07-13 | Clicked Text Message | 6,778 / 2 | 4,095 / 1 | under → under |

Per-cohort runs to fill a column: **30→25, 36→16, 36→27, 37→13, 40→29** (179 → 110).

🔴 **Exactly ONE row-pair is bought by this change** — `_SHIP_2026-07-20`'s `SMS Sent` rows. Every
`Received Email` sweep is still 1.9×–6.6× over the cap, so every `Order Delivered → Email Sent` cell
stays DECLINED and BLANK in Apps Script. Do not read the 72% cut on 08-03's email metric as progress
toward a filled cell: 44,762 is still nearly twice 24,000. It *is* now the cheapest email column by a
wide margin — **if** Kurt raises the cap, ~45,000 buys that one column for 8 runs.

🔴 **And the old `135,665` figure for 08-10 was itself measuring a moving target** — re-probed a day
later over the identical `+12d` window it is **144,886**, because that window ran to 2026-08-22, into
the future. A cost quoted from a window that has not closed is a lower bound, not a measurement. The
maturity windows have all closed, so the new figures cannot drift.

#### Verification

- `nt_maturity_selftest.js` **lifts `ntMaturityEndIso_` / `ntWindowFor_` out of the deployed `.gs`**
  (not a re-typed copy — extraction fails loudly if they move) and asserts: every cohort matures on
  its Friday; the window closes at the END of it (04:00Z EDT, **05:00Z EST** — a January cohort is
  tested, so nothing is hard-coded to summer); a Tuesday cohort matures Saturday; the leads are
  unchanged; the age→leg table; the checkpoint-reuse predicate. **0 failures.**
- **Cross-language reproduce gate:** the `.gs` and the python pipeline independently produce
  `2026-08-15T04:00:00Z` for `_SHIP_2026-08-10`.
- **Fidelity gate re-run on the new boundary** (`nt_cost.py`): our re-slice vs Klaviyo's own
  server-side `by:["$flow"]` aggregate — **45 of 45 comparisons EXACT**. Under the old `+12d` window
  the same gate was 42 of 45, and **all 3 misses were `_SHIP_2026-08-10`, in both directions** — the
  count moving under the measurement. Closing the window at maturity is what removed them.

#### `_SHIP_2026-08-10` was written under this rule (2026-08-20)

16 cells, `Notifications!F2:F21`, targeted `values.update` per cell, column resolved by row-1 header
text and row by column-A label, `USER_ENTERED` for percents (RAW would land a TEXT string and break
arithmetic), fill-blanks-only, every cell read back. **16 verified, 0 failed.** Gates passed first:
computed Total Shipments 2,316 == the population `TnT2` publishes (this tab's own cell was blank);
every Sent ≤ Total; time split 3,744 + 154 + 123 MISSING = 4,021 sends.

Total 2316 · Arrived 2300 · Placed 2289 / 98.83% · Shipped email 2316 / 100.00% · Shipped SMS 928 /
40.07%, engaged 348 · Delivered email 2153 / 92.96% · Delivered SMS 940 / 40.59%, engaged 283 ·
Time 3744 day / 154 night.

#### `_SHIP_2026-08-03` (column E) was RESTATED — the one time the never-overwrite guard was lifted

Column E was written 2026-08-19 under the `+12d` window, so 10 of its cells disagreed with the
maturity measurement. Fill-blanks-only left them alone and reported them; **Kurt then explicitly
lifted the guard for THIS column and THESE cells** (2026-08-20) and they were overwritten.

🔴 **That authorisation is narrow and it is spelled out in `nt_restate_e.py`, not carried by a flag.**
The cohort is hard-pinned (no loop, no argument, no other column reachable), `own||Arrived` is
hard-EXCLUDED and the exclusion is asserted, only differing cells are written, and every write is
read back. **The default everywhere else is still: report the disagreement, never correct it.**

| cell | row | before | after |
|---|---|---|---|
| `E10` | Shipped → SMS Engaged | 272 | **265** |
| `E11` | Shipped → SMS Sent | 704 | **698** |
| `E12` | Shipped → SMS % of Total | 30.54% | **30.28%** |
| `E14` | Delivered → Email Sent | 2,142 | **2,132** |
| `E15` | Delivered → Email % of Total | 92.93% | **92.49%** |
| `E16` | Delivered → SMS Engaged | 259 | **255** |
| `E17` | Delivered → SMS Sent | 854 | **848** |
| `E18` | Delivered → SMS % of Total | 37.05% | **36.79%** |
| `E20` | Time of Sending — day | 3,456 | **3,440** |
| `E21` | Time of Sending — night | 180 | **174** |

**10 written, 10 verified on read-back, 0 failed.** Unchanged and not rewritten: `E2` 2,305, `E5`
2,283, `E6` 99.05%, `E8` 2,305, `E9` 100.00% (Shopify-sourced rows are window-independent).
🔴 **These numbers MOVED after publication — Dan may have quoted the old ones.** That is the reason
the before/after is recorded here rather than the change being made silently.

🔴 **`Arrived` (E3) was NOT touched and the disagreement still stands: the sheet holds a hand-typed
2,075 against an authority value of 2,268 on `Lost in Transit`.** That is a separate, pre-existing
problem (a mid-flight copy that was never refreshed — the motivating case for D29c) and Kurt has not
ruled on it. It is not part of this restatement and must not be swept into one.

**Re-measured from a FRESH sweep, not re-sliced from the union cache** — re-slicing would make the
restatement depend on the same bytes that produced the value being replaced. 08-03's own closed
windows were paged into a separate cache namespace (44,762 + 26,943 + 13,326 events, 3.7 min) and
the Shopify cohort re-pulled (2,305 orders, identical population). Both gates passed before any
write: our retained per-flow counts vs Klaviyo's server-side `by:["$flow"]` aggregate **9 of 9
exact**; the fresh sweep vs the union-cache re-slice **0 differences — two independent measurements
reproducing each other**; computed Total 2,305 == published 2,305; every Sent ≤ Total; time split
3,440 + 174 + 64 MISSING = 3,678 sends.

**No other column is owed this.** `_SHIP_2026-07-13/-20/-27` (columns B/C/D) hold **only** rows 2–3
(`Total Shipments`, `Arrived`) — rows 5–21 are blank, so there is nothing measured to restate; they
remain refused on the denominator gate (cohort 1,944/2,038/2,169 vs published 2,025/2,075/2,227),
which this rule does not touch. `_SHIP_2026-08-17` (column G) needs nothing either: at age 3 both the
old `+12d` edge (2026-08-29) and the new maturity edge (2026-08-22T04:00:00Z) are still in the
FUTURE, so no cell there is wrong today, and it walks onto the new boundary by itself (full runs
through day 5, then mirror-only, then frozen).

> Open, unrelated to maturity: `_SHIP_2026-07-20`'s cohort/published gap is 1.78%, **inside**
> `NT_DENOM_TOLERANCE` (2%), yet the writer refuses it because its gate demands EXACT equality with
> the published total. Two gates with different thresholds; not reconciled here.

#### 🔴 TnT2 / Lost in Transit / Routing Match are NOT changed here — reported for Kurt

By the Friday rule, `PA_MATURITY_DAYS = 10` (D15) and D23's freeze-at-first-write are six days late
too. **They are deliberately left alone**, because the tradeoff is not the same one:

- **What changing TnT2 to 4 days WOULD fix:** the column would stop describing "eventually arrived"
  and start describing "arrived by the deadline", matching `Notifications` and matching how Kurt
  actually judges a week.
- **What it would BREAK, and why the longer window is not the same mistake:** TnT2's walk-forward
  exists so a **late delivery self-heals into the column** — a box frozen `3+ Day` / `Not Arrived`
  that later proves delivered (D15: `arrived` moved four times in one day). That is a *correctness*
  mechanism for a fact that arrives late, not a generosity in a deadline. A notification count has no
  equivalent: the event either happened by Friday or it did not, and nothing about it is pending. So
  the same number means different things on the two tabs, and cutting TnT2 to 4 days would freeze
  `Not Arrived` boxes that are simply not scanned yet.
- **What it would NOT fix either way:** the all-in late rate. Attribution never shrinks it
  ([[any-tag-convention-is-rmfgs]]).
- **The shape that gets both:** keep TnT2's 10-day self-heal for *arrival truth*, and add a
  **`by Friday`** observation alongside it — the same box, judged at the deadline — rather than moving
  the freeze. That is a new published number and therefore Kurt's call.
- **`Routing Match` (D23, write-once at first write) is a third case** and is untouched: it records
  what the engine chose, which cannot change after the fact.

**Deployed:** `Notifications.gs` `803c3ea257af` → `68e3b2245faf` via the gated pusher (other four
files verified byte-identical to live before and after the PUT; never `clasp push`).

### D33 — THE `Hold` TAB HAD NO WRITER, AND A HOLD SNAPSHOT CANNOT BE BACK-FILLED (2026-08-25)

> The tab was rebuilt and hand-filled ONCE, on 2026-08-20, by a local one-shot
> (`scratchpad/hold_compute.py` + `hold_write.py`). Column **J = 2026-08-20** is the only column it
> ever wrote. **K–N (08-21…08-24) are blank and always will be.**

**The failure this prevents (negatives first).** A `grep -E '_HOLD|_CSHOLD|_FLOWHOLD|_UNRESOLVED'`
over all five `.gs` files returned 18 hits, every one of them the *Reship* tab's "Unresolved"
COUNTIFS. **Nothing in the deployed project had ever touched this tab.** So the rebuild produced a
report with a date column per day and no cadence — the dead-cadence class (`ontrac_master`,
`mfg_translations`, `shopify_orders`, `fulfillments-sync`), except worse: those three could be
caught up by re-running the ingest, and **this one cannot**. Shopify order tags carry no
application timestamp and the Orders API exposes no tag history, so a hold snapshot exists only on
the day it is taken. Five columns were not "late", they were **lost**. The tab looked maintained
the whole time, because a blank cell and a not-yet-measured cell are the same pixel.

**🔴 The rules.**

1. **WRITE-ONCE PER DATE.** A cell that already holds anything is never overwritten, never blanked
   and rewritten, never "corrected" — by a re-run, by a backfill, or by a human-invoked menu click.
   Same shape as `Routing Match` (D23) and the never-overwrite-a-dated-output rule, and for a
   harder reason than D23's: Routing Match *degrades* with age, this one **does not exist** after
   the day. Cells are written **one at a time, only when blank**; a range write over this tab
   destroys history and is forbidden.
2. **A disagreement is REPORTED, not repaired.** A filled cell whose value differs from what we
   would compute is logged (`Hold DISAGREEMENT`) and left alone. It is somebody's reading; this
   writer is not the authority on a cell it did not write.
3. **BLANK ≠ ZERO.** Snapshot rows are written into **today's column only**. 08-12…08-19 stay blank
   forever rather than carry a fabricated back-cast. The four **HOLDS OPENED** rows are the sole
   exception: they key on order `createdAt`, which *is* historical, so they are filled for every
   past date column — and a `0` there is a real observation (the sweep covered every hold-tagged
   order and none was created that day), not a gap.
4. **Rows resolve by LABEL against column A, columns by the row-1 DATE. Never by index.**
   `HOLD_ASSERT_ROW_SHAPE` throws if any owned label is missing (40 at launch; 36 since the
   2026-08-26 unfulfilled-only addendum below retired four), `HOLD_ASSERT_DUP_LABEL` if one
   repeats. Three labels are deliberately INDENTED (`"   By Flow  (_FLOWHOLD)"`) and the leading
   spaces are part of the key; a trimmed match is accepted but logged.
5. **Every partition must close before a single cell is planned** (`HOLD_ASSERT_PARTITION`, refuses
   the whole write): `_HOLD` unfulfilled + fulfilled + other == the `_HOLD` total; the three aging
   buckets == the unfulfilled-active denominator; the active union is between `max(per-tag)` and
   `sum(per-tag)`; the HELD id list length == the `_HOLD unfulfilled` count row. A partial sweep
   otherwise writes a smaller number that reads as progress on the migration.
6. **A zero is a claim** (`HOLD_ASSERT_ALL_ZERO`). One tag at zero is a real state — `_HOLD`
   reaching zero **is the goal**, and `_CSHOLD` was legitimately 0 on 08-20. All **seven**
   simultaneously is a dead token or a broken query far more often than it is the truth, and the
   column it would stamp is unrecoverable. Override with Script Property `HOLD_ALLOW_ALL_ZERO=1`.
7. **A sweep over `HOLD_MAX_SWEEP` (6000) rows refuses** rather than truncating.
8. **Money and percent are written as typed literals** (`"$5045.01"`, `"97.92%"`) so they land as
   NUMBERS under a currency/percent format, not as text that breaks every later sum. Money is summed
   in integer **cents** — float addition of 200+ order totals is page-order dependent at the 1e-10,
   and a currency cell must not depend on which page an order arrived on.
9. **An empty id list writes `(none)`, never `""`.** An empty cell reads as NOT MEASURED; zero
   orders is a measurement.
10. **Shopify tag counts ONLY — zero ParcelPanel calls.** Nothing on this tab needs a tracking
    event, PP has no weekly budget to spend here, and PP is in a failure state.

**Definitions Dan has NOT stated; the observable one was chosen and is named here.** These are the
only places this tab departs from a directly-measured quantity, and none of them is an invented
business rule:
- **"active hold"** = `_HOLD ∪ _CSHOLD ∪ _FLOWHOLD`, by order. `_UNRESOLVED` is **not** counted — it
  is what replaces the hold tag after 2 CS pings with the ticket closed, i.e. terminal, not an order
  awaiting a decision. It gets its own row.
- **"moved to _HOLD"** is **NOT OBSERVABLE**. The proxy is *orders CREATED on that date that carry a
  hold tag NOW*. It undercounts any hold already released, and a CS hold applied days after the
  order was placed lands on the order's creation date, not the day CS acted. The row label says so.
- **"aging"** = days since order CREATED, not days since held — same reason.
- **`_HOLD created on/after 2026-08-15`** uses the taxonomy cutover date Dan gave in the group DM.

**🔴 DATE BASIS — ALL EASTERN. SETTLED (Kurt 2026-08-25: *"it has to be all Eastern."*)**
- **Which column is today → Eastern.** **An order's calendar date → Eastern.** One basis, no seam.
  `HOLD_TZ = 'America/New_York'` is passed explicitly to every `Utilities.formatDate` call, and
  aging is `ET-today − ET-created` — there is no mixed-basis subtraction left anywhere.
- 🔴 **`Session.getScriptTimeZone()` must never reach a date calculation on this tab.** The
  project manifest's `timeZone` is **America/Chicago**, so the script clock is CENTRAL. Using it
  would stamp tomorrow's column after 23:00 CT *and* misdate orders for an hour every night.
- 🔴 **`createdAt.slice(0,10)` is banned here.** Shopify returns `createdAt` in UTC, so slicing
  it dates every order placed after 20:00 ET (19:00 EST) to TOMORROW. That is precisely the basis the
  08-20 one-shot used, and it is what the backfill below corrects. `holdSweep_` converts with
  `Utilities.formatDate(new Date(n.createdAt), HOLD_TZ, 'yyyy-MM-dd')`.
- The conversion must use a real **tz database**, never a fixed −4: this population reaches back to
  2025-11-26, which is EST (−5). Both the GAS side (`Utilities.formatDate`) and the Python reference
  (`zoneinfo`) do; a regression test asserts a winter order dates correctly.

**🔴 THE FIVE ALREADY-WRITTEN COLUMNS WERE WRITTEN ON THE OLD (UTC) BASIS — and this is the
mixed-basis note that has to survive in the doc.** Columns **B–J (2026-08-12 … 2026-08-20)** hold
HOLDS-OPENED values the 08-20 one-shot computed by UTC-slicing `createdAt`. Everything written from
2026-08-21 onward is Eastern. Until the correction below is applied, **the 08-17 and 08-18 cells of
two rows are on a different basis than the rest of their row** — which is exactly the thing that
burns someone six months from now reading the trend, so it is written down rather than assumed
harmless.

**How big it actually is: FOUR cells, not twelve.** An earlier estimate in this thread said 12 cells
across five columns. That estimate was **wrong twice** and is corrected here: it was computed against
*today's* population (which contains 23 `_CSHOLD` orders that did not exist on 08-20) and with a
fixed −4 offset instead of a tz database. Derived properly, from the 08-20 population:

| date | row | UTC (in the sheet now) | Eastern (correct) |
|------|-----|------------------------|-------------------|
| 2026-08-17 | `Orders moved to _HOLD status  (proxy: …)` | 5 | **6** |
| 2026-08-17 | `   Legacy _HOLD, origin not recorded` | 5 | **6** |
| 2026-08-18 | `Orders moved to _HOLD status  (proxy: …)` | 3 | **2** |
| 2026-08-18 | `   Legacy _HOLD, origin not recorded` | 2 | **1** |

> **Row RENAMED 2026-08-26 (Kurt):** `Orders moved to _HOLD status  (proxy: created that date, hold
> tag present now)` → **`Holds opened (all types, by order-created date)`**. The old name was wrong
> twice after the purge — the row counts ALL hold types (the Flow/CS/legacy breakdown sits beneath
> it), and legacy `_HOLD` is retired. The table above and the `HOLD_ET_BACKFILL` record now describe
> the RENAMED row; values, dates and semantics are untouched. Because the writer resolves rows by
> label (`HOLD_ASSERT_ROW_SHAPE`), the rename is **code-first**: `HOLD_LABEL_ALIASES` in `Code.gs`
> accepts the old cell text transiently, the code deploys, THEN the sheet cell is rewritten
> (service-account one-shot `_outputs/scripts/hold_label_rename_20260826.py`); the alias entry is
> removed once the cell is confirmed renamed. Renaming the cell before the deploy would
> `HOLD_ASSERT_ROW_SHAPE`-kill every hourly refresh in between — never that order.

**Every one of those four cells is ONE order moving.** `#174489`, created `2026-08-18T03:10:32Z` =
**2026-08-17 23:10 EDT**, on `_HOLD` unfulfilled. It leaves 08-18 and joins 08-17; both rows move on
both days because a `_HOLD` order with no `_CSHOLD`/`_FLOWHOLD` counts once in the total and once in
Legacy. **08-12, 08-13, 08-14, 08-15, 08-16, 08-19 and 08-20 do not change at all.** Six orders in
the 08-20 population cross a day boundary (`#94595`, `#156277`, `#167253`, `#170401`, `#171472`,
`#174489`) but only `#174489` lands inside the written range; the other five sit on dates that have
no column on this tab.

**How the corrected numbers were derived — and the trap that was avoided.** Re-running the metrics
today and writing the answer in would be **wrong, and worse than the bug**: these rows say "created
that date and carrying a hold tag NOW", so an August-25 recompute replaces an August-20 measurement
with a different quantity (`_HOLD` went 94 → 46 in between). Instead the 08-20 population was
**reconstructed** from two sources, neither of them an estimate:
1. **Membership** from the id lists the 08-20 run published on the tab — 51 `_HOLD` unfulfilled + 43
   `_HOLD` fulfilled = the published 94, `_CSHOLD` `(none)` = the published 0, `_FLOWHOLD` 2 ids = the
   published 2. The lists **account for every published count**, so who was on which tag that morning
   is a recorded fact. (No `_HOLD` order was also on `_CSHOLD`/`_FLOWHOLD` — derived from the lists,
   not assumed, which is why Legacy tracks the total exactly.)
2. **Timestamps** from live Shopify. `createdAt` is immutable, so today's value is the value it had on
   08-20. All **96 of 96** orders were fetchable; **no cell is underivable**, and none was estimated.

🔴 **The gate that makes those numbers believable.** The same reconstruction was first run on the
**old UTC basis** and required to reproduce every cell the sheet already holds for 2026-08-12 …
2026-08-20. It reproduced **36 of 36 exactly**. Only then was Eastern applied. A reconstruction that
cannot reproduce the logged values is not a reconstruction, and nothing would have been proposed.
Script: `scratchpad/hold_et_backfill.py`, output `hold_et_backfill.json`.

**🔴 THE CORRECTION IS AN ARMED ONE-SHOT. NOTHING AUTO-CORRECTS. STATUS: NOT APPLIED.**
`holdFixEtBasis(dry)` in `Code.gs` is the **only** thing in the file allowed past the write-once rule,
and it is fenced on five sides:
1. **Disarmed by default** — a wet run throws `HOLD_ASSERT_BACKFILL_DISARMED` unless the arm is set.
   **2026-08-26: the arm moved from Script Property `HOLD_ARM_ET_BACKFILL` to a sheet cell** — tab
   `_hold_arm` on the pivot sheet, A1 = `HOLD_ARM_ET_BACKFILL` (exact label, checked), B1 = `1` —
   because the Script Property was settable only in the GAS UI (a human click) and **headless is the
   north star of this report system**: no step may require a human click; DB writes go through
   real-context scheduled tasks; sheet ops through the service account, which has editor and can
   write this cell. (Not the `_state` tab: `saveState_()` clearContents()-wipes that whole tab every
   hourly `build_()` run before the hold path executes, so an arm there would be erased unread.)
2. **Dry by default** — `dry` must be **explicitly** `false` to write (the `ntBackfillFrozen` shape).
   That default is also the trigger guard: an event object is not `=== false`, so a stray binding
   previews instead of writing.
3. **A closed table** — `HOLD_ET_BACKFILL` names the four cells; a cell not in it cannot be reached.
4. **Pre-state checked per cell** — a cell must currently hold the recorded `from`. Already corrected,
   or edited by a human, and it refuses.
5. 🔴 **All-or-nothing** — if any one cell fails its check the **whole set** is refused. The four
   cells are one fact; applying half leaves 08-17's total saying 6 while its Legacy row still says 5,
   and a self-inconsistent column is worse than an uncorrected one.
On success it reads every cell back and **clears its own arm cell** (`_hold_arm`!B1) — a one-shot
left armed is a standing exception to the write-once rule.

**To apply (Kurt's call, after reading the table above):** write `1` into `_hold_arm`!B1 (service
account, headless). The next hourly `refresh` applies it: `build_()` calls `holdEtBackfillIfArmed_()`
in a non-fatal try/catch beside `holdRefresh_()`, which runs `holdFixEtBasis(false)` iff the arm cell
is set — no human click anywhere in the loop. **If it is never armed, those four cells stay on the
UTC basis and this section is the record of why.**

**Two determinism fixes vs the one-shot** (the port is not a transcription of a bug):
- The active-hold union was built by iterating a Python **set**, so `Oldest unfulfilled hold` and the
  cohort id list could differ between two runs over identical data. The port unions in a fixed order
  (`_HOLD`, `_CSHOLD`, `_FLOWHOLD`; first-seen wins).
- `Oldest unfulfilled hold` ties on the created DATE now break on the **lowest order number**.

**🔴 WRITER OWNERSHIP — the scheduled owner and what fails loudly.**
- **Owner: the project's existing hourly `refresh` trigger.** `build_()` calls `holdRefresh_()`
  in a non-fatal `try/catch`, beside the identical Triage call — a hold sweep failing must never
  cost the reship report. **No new trigger.** It is hosted there and not on `ntRefreshCurrentColumn`
  or `hourlyExceptionSweep` because those live in `Notifications.gs` / `Exceptions.gs`, and this
  change is confined to `Code.gs`; and not on a new one because this project has a history of
  triggers that pass an event object and die.
- **Cost is bounded by construction:** the cheap gate runs FIRST. One Sheets read decides whether
  any target cell is blank; if none is, the function returns having made **zero** Shopify calls. Only
  the first invocation of each ET day does the work — 4 paged sweeps + 3 `ordersCount` calls, ~8
  GraphQL round-trips on a 317-order population. Nowhere near the 360s ceiling. Idempotent, so
  24 invocations a day are safe.
- **Fails loudly:** if the newest stamped snapshot column is more than `HOLD_GAP_ALERT_DAYS` (2)
  behind today, `holdGapAlert_` names the gap in the ops DM via `slack_` — once per ET day, and
  BEFORE writing, while the gap is still visible. That is the freshness assert the writer-ownership
  gate requires; it is inside the reader itself rather than in the weekly sweep because the fact it
  guards (a column that can never be recovered) has a two-day fuse, not a seven-day one.
  `HOLD_LAST_RUN_AT` is stamped on every successful write.
- If the host trigger itself dies, the whole reship report dies with it and `refresh()`'s catch
  already alerts. The failure mode this directive closes is the *silent* one: a live report whose
  Hold tab quietly stopped.

**🔴 TRIGGER-ARG GUARD.** `holdRefreshNow(dateIso)` and `holdPreview(dateIso)` are trigger-bindable,
so both reject anything that is not a bare `yyyy-MM-dd` string and fall back to today. A time-driven
trigger passes an **event object** as argument 1 — the bug that killed `paRefreshCurrentColumn_` in
prod for two nights.

**Menu.** `Reship Report → Refresh Hold tab` and `→ Preview Hold tab (writes NOTHING)`. The preview
is the `ntPreviewCurrentColumn` shape: it computes the entire plan, prints every cell it would
write, lists every disagreement, and writes nothing.

**Verification (2026-08-25, before deploy).**
- **Differential, same inputs:** `holdMetrics_` run over a frozen live Shopify capture
  (`scratchpad/hold_rows_0825.json`, 317 orders) matches the Python reference over the *same*
  capture on **36 of 36** snapshot metrics and **140 of 140** daily origin cells, exactly — both sides on the **Eastern** basis, so the differential is not silently comparing two quantities.
- **Behavioural, real tab:** `holdRefresh_` driven over the actual live `Hold` tab contents in a
  stubbed Sheets/Shopify context — **73 assertions**, all passing: column J is byte-identical after a
  wet run; a second run is a no-op with zero Shopify calls; a pre-filled cell is excluded and
  reported; an inserted row does not shift a single target; a renamed label throws; a date past the
  header appends one column and a date behind it refuses; all-seven-zero refuses and writes nothing; every swept order is dated on its Eastern day including a winter (EST) one; and the armed one-shot writes exactly its four cells, changes nothing else, refuses disarmed, refuses on a second run, and refuses the whole set on one bad pre-state.
  `scratchpad/hold_port_test.js`.
- **The 08-20 fixture is NOT reproducible and was not faked.** `hold_counts.json` looks like a
  capture of the fixture run and is not one — it was taken at 08:42:41Z, 16½ minutes before
  `hold_metrics.json` (08:59:19Z), and in that window `#173703` moved `_HOLD`→`_FLOWHOLD`, two orders
  lost their `_SHIP_2026-08-24` tag, and one gained `_DUP_SFO_10MINS`. Where the two populations
  agree the reference reproduces the fixture exactly (**20/20** snapshot metrics, **30/32** daily
  cells); **every** divergence traces to one of those named input differences, none to logic — the
  two daily cells that differ are 08-15's Flow/Legacy split, which is `#173703`'s own creation date.
- **`ordersCount` == exact-tag sweep on all seven tags** (measured live 08-25), which is what
  licenses the count-only query for the three `_FLOWHOLD` reason tags.

**Implementation:** `Code.gs` — `holdRefresh_` (writer), `holdMetrics_` (pure, testable),
`holdFetch_`/`holdSweep_`, `holdGates_`, `holdRowMap_`, `holdAppendColumn_`, `holdGapAlert_`,
`holdRefreshNow`/`holdPreview` (entry points), `menuRefreshHold`/`menuPreviewHold`, and the armed one-shot `holdFixEtBasis` + `HOLD_ET_BACKFILL`. All 41 new
top-level names are `hold`/`HOLD_`-prefixed and declared in `Code.gs` only (collision sweep across
the four deployed files: NONE). The tab is named **`Hold`**, not `_HOLD` — `_HOLD` is the Shopify tag.

**Open, NOT built (recommendation only).** A `Last refreshed (UTC)` row on the tab would make
staleness visible to a human reading the sheet rather than only to the ops DM. It changes the row
list, so it waits for Dan/Kurt.

#### D33 addendum — UNFULFILLED-ONLY SEMANTICS CUTOVER (Kurt, 2026-08-26)

> Kurt, verbatim: **"I don't need row 12 for hold."** → **"we only care about open unfulfilled
> holds"** → **"all types. if it was shipped on hold, then it was fulfilled, so not on hold"**.

**The new definition.** An order counts as ON HOLD only while its fulfillment status is
`UNFULFILLED` — for **every** hold tag (`_HOLD`, `_CSHOLD`, `_FLOWHOLD` alike). A fulfilled order
still carrying a hold tag is tag noise, not a hold. Every open-holds row on the tab is now
unfulfilled-only; the fulfilled/unfulfilled split rows are retired because the split is gone —
**unfulfilled IS the number**. Readers use `Orders on _HOLD  (LEGACY - migration backlog,
target 0)` (physical row 3) as the actionable `_HOLD` number.

**🔴 THE MIXED-BASIS RECORD (this is the paragraph that has to survive).** Columns stamped **on or
before 2026-08-26** hold **snapshot-basis** values: all uncancelled orders per tag, fulfilled
included (08-20's published 94 `_HOLD` contained 43 fulfilled noise; unfulfilled was 51). Columns
stamped **after** the new writer deploys are **unfulfilled-only** (the same 08-20 row would have
read 51). Write-once (rule 1) means the old columns are never rewritten — the trend line crosses a
definition seam at the deploy date and must be read on two bases, exactly like the UTC→Eastern seam
above. The 2026-08-26 column was stamped by the OLD writer before this deployed; the first
unfulfilled-only column is the first one stamped after deploy (expected 2026-08-27 — verify against
the deploy time and correct this sentence if the 08-26 column was still blank at deploy).
**A gated backfill of old columns to the new basis is NOT derivable**: per-tag membership on those
dates was never persisted except 08-20 (which published its id lists, pre-split 51/43 — so 08-20's
`_HOLD` row alone IS derivable at 51), and even the 08-20 lists carry no fulfillment status for
`_FLOWHOLD`/`_UNRESOLVED`, and "fulfilled as of that date" is not reconstructible from today's
statuses. No backfill is planned; this record is the mitigation. Until the physical retired rows
are deleted, the old split (rows 11/12/13) remains readable in the historical columns.

**Per-row verdicts (the full 40-row disposition):**
- **RETIRED (4 rows — writer neither writes nor asserts them; physical rows deletable):**
  `_HOLD unfulfilled  (actionable backlog)` (physical row 11 — redundant: row 3 now IS this
  number), `_HOLD fulfilled  (shipped, tag never cleared - noise)` (row 12 — noise by definition),
  `_HOLD other fulfillment status` (row 13), `List of Order IDs - _HOLD fulfilled (clear the tag)`
  (row 53 — 🔴 this was the tag-cleanup work-list; fulfilled-with-tag orders are now invisible on
  the tab, accepted per "we only care about open unfulfilled holds").
- **REDEFINED to unfulfilled-only (11 rows):** `Orders on _HOLD` · `Orders on _CSHOLD` ·
  `Orders on _FLOWHOLD` · `Total on active hold (union)` · `Legacy share of active holds` ·
  `_HOLD created on/after 2026-08-15` · `_HOLD also carrying _UNRESOLVED` · the three
  `Customers with N Orders on _HOLD` rows · `_FLOWHOLD with no reason tag`; plus the two id lists
  `List of Order IDs - _CSHOLD` / `- _FLOWHOLD`.
- **UNCHANGED — already unfulfilled-only (verified in code, not assumed):** the three
  `$ held` rows (DOLLARS AT REST), the AGING section (denominator, three buckets, oldest
  order/days), both `_SHIP_` cohort rows and their id lists, `List of Order IDs HELD`,
  `List of Order IDs - _UNRESOLVED (unfulfilled)`.
- **UNCHANGED — deliberately all-fulfillment (2 exceptions, flagged):**
  1. `Orders on _UNRESOLVED  (terminal, not an active hold)`: `_UNRESOLVED` is terminal, never an
     "open hold" (D33 definitions), so Kurt's rule does not reach it — the row tracks the terminal
     bucket's size. The code does not entangle it with the open-holds rows.
  2. The four **HOLDS OPENED** daily rows: they measure hold *openings* by `createdAt`, not open
     holds; rebasing them would put a **third** basis inside partially-written rows and would
     invalidate `HOLD_ET_BACKFILL`'s recorded `from`/`to` values. They keep the full population.
- **Section headers** (`-- OPEN HOLDS BY TAG - snapshot, uncancelled --` etc.) are sheet text the
  writer does not own; the "snapshot, uncancelled" wording is now stale and may be edited by hand.

**Machinery notes.**
- The fulfilled/other populations are still **computed** — as `INTERNAL` keys in `holdMetrics_`
  that never reach the sheet (the write plan iterates `HOLD_ROWS` only) — because the fulfilment
  partition gate needs them: published `_HOLD` (unfulfilled) + fulfilled + other must equal the
  sweep total, with `other` now counted **directly** rather than by subtraction (the old
  subtraction made that gate an identity that could never fire). `HOLD_ASSERT_ALL_ZERO` is
  untouched: it fires on the raw sweeps, before any fulfillment filter.
- `HOLD_ROWS` is now a **subset** of the physical tab. Retired labels are ignored by
  `holdRowMap_`, so **the physical rows 11/12/13/53 can be deleted at any time after this
  deploys** — the row map re-derives every position by label each run, and `holdFixEtBasis`
  addresses its four cells by label+date through the same map (verified: nothing in the project
  addresses the Hold tab by absolute row number). Deleting them BEFORE deploy kills the refresh
  (`HOLD_ASSERT_ROW_SHAPE`), which is why the code landed first. Renaming a surviving label still
  requires sheet + `HOLD_ROWS` to move in the same moment.
- Until the pre-cutover columns' cells disagree-check ages out (i.e., for the rest of deploy day),
  every redefined row logs a `Hold DISAGREEMENT` against the already-stamped 08-26 column on each
  hourly run — expected, write-once keeps the sheet's value, and it stops with the next day's
  column.
- **Verification (2026-08-26):** new Python reference `scratchpad/hold_ref_unf0826.py`
  (`hold_ref.py` kept untouched so the pre-cutover 36/36 + 73-assert record stays re-derivable);
  differential vs `holdMetrics_` over the frozen 08-25 capture: **35/35** snapshot metrics and
  **140/140** daily cells identical; behavioural suite extended to **79 assertions**, all passing —
  including: no retired row is planned, no `INTERNAL` key leaks into the plan, retired physical
  rows stay blank after a wet run, the mangled-fulfilment partition still closes (now a real
  check), and the entire `holdFixEtBasis` section unchanged (its four-cell table still resolves by
  label+date). `scratchpad/hold_port_test.js`.

#### D33 addendum 2 — MIGRATION COMPLETE: THE LEGACY `_HOLD` ROWS ARE RETIRED (Kurt-approved, 2026-08-26)

**The event.** The `_HOLD` migration reached its target the same day the unfulfilled-only cutover
deployed: live non-cancelled `_HOLD` = **0** (the 43 fulfilled tag-noise orders were stripped; the
last 2 unfulfilled were externally resolved). New holds land only on `_CSHOLD`/`_FLOWHOLD` —
`_HOLD created on/after 2026-08-15` has read 0 since 08-25. The migration-backlog rows' target-0
watchdog job is done, so the rows are retired: code first (labels removed from `HOLD_ROWS`,
deployed), then the physical rows deleted via the service account — the same two-step as the
morning's row removal.

**Retired 2026-08-26 (8 labels + 1 section header; positions = final pre-deletion):**
- Row 3 `Orders on _HOLD  (LEGACY - migration backlog, target 0)` — target reached.
- Row 8 `Legacy share of active holds` — its numerator is structurally 0; the row could only ever
  read 0.00% again. Retired WITH the legacy row (deliberate decision, recorded here).
- Row 10 section header `-- MIGRATION BACKLOG - _HOLD must reach zero --` and its whole block:
  row 11 `_HOLD created on/after 2026-08-15`, row 12 `_HOLD also carrying _UNRESOLVED`, rows
  13–15 `Customers with 1/2/2+ Orders on _HOLD`.
- Row 49 `List of Order IDs HELD  (_HOLD, unfulfilled)`.

**Kept, deliberately:**
- `Total on active hold  (_HOLD + _CSHOLD + _FLOWHOLD, union)` — label AND computation unchanged.
  The sweep still queries `_HOLD` (below), so the union still counts any `_HOLD` member; with the
  tag structurally at zero it equals the `_CSHOLD`+`_FLOWHOLD` union, and a regression moves this
  published number too. Do NOT drop `_HOLD` from the union while the tag is still queried.
- `$ held on _HOLD  (unfulfilled)` (DOLLARS AT REST), the AGING block, the HOLDS OPENED daily rows
  (including `   Legacy _HOLD, origin not recorded` — they measure historical openings and back
  `HOLD_ET_BACKFILL`), the HELD-INSIDE-A-LIVE-COHORT block, and every `_CSHOLD`/`_FLOWHOLD`/
  `_UNRESOLVED` row.

**🔴 The sweep KEEPS QUERYING `_HOLD`.** ~380 cancelled orders still carry the tag, and a
regression — anything re-applying `_HOLD` to a live order — must stay detectable. The deleted
rows' watchdog function is replaced by the **`HOLD_LEGACY_REGRESSION` tripwire** in
`holdRefresh_`: if the non-cancelled `_HOLD` sweep ever returns > 0 orders again it logs every
run and DMs the ops reader once per ET day (`HOLD_LEGACY_ALERTED_ON` property, gap-alert
pattern), naming up to 20 order ids. `_HOLD unfulfilled` moved from a published row to the
`INTERNAL _HOLD unfulfilled` key; the fulfilment partition gate and the union-impossibility gate
still close against it. The old ID-list-vs-count gate retired with its rows (with neither side
published it degenerated to an identity). `HOLD_CUTOVER` (the 08-15 taxonomy date) left the code
with its only consumer; the date lives in this record.

**🔴 History.** Write-once stands: the retired rows' already-stamped historical values rode along
in the sheet until physical deletion. After deletion, the pre-deletion history for those rows
lives ONLY in git (this repo's snapshots) and the PRE-FIX tab snapshot at
`_outputs/cache/hold_tab_prefix_snapshot_2026-08-26.json` — taken immediately before the
`deleteDimension` calls. Rows were deleted in DESCENDING order, each label re-verified against
column A immediately before its deletion (abort per-row on mismatch).

### D34 — THE `By-State` BLOCK SUMMED **186** AGAINST A HEADLINE OF **7**, AND NOTHING CAUGHT IT (2026-08-25)

> `Lost in Transit` published `_SHIP_2026-08-17` with `Not Arrived` = **7** at the top of the tab and
> a `By State` block underneath it summing **186** — `ME · Not Arrived` = **19** against **19** total
> Maine boxes (the entire state), `NC` = 42, `LA` = 10, `MS` = 10, `DE` = 9. `By Hub` summed **88**
> off a single cell (`RMFG choice (2+ hubs open)` = 79); `By Carrier` summed 10. `_SHIP_2026-08-10`
> was corrupt the same way on **both** tabs. Five of the twelve published cohort columns were wrong,
> for weeks, on the numbers Dan actually reads.

**The failure, negatives first.**

1. 🔴 **A COUNT ROW THAT FALLS TO ZERO IS NEVER WRITTEN, SO IT KEEPS ITS HIGH-WATER MARK FOREVER.**
   `paValues_` built each `dim||{bucket} · {grain}` key by INCREMENTING over matching records, so a
   bucket with **zero** matches this run emitted **no key at all**. `paWriteOwned_` is label-driven
   and skips any label it has no key for — correct behaviour, and exactly why Dan's formula rows
   survive — so the cell silently kept the number from the last run where the bucket was non-empty.
   `Not Arrived` and `3+ Day` DECAY across the week (that is the entire point of the daily self-heal),
   so every bucket that emptied froze at its peak and the blocks over-summed a little more each day.
   The published number is a **stale high-water mark**, not a mis-computation: the run computed 0 and
   had nowhere to put it.
2. 🔴 **THE ONE BLOCK A READER SPOT-CHECKS IS THE ONE BLOCK THAT COULD NOT EXPRESS THE BUG.**
   `By Box` was correct in **every** column, because its three buckets (Regular Box / Medium Tray /
   Large Tray) are never empty. Hub, carrier and state have buckets that empty routinely — a state
   with one box, a hub that closes (Indianapolis), a carrier that goes away (Veho), and above all
   `RMFG choice (2+ hubs open)`, a **residual** bucket that DRAINS to ~0 during the week as routing
   tags are corrected. That is why it survived every eyeball: the block people check is structurally
   incapable of showing it.
3. 🔴 **THE FIX WAS ALREADY IN THE FILE, ONE SCREEN AWAY, WITH THIS EXACT BUG NAMED IN ITS COMMENT.**
   The per-hub TNT1 rows (D22b) are zero-filled, and the comment justifying it says *"a count row
   that silently keeps LAST run's value is the stale-number bug"* and even notes that the sibling
   dimension rows "emit only non-zero". The reasoning was correct, written down, and never applied
   to the rows next to it. A guard written for one row does not cover the row beside it.
4. 🔴 **NOT a partial-label / cross-section collision** — the first hypothesis, and it was ruled out
   with evidence, not assumed. The section-scoped `dim||label` keys and `paColumnByKey_`'s
   header-tracking are working: in `_SHIP_2026-08-17` the five *live* hubs sum to exactly the
   headline (473+353+471+451+569 = 2,317 = `Arrived`) and the entire excess is one residual bucket
   that should read 0. A collision would have moved the live buckets too.
5. 🔴 **NO ASSERT HAD EVER LOOKED BELOW THE TOP BLOCK.** `PA_ASSERT_TOTAL_PARTITION`,
   `PA_ASSERT_NOTARRIVED_PARTITION` and `PA_ASSERT_OBSERVATION_PARTITION` all partition the HEADLINE
   block; `PA_ASSERT_TNT1_SUBSET` / `PA_ASSERT_PENDING_SUBSET` bound nested rows against their
   parent. Roughly 120 of the ~150 numbers on each tab — every dimension cell — had no invariant on
   them at all. Every headline assert passed on all five corrupt columns.

**🔴 The rules.**

1. **ZERO-FILL EVERY BUCKET IN THE COHORT'S UNIVERSE.** `paValues_` now emits an explicit `0` for
   every `{bucket} · {grain}` where that bucket appears in **at least one** grain this run.
2. **THE UNIVERSE IS THIS COHORT'S BUCKETS, NOT THE SHEET'S ROWS. Blank still ≠ zero.** A bucket with
   no boxes at all in the cohort emits NOTHING and its cells stay **blank** — `0` asserts "shipped,
   and none are in this grain", blank asserts "did not ship / did not exist". Never widen the
   zero-fill to the sheet's row list: that would stamp `0` into Indianapolis (closed) and into states
   nobody ordered from, which is the A5 / D19 error in the other direction.
3. **`PA_ASSERT_SECTION_SUM` — every section block that partitions a headline must sum to it.**
   Throws, named, from the refresh path.
4. 🔴 **IT RUNS ON THE SHEET, AFTER THE WRITE — AND THAT ORDERING IS THE RULE, NOT AN ACCIDENT.**
   - *Not on the computed value map*: the headline and the buckets come out of the same loop over the
     same records with the same predicate, so a map-level version is **derivable from the code that
     produced it** and is not an independent check — it cannot fail on this defect and did not.
     The defect lives in the gap between what was computed and what the column ENDS UP HOLDING, and
     only a read-back can see that gap.
   - *Not before the write*: the corrupt column is repaired **by** the write, so a pre-write refusal
     would freeze the damage and refuse every run forever. A fail-closed guard must not close the
     door on its own fix. (Compare `PA_ASSERT_HEADERLESS_COLUMN`, which correctly refuses *before*
     writing, because writing onto that damage makes it worse.)
   - A **dry run** asserts the simulation — the current column overlaid with this run's values,
     restricted to labels that exist as rows — so the refusal surfaces before anything is armed.
5. **A computed bucket with NO ROW now REFUSES the column, where it used to be a `⚠️` log line.**
   That is the Swedesboro gap (D19: `· Arrived = 570` computed and written nowhere for days) turned
   into a hard stop, because a block missing a bucket does not partition its headline either. The
   remedy is the human-invoked row tool (`addSwedesboroRows` / `paAddHub_`), and the refusal is loud
   via the D20 wrapper. Cost of this choice, stated: a brand-new **state** has no equivalent tool, so
   its first appearance will refuse the run until a row is added by hand.
6. **`auditSectionSums()` REPORTS, it does not throw.** Read-only, every column on both tabs. The
   refusal belongs on the write path; this is how a human inspects the frozen history the refresh is
   not allowed to touch. It takes no arguments, so a time-driven trigger's event object cannot be
   mistaken for one (the bug that killed `paRefreshCurrentColumn_` for two nights).

**Proof it fires on the real thing, not on a fixture.** Run against the ACTUAL bytes read out of the
live sheet (`paColumnByKey_` over the unformatted grid, every cohort column, both tabs):

| Column | Lost in Transit | TnT2 |
|---|---|---|
| `_SHIP_2026-07-13` · `-07-20` · `-07-27` | ✅ pass | ✅ pass |
| `_SHIP_2026-08-03` | 🔴 `state · Not Arrived` 38 vs 37 | ✅ pass |
| `_SHIP_2026-08-10` | 🔴 hub 21 · carrier 23 · state 149, vs 16 | 🔴 hub 91 · carrier 98 · state 107, vs 89 |
| `_SHIP_2026-08-17` | 🔴 hub 88 · carrier 10 · state 186, vs 7; `hub · Arrived` 2,318 vs 2,317 | 🔴 `hub · 2 Day` 2,265 vs 2,264 |

Seven columns clean, five refused — and the five refused are exactly the five independently found to
be corrupt. Replaying the OLD writer over a seeded column reproduces the defect shape (block sums 20
against a headline of 1) and the assert fires; the NEW writer passes the same replay.

**🔴 The already-written cells: ONE column self-heals, the rest are NOT re-derivable.**

- **`_SHIP_2026-08-17` (col G) — re-derivable, by the ordinary refresh, no one-shot.** At age 8 it is
  still script-owned (D15), so the first armed run after this change rewrites every dimension cell
  with the zero-fill in place and the post-write assert confirms it. 🔴 **It freezes at age 10 =
  2026-08-27.** Deployed and run before then, it repairs itself; after then it is frozen wrong like
  the others. That deadline is the only time-critical part of this change.
  > 🔴 **AMENDED BY [D37](#d37--a-bucket-that-drains-to-zero-boxes-leaves-the-universe-and-the-repair-refuses-itself-2026-08-26) (2026-08-26): this repair claim was INCOMPLETE.** The zero-fill
  > cannot reach a bucket that drained to zero boxes in the ENTIRE cohort (`RMFG choice (2+ hubs
  > open)` on wk0817), so the first repair run wrote TnT2 and then the post-write assert correctly
  > refused it — the repair self-refused the day before the freeze. D37 adds the reclaim that
  > closes the gap. Everything else in D34 stands unchanged.
- **`_SHIP_2026-08-10` (col F, both tabs) and `_SHIP_2026-08-03` (col E, `Lost` state block) — NOT
  re-derivable, per cell, and deliberately left alone.** The frozen headline is the reading taken
  inside D32's measurement window; **the dimension split of that reading was never persisted
  anywhere**. `_nt_sweep` holds Klaviyo sweep signatures and volumes, `_pp_cache` holds carrier and
  transit days per order with no cohort or arrival snapshot, `_state` is the reship ledger — none of
  them records which boxes were Not Arrived at the freeze. A recompute today measures a **later
  world** (boxes Not Arrived at the freeze have since delivered), so it would restate the headline as
  well as the split — the rebuild A1/D4 forbid, and the one Kurt already refused once for column D
  when he took an even haircut instead. **No value is estimated and no correction one-shot is
  built**: which specific cells the writer skipped is not recoverable, only that each block over-sums
  (Lost F: hub +5, carrier +7, state +133; Lost E: state +1; TnT2 F: hub +2, carrier +9, state +18).
  Restating them is a Kurt decision with no derivation behind it, so it is not offered.

**Implementation:** `PivotAnalytics.gs` — zero-fill inside `paValues_`; new `paHeadlineLabel_`,
`paSectionSums_`, `paAssertSectionSums_`, `auditSectionSums`. All four names are unique across the
four deployed files (collision sweep: 417 top-level names, no duplicate; `node --check` clean per
file and over the concatenation, `PivotSheet.gs` excluded as local-only). No new trigger-bindable
function takes an argument. Grain resolution splits on the **last** ` · ` and compares the whole
token — never `endsWith`, never a substring — because `Not Arrived` ends with `Arrived` and this file
has burned four separate times on partial-label matching.

### D35 — `Carrier Mix`: FEDEX 2DAY IS ITS OWN ROW, ONTRAC AND LASERSHIP ARE ONE CARRIER, AND THE COST HALF RUNS ON A SECOND CLOCK (Kurt 2026-08-25)

> Kurt: *"we should be hammering ontrac as much as possible."* · *"fedex 2day air is separate from
> home delivery."* · *"have a second row under each carrier service lane outline cost at a high
> level. of course those cells have to be on a different refresh because digital ocean will get
> those invoices later."* · *"the fences should resolve on monday once they're actually assigned to
> a carrier."*

A standing pivot: ship weeks as **COLUMNS** (last 4–5 `_SHIP_` Mondays), one **count** row per
carrier·service lane, a **cost** row paired directly beneath each, `n (pct%)` cells, a `Total`.
It is a tripwire for OnTrac share erosion — a share drop has to be visible at a glance across
weeks, not something a reader computes.

**Implementation:** `ShippingReports/carrier_mix_pivot.py` (read-only, `connect_ro`).
**Not** in the `Running Reship` Apps Script project — see "Why it is not a `.gs` tab" below.

#### 🔴 The failures this exists to prevent, negatives first

1. 🔴 **FEDEX 2DAY MERGED INTO FEDEX GROUND HIDES THE AIR SPEND, AND THE TABLE STILL SUMS TO THE
   COHORT.** Air is the expensive escape hatch: measured over the last five cohorts FedEx 2Day
   bills **$23.82/box** against **$16.29–16.48** for FedEx Ground-HD and **$6.73–6.92** for OnTrac
   Ground. Folded into Ground, 175 air boxes read as a slightly pricier ground row and nothing
   about the table looks wrong. **2Day is its own row, always, and it never merges into
   Ground-HD, into UPS, or into OnTrac.**
2. 🔴 **ONTRAC AND LASERSHIP COUNTED AS TWO CARRIERS HALVES THE SHARE THE TABLE EXISTS TO WATCH.**
   They are one carrier under two names (D5). Carrier normalization is
   `ShipRouting/lib/canon.normalize_carrier` and is **never re-implemented here** — it is imported.
   A local copy is how the two spellings drift apart again.
3. 🔴 **NEVER INFER THE SERVICE FROM A PARTIAL OR SUBSTRING MATCH ON A CARRIER/SERVICE LABEL.**
   This project has shipped **four** separate partial-label-matching bugs into these reports (the
   `Unknown` section collision, the `of which` substring assert, the verifier pair-finder, the
   monotonicity baseline). Comparison is on the **whole canonical token**, dimension-scoped.
   `"Home Delivery" in label` would pull a `FedEx 2Day` box into Ground-HD.
4. 🔴 **AIR IS TESTED FIRST AND POSITIVELY; GROUND-HD IS WHAT A BOX FALLS TO ONLY AFTER AIR IS
   RULED OUT.** The other ordering — "is it Home Delivery? no → must be air" — silently
   reclassifies anything the service map does not recognize.
5. 🔴 **A FENCE IS NOT A CARRIER.** A `!NO …` stack, a bare `!ANY - <Hub>_AHB!`, or any row where
   more than one hub is left open has **no carrier** until RMFG picks at the dock. Distributing
   those boxes across OnTrac/FedEx/UPS by hub default or by "what usually happens" is fabrication
   and inflates whichever row was assumed. On `_SHIP_2026-08-24` that is **1,397 of 2,500 boxes**
   (742 bare `!ANY`, 609 `!ANY FedEx`, 46 fence-only) — over half the cohort.
6. 🔴 **A BOX THAT MATCHES NO ROW MUST NOT VANISH** (the hub-literal undercount class). Every box
   lands in exactly one row and `CM_ASSERT_ROWS_SUM_TO_COHORT` proves it on the published numbers.
   Veho is the live instance: it is dead as a carrier (Kurt 2026-08-02) but `_SHIP_2026-07-27` —
   inside the default 5-week window — still carries **372 Veho boxes**. They go to
   `Other / Unmapped`, itemized by name in the run notes, never quietly into OnTrac.
7. 🔴 **AN EMPTY COST CELL IS "NOT INVOICED YET", NEVER `$0`.** A zero claims the lane cost
   nothing. Blank ≠ zero, and nothing downstream sums a blank.

#### The four rows (exactly these, in this order), plus two residuals

| Row | Contents |
|---|---|
| `OnTrac Ground` | carrier `OnTrac` (LaserShip folded in), air ruled out |
| `FedEx Ground-HD` | carrier `FedEx`, air ruled out — **FedEx Ground and FedEx Home Delivery are ONE row** |
| `FedEx 2Day Air` | carrier `FedEx`, service level `2Day` |
| `UPS Ground` | carrier `UPS`, air ruled out |
| `Other / Unmapped` | anything with no row: Veho, a UPS 2Day, any `Overnight`, an unrecognized carrier, or a box whose sources disagree. **Itemized by reason every run, never a bare number.** |
| `Unresolved / Pending` | cohort orders with **no label yet** — the fence has not resolved |

**Why the Ground/Home-Delivery merge is not a loss of information:** `!ANY FedEx - <Hub>_AHB!`
(the `FENCE_FEDEX_HD` fence, ROUTING_RULES §10) deliberately leaves RMFG to pick Ground vs Home
Delivery off the residential/commercial flag we do not carry. **648 of the 717** FedEx boxes on
`_SHIP_2026-08-24` carry no service signal at all for exactly that reason. The merge is what makes
that fence unambiguous — both outcomes are the same row and the same economics. Splitting them
would put 648 boxes into a coin-flip.

#### Data contract

- **Source:** `fulfillments`, `tags LIKE '%_SHIP_<Mon>%'`. **Join on `order_number`, never
  `tracking_number`** (FedEx reuses them, D12), and normalize `#132940` vs `132940` — that key
  format mismatch has produced confident zeros here twice.
- **Carrier = `fulfillments.tracking_company`** through `canon.normalize_carrier`. Measured
  2026-08-25: never blank on any `_SHIP_` row, and the only unmapped value in the whole table is a
  single `Other` from 2026-04-27.
- **Service, in priority order: carrier invoice → `delivery_status.service` → applied routing
  tag.** A routing-tag signal counts **only when the tag names the carrier that actually carried
  the box** — a `!FedEx 2Day` tag on a box RMFG handed to OnTrac describes a plan that did not
  happen. `is_any` carries **no** service and must never be read as one.
- 🔴 **`delivery_status.service` IS A DEAD SIGNAL — NULL on all 118,909 rows**, every carrier,
  every cohort (measured 2026-08-25). The contract names it, the code reads it, and its coverage
  is **printed every run**: a signal that is silently always-absent is indistinguishable from one
  that is silently always-wrong. Today the routing tag is the sole ship-time service source.
- **Volume-normalize:** pct of the week total; denominator = the **full cohort** (the `Total` row).
- 🔴 **DB read-only, absolutely.** `appyhour_lib.db.connect_ro`, never `sqlite3.connect`, never a
  writer (WAL corruption 6/27 + 7/01). The ONLY write anywhere in the tool is the Sheets API
  repaint of the `Carrier Mix` tab behind `--write-sheet` (D35c, Kurt-authorized 2026-08-26) —
  it touches no other tab and never touches `shipping.db`.
- 🔴 **The ship-week window is derived from the CALENDAR, anchored on Monday** — never from the
  newest tag in the data and never from a sheet header. Reading the data pins the window to what
  already shipped, so a new cohort can never be discovered and the table silently stops walking
  forward. Non-Monday `_SHIP_` tags exist (`_SHIP_2026-07-24`, 7 boxes — a drift-in leg) and are
  not ship weeks.

#### 🔴 Reproduce-gate — this ran BEFORE anything was extended to other weeks

> 🔴 **SUPERSEDED BY D35d (2026-08-27) — DO NOT RESTORE THESE LITERALS.** The table below is the
> **Monday-leg** reading of `_SHIP_2026-08-24` and is kept only as the historical record of the
> first run. Pinning the gate to it made the gate break every Tuesday night when the Dallas leg
> landed; the cohort is **2,545** once that leg is in. The live gate is structural — see **D35d**.

`_SHIP_2026-08-24`, tag basis, reference computed 2026-08-25 (Monday leg only):

| row | reference | computed |
|---|---:|---:|
| OnTrac Ground | 1763 | **1763** ✅ |
| FedEx Ground-HD | 648 | **648** ✅ |
| FedEx 2Day Air | 69 | **69** ✅ |
| UPS Ground | 20 | **20** ✅ |
| **Total** | **2500** | **2500** ✅ |

Exact on the first run. `--verify-gate` re-runs it; `main()` **refuses to extend to other weeks**
if it fails. Inputs are the trap, not the formula — a recompute that has not reproduced a number
the system already produced is not evidence.

**The reference was taken POST-resolution.** All 2,500 boxes were fulfilled on 08-24 itself and
every one carries a non-blank `tracking_company`, so no fence was open in the *carrier* dimension
when it was measured. The service dimension is a different matter — see the air reconciliation.

**Exclusivity proven, not asserted.** Across all five cohorts, **zero** boxes satisfy both the air
and the ground test (`CM_ASSERT_AIR_GROUND_EXCLUSIVE` refuses the column if one ever does). On
08-24 the 69 air boxes are exactly the 69 the routing tag alone yields, and none of them also
matched Ground-HD.

#### 🔴 How a fence resolving is OBSERVABLE — the real signal, not an inferred one

**A `fulfillments` row IS the resolution.** The row only exists once a label is cut, and
`tracking_company` is populated on every one of them — RMFG's dock pick materializes as the label.
So:

- **Before the labels exist the cohort has NO rows in `fulfillments` at all.** A pre-Monday column
  is not "partially pending", it is empty, and it is **not written** (`CM_NO_LABELS_YET`).
- `Unresolved / Pending` = cohort orders with **no label yet**, computed as a **SET DIFFERENCE on
  `order_number`** against `shopify_orders`. 🔴 Never a subtraction of two counts: labels outnumber
  open orders whenever an order cancels *after* its label is cut, so `orders − labels` goes
  negative on a healthy week and says nothing about which boxes are waiting.
- 🔴 **`shopify_orders` is a replica that goes stale silently** (the 7/07 dead-cadence class).
  Measured 2026-08-25 it held **1,005** open orders for `_SHIP_2026-08-24` against **2,500** labels
  — 40% complete. A set difference against 40% of a cohort under-reports pending toward zero,
  which is the flattering direction. Below `REPLICA_MIN_COMPLETENESS = 0.98` the cell reads
  **`unknown`**, never `0`, and **the column cannot freeze**. The floor is measured, not chosen:
  the replica sits at 99.9–100% on four of the five live cohorts and at 40% on the fifth.
- **Pending shrinking to 0 is the freshness signal.** A column still carrying pending days after
  its Monday means the assignment was never recorded. Live instance: `_SHIP_2026-08-17` at age 9d
  still shows **13 orders with no label** (`CM_FENCES_OPEN`).

#### 🔴 TWO INDEPENDENT CLOCKS IN ONE TABLE — and neither may freeze the other

Freezing the whole column when the counts settle would permanently lock the cost cells at whatever
partial invoice data existed that day. Counts and costs are frozen **separately**.

**Clock 1 — COUNT rows. Freeze when the fences have resolved (`pending == 0`).**
🔴 Frozen thereafter because the service half is read from the routing **TAG, which is MUTABLE
after ship** — `_SHIP_2026-08-10` alone logged **376 corrective tag writes**
(`_outputs/logs/wk0810_corrective_delta.jsonl`). A recompute on day 20 compares day-0 carriers
against tags that are no longer what shipped: this number **degrades with age instead of
converging**, exactly as D23 found for `Routing Match`. `CM_ASSERT_FROZEN_COUNTS` **refuses**, it
never repairs — the ship-time reading is unrecoverable once overwritten.

- **Backstop `COUNT_FREEZE_MAX_AGE_DAYS = 10`** (reusing `PA_MATURITY_DAYS`, D15, rather than
  inventing a constant). Without it a single order that is never labelled holds a column
  provisional forever and the mutable tag keeps being re-read for months. `_SHIP_2026-07-27` sat
  at `pending = 1` at age 30d. On force-freeze the residual is **recorded** as
  `residual_pending`, loudly, never swallowed.

**Clock 2 — COST rows. Per LANE, frozen at invoice coverage ≥ `COST_COMPLETE_COVERAGE = 0.98`.**

- 🔴 **The threshold is measured, not chosen for roundness.** Invoice coverage asymptotes at
  98–100% and **never reaches 100** — `_SHIP_2026-07-06` is still at 98% at age 51d, because a
  residue of boxes is cancelled or undeliverable and is never billed. A 100% gate would never fire
  and every cost cell would stay provisional forever.
- **A partial cell leads with its coverage** (`58% inv · $6.73/bx · $5,967 so far`). Spend scales
  with coverage — a lane 58% invoiced shows a real-looking total that is 42% low — so the
  percentage goes **first**; putting it after the dollars is how a partial reads as complete.
- 🔴 **The per-box unit divides by INVOICED boxes, not by total boxes.** Dividing measured dollars
  by a population that was never billed understates the unit by exactly the uninvoiced share. The
  denominator has to come from the same place as the numerator. (The unit is coverage-independent
  and stays comparable across weeks; the spend is not.)
- **`Total $` is emitted only when EVERY lane in that week is complete.** A sum over a mix of
  frozen and partial lanes is a real-looking number that is low by an unknown amount; it renders
  `partial (3/4 lanes)` instead.

#### Measured invoice lag — instrumented, not recalled

Cost authority = **`shipments`** (the carrier-invoice ingest, fed by the `invoices` email ledger:
OnTrac CSV / UPS CSV / FedEx XLSX per `ShippingReports/CLAUDE.md`). `cost` is non-NULL wherever a
row exists. Invoice arrival, from `invoices.email_date` against the cohort Monday:

| invoice | ship week | landed | lag |
|---|---|---|---:|
| OnTrac `AHB_00416` | 8/10 | 2026-08-21 | **11d** |
| FedEx `AHB_00422` | 8/10 | 2026-08-25 | **15d** |
| FedEx `AHB_00421` | 8/3 | 2026-08-25 | **22d** |
| FedEx `AHB_00415` | 7/27 | 2026-08-21 | **25d** |

Coverage by cohort age, measured 2026-08-26:

| cohort | age | OnTrac | FedEx | UPS |
|---|---:|---:|---:|---:|
| `_SHIP_2026-07-27` | 30d | 100% | 100% | 100% |
| `_SHIP_2026-08-03` | 23d | **79%** | 99% | 100% |
| `_SHIP_2026-08-10` | 16d | **58%** | 86% | 100% |
| `_SHIP_2026-08-17` | 9d | 0% | 0% | 0% |
| `_SHIP_2026-08-24` | 2d | 0% | 0% | 0% |

**OnTrac is the laggard** and is still not complete at 23 days. A whole-week `Total $` is realistic
at **~4 weeks**, and the refresh cadence follows that measurement rather than a guess.

🔴 **INVOICES ONLY — never a quoted or estimated rate, not even as a stand-in for a late invoice.**
A ShipStation quote is an estimate, not a commitment ([[quote-endpoint-measured-accurate]]);
blending measured and estimated dollars in one row produces a number nobody can falsify, which is
precisely how work gets sent in the wrong direction. A late lane shows `—`, not a modelled figure.

🔴 **Invoice ingest is LOCAL today, not DigitalOcean.** The gmail → `invoices` → `shipments` chain
runs on this machine and DO holds only `shopify_orders`. When invoice ingest moves to DO the source
of `shipments` changes and nothing else in this rule does. **Do not build toward the DO side from
here — another session owns that epic.**

#### 🔴 The air reconciliation — the tag is a PRECISE but INCOMPLETE air signal

Cross-checking tag-derived air against carrier invoices on matured cohorts:

| cohort | air by tag | air by invoice | hidden |
|---|---:|---:|---:|
| `_SHIP_2026-07-13` | 84 | 88 | **+5** |
| `_SHIP_2026-07-20` | 208 | 217 | **+9** |
| `_SHIP_2026-07-27` | 117 | 124 | **+7** |
| `_SHIP_2026-08-03` | 179 | 188 | **+9** |
| `_SHIP_2026-08-10` | 166 | 175 | **+9** |

**Every box the tag calls air was billed as air — precision is 100%.** But **5–9 boxes a week are
billed FedEx 2Day with no 2Day tag**, almost all of them `!ANY FedEx - <Hub>_AHB!` — the fence
resolved to air at the dock. The miss is one-directional: the tag basis **always undercounts air**,
which is the flattering direction for the number Kurt is watching. This is why the service priority
puts **invoice above tag**: once a lane is invoiced the air row corrects upward, and because that
happens before the count freeze on a normal week, the frozen number is the invoiced one.

#### Why it is not a `.gs` tab on the `Running Reship` project

**The `.gs` project cannot reach `shipping.db`, and every signal this table needs lives there.**
The deployed tab writers pull from Shopify GraphQL and ParcelPanel; neither carries the applied
routing tag's service token, and neither carries `shipments.service`/`shipments.cost` — the
carrier-invoice data that is the entire cost half and the only complete air signal. The cloud
MySQL holds only `shopify_orders` (the precondition gate above), so a Jdbc route does not exist
either. **Forcing this into Apps Script would mean per-order ParcelPanel calls for a carrier we
already have locally, on the exact surface another session is migrating.** A Python writer feeding
the sheet is the honest home; a `.gs` tab is not.

Consequence, stated plainly: **this does not deploy to Apps Script, no `gas_swap.py push` is
involved, and none of the five `.gs` files change.**

#### Asserts — named so a refusal is greppable, and they REFUSE rather than repair

| Name | Invariant |
|---|---|
| `CM_REPRODUCE_GATE` | the **structural** gate passes before any other week is computed — see **D35d**. 🔴 Superseded the frozen 08-24 literals, which broke every Tuesday |
| `CM_GATE_CLASSIFIER_GOLDEN` | 12 frozen `(carrier, signals) → lane` cases, zero DB (D35d) |
| `CM_GATE_ALIAS_FOLD` | `normalize_carrier("LaserShip") == "OnTrac"` (D35d) |
| `CM_GATE_LANE_PARTITION` | lanes are disjoint sets whose union is exactly the cohort (D35d) |
| `CM_GATE_AIR_SEPARATE` | tag-derived FedEx-2Day ⊆ the air lane, ∩ Ground-HD = ∅ (D35d) |
| `CM_GATE_MATURED_ANCHOR` | a **closed** cohort reproduces exactly — never the current week (D35d) |
| `CM_COHORT_GREW` | a cohort gaining boxes **prints the delta and keeps rendering** (D35d) |
| `CM_ASSERT_ROWS_SUM_TO_COHORT` | every box lands in exactly one row; rows sum to the cohort |
| `CM_ASSERT_AIR_GROUND_EXCLUSIVE` | no box satisfies both the air and the ground test |
| `CM_ASSERT_KNOWN_KEY_PASSES` | a known-present `order_number` survives the filter — **a zero is a claim** |
| `CM_ASSERT_FROZEN_COUNTS` | a frozen count column may not be restated |
| `CM_UNMAPPED` | `Other / Unmapped` is itemized by reason, never a bare number |
| `CM_PENDING_UNKNOWN` | a stale cohort replica reports `unknown`, never `0`, and blocks the freeze |
| `CM_FENCES_OPEN` | pending > 0 keeps the column provisional and says so |
| `CM_STALE_SOURCE` | `fulfillments` untouched > 3d (mirrors `freshness_sweep.py`'s own rule) |
| `CM_NO_LABELS_YET` | a cohort with no labels is not written at all |

#### 🔴 WRITER-OWNERSHIP GATE — **NOT MET. This is UNOWNED and is therefore NOT SHIPPED.**

Per the standing gate a writer is not shipped until it has (a) a scheduled owner that survives the
machine being off at fire time and (b) a freshness assert in a reader or in
`_outputs/scripts/freshness_sweep.py`. **Neither is armed.** What it needs, named:

- **(a) Owner:** a **logon-cycle / `StartWhenAvailable`** task — 🔴 never a bare fixed-time schtask
  (the machine is off at 6am; five Monday 6–8am tasks never ran). Default fire ~noon.
  Cadence follows the measured lag above: **daily** while any column in the window is provisional,
  which is what both clocks need — counts settle within days, costs over ~4 weeks.
- **(b) Freshness:** `CM_STALE_SOURCE` is implemented in-tool, but nothing outside the tool watches
  whether the tool itself ran. It needs a `freshness_sweep.py` entry on
  `_outputs/reports/carrier_mix_ledger.json` (max age ~2d). That file lives outside this repo and
  was **not** modified here.

Until both are armed this is a manually-run report. **Silence must fail loudly, and right now it
does not.**

#### Outputs

`_outputs/reports/carrier_mix_ledger.json` (the write-once state: per column `counts`,
`counts_frozen`, `residual_pending`, per-lane `cost`/`cost_frozen`, and a rolling 20-entry event
log) and `_outputs/reports/carrier-mix-pivot.md` (the rendered table). Both are re-derivable from
`shipping.db` *except* the frozen ship-time count reading, which is not — that is the one value
here with no second source, and it is the reason the ledger is written atomically and never
rewritten in place for a frozen key.

#### D35b — `--self-test`, BECAUSE A GREEN HAPPY-PATH RUN PROVES NOTHING ABOUT THE FAILURE BRANCHES (review 2026-08-26)

**The failure this closes.** `--verify-gate` and a routine `--weeks 5` run are a **happy path**. On a
normal week the cohort replica is complete, no column is empty, no assert refuses and no frozen cell
is challenged — so the entire below-threshold arm of `unresolved_orders`, every named refusal, and
the count-freeze backstop **never execute**. A green run over those weeks says nothing about the code
that only runs when something is wrong, which is the code that matters. That is the same shape as
D34: the one block a reader spot-checks was the one block structurally incapable of showing the bug.

`--self-test` exercises **21 cases**, every one a branch a normal run does not take: all eleven
`classify` outcomes (including `UPS 2Day`, `Overnight` and `Veho`, none of which exist in live data
today), the LaserShip→OnTrac fold, **all three arms** of `unresolved_orders`, all four named
refusals, the force-freeze backstop, the "unknown denominator must block the freeze" case,
rendering a cohort set that contains an unlabelled column, and the sheet repaint's foreign-tab
refusal (`_foreign_tab`, D35c — pure precisely so its refusal arm is exercisable without a network).

🔴 **THE BRANCH SWEEP FOUND A LIVE CRASH — `KeyError: 'counts'` IN `_cell_count`.** A week with no
labels yet is deliberately never written to the ledger (`total == 0` → `reconcile_ledger` is
skipped) but it **is** still rendered as a column, so `render` handed `_cell_count` the `{}` from
`ledger["columns"].get(tag, {})` and the subscript `e["counts"]` blew up. `_cell_cost`, three lines
below, already used `.get`; this one had lost it.

**It is an unattended-run killer, not a cosmetic bug.** `ship_mondays` always includes the CURRENT
week's Monday, so every run between Monday 00:00 and the moment RMFG cuts that week's labels hits
it — precisely the window a scheduled owner runs in. It never fired in any hand-run because every
one of those happened mid-week with the column already populated. Reproduced 2026-08-26 by
rendering `_SHIP_2026-08-31` (0 labels) alongside the live weeks; fixed to `e.get("counts")`, which
renders `—`, the same blank-≠-zero reading an un-invoiced cost cell gets, never a fabricated `0`.
The regression case asserts `"$0" not in table`, and it is **falsifiable** — restoring the pre-fix
body at runtime flips it to `[FAIL] KeyError: 'counts'`, 19/20.

🔴 **The lesson, stated in the form that would have caught it:** the branch-coverage argument was
made about `unresolved_orders` and the asserts, and it stopped there. `render` consumes the SAME
"column that has no ledger entry" state, and nothing walked the consumers of that state. When a
branch is identified as untested, sweep every consumer of the value that branch produces — not
just the function that produces it.

🔴 **The fixture is PRODUCTION-SHAPED and asserts that it is.** The self-test's `base` column is
checked against `build_column`'s real key set on every run
(`assert not (set(_live) | {"_keys"}) - set(base)`). It earned that on its first execution: the
hand-built fixture was missing `coverage`/`spend`/`invoiced` and three cases failed with `KeyError`
— a guard that would have KeyError'd on the real object while looking green against an injected
shape. That is the exact fail-open class this repo has shipped repeatedly; a fixture that carries
only the keys the test happens to touch is not evidence.

**Review findings, resolved:**

1. **`REPLICA_MIN_COMPLETENESS` / `_expected_cohort_size` reported as NameErrors — NOT PRESENT.**
   Verified independently against the committed blob (`git show c4a391a:…`), not the working tree:
   `REPLICA_MIN_COMPLETENESS` is defined at line 74 and used at 226; `_expected_cohort_size` does
   not occur anywhere in the file (it was replaced by `unresolved_orders`). ruff `F821` reports zero
   undefined names and pyright reports zero errors/zero warnings under this repo's own config.
   🔴 **The lesson is the one worth keeping: a static read of a file MID-EDIT is not a review of the
   commit, and the line numbers are the tell.** The same thing happened on the second review pass —
   a reported "Expected 2 positional arguments" is exactly the transient state where
   `verify_gate`'s signature had already been narrowed to `(con, dss)` but its call site in `main`
   still passed `(con, inv, dss)`; both ends are consistent on disk and in the commit. The reported
   `str | dict[...]` union is likewise an inference artifact of the self-test's heterogeneous
   `base` fixture, now annotated `dict[str, Any]` at the source rather than cast away at a call
   site — a cast there would hide a real mismatch later. **Check the blob, and re-run the checker
   yourself before acting on someone else's diagnostic.**

   🔴 **It happened a THIRD time, and the line numbers are always the tell.** A reviewer reported
   `"Any" is not defined` at lines **621 and 637** of the pushed commit. `from typing import Any`
   is at line 41 of `94baf44` and the four `Any` uses are at 579/608/624/633/649 — 621 and 637 are
   not among them. Reconstructing the window between the edit that first *used* `Any` (the render
   self-test case) and the edit that *imported* it — i.e. the file minus the import (1 line), the
   `base:` rationale comment (6) and the docstring warning (5), 12 lines total — puts the two
   `Any` uses that existed in that window at **exactly 621 and 637**, and `ruff --select F821`
   on the reconstruction returns exactly those two. The linter read a ~90-second mid-edit state.
   **The procedure: if a reported line number does not contain the reported construct in the
   commit, you are looking at a stale buffer — diff the tree against the blob
   (`git diff <sha> -- <path>`, empty = identical) before changing anything.**
   For the record it was never a runtime `NameError` either: all four uses are LOCAL variable
   annotations, which Python never evaluates, and the module carries
   `from __future__ import annotations` besides. Verified by execution, not by reasoning.
2. **`inv` was genuinely unused in `verify_gate` — REMOVED.** Not a dropped assignment feeding a cost
   cell: the gate passes `{}` on purpose so it reads the TAG basis. 🔴 But an unused invoice index
   sitting in that signature was a live trap — a future reader "fixing" it would wire live invoices
   into the gate, letting the 5–9 dock-upgraded air boxes/week move the air row so the gate fails
   against a reference that is still correct (or passes for the wrong reason once two errors
   cancel). The parameter is gone, so there is nothing to wire in by accident, and the docstring now
   says so in the imperative. The review also reported "three unused `inv` bindings" in the
   **rendering** block (~411–443): there is no `inv` binding in that range at all — the only match
   is the literal `inv` inside `_cell_cost`'s partial-coverage f-string (`"…% inv · $…/bx"`). But
   the instinct that an unused-looking name in invoice-coverage code deserves a second look was
   right: that block is where the `_cell_count` `KeyError` above was hiding, three lines from the
   flagged line. ruff's `F841` covers genuinely unused locals and is clean.
3. **`from lib import canon` resolves at RUNTIME and is cwd-independent.** Both `sys.path` inserts
   derive from `Path(__file__).resolve()`, never from the cwd. Verified by running `--verify-gate`
   and `--self-test` from `C:\` and `C:\Windows` — identical output, exit 0. This matters because
   the scheduled owner runs unattended with whatever cwd the task scheduler hands it. `ShipRouting`
   is inserted LAST so it wins position 0 for the very generic name `lib`; AppyHour has no competing
   `lib` package. pyright's `reportMissingImports` on that line is a static-analysis false positive
   (a checker cannot see a `sys.path` mutation) and is suppressed narrowly, with the reason stated.

**Both arms of the Pending/denominator branch, measured 2026-08-26 (this is what "proved" means
here — not a `--verify-gate` PASS, which resolves before the branch is ever reached):**

| tag | labels | replica | complete | arm | pending |
|---|---:|---:|---:|---|---:|
| `_SHIP_2026-07-20` | 2082 | 2075 | 99.7% | **above** | 0 |
| `_SHIP_2026-08-17` | 2366 | 2369 | 100.1% | above | 13 |
| `_SHIP_2026-08-24` | 2500 | 1005 | **40.2%** | **below** | unknown |
| `_SHIP_2026-08-31` | 0 | 0 | 0.0% | **empty** | unknown |

Above-threshold returns a real set difference (`CM_FENCES_OPEN` when non-zero). Below-threshold
returns `None` and prints `CM_PENDING_UNKNOWN: … replica is 40% complete … pending reported as
unknown, never 0, and this column CANNOT freeze`. Empty prints `CM_NO_LABELS_YET` and the column is
not written. Note `_SHIP_2026-08-17` at **100.1%** — the replica can legitimately exceed the label
count, which is why this is a completeness RATIO against labels and never a subtraction.

**Gate for this file: `ruff` clean, `pyright` 0 errors / 0 warnings, `--self-test` 21/21,
`--verify-gate` PASS (OnTrac 1763 / FedEx Ground-HD 648 / FedEx 2Day 69 / UPS 20 / Total 2500,
exact) — run all four before committing a change here, and run them from a cwd OUTSIDE the repo so
the import path is exercised the way the scheduled owner will exercise it.**

#### D35c — `--write-sheet`: THE TAB IS A VIEW, THE LEDGER IS THE MEMORY (Kurt-authorized 2026-08-26)

Kurt, looking at the terminal pivot: *"push it to a new tab."* Authorized as a **new tab named
`Carrier Mix`** on the Running Reship sheet (`1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU`),
painted by `carrier_mix_pivot.py --write-sheet` via the `shipping-perfomance-review` service
account (editor on the sheet; key gitignored at `AppyHour/shipping-perfomance-review-*.json`).
This does not change the "not a `.gs` tab" decision above — the Python writer IS the honest home
that section named; the bound Apps Script project still never touches this tab.

1. 🔴 **ONE COMPUTE PATH, TWO RENDERERS.** `grid()` is the single place ledger state becomes cell
   text; the markdown `render()` and the sheet repaint both consume it verbatim and add only
   presentation. A second computation path for the sheet is how the terminal and the tab drift
   into showing two different tables while both look right.
2. 🔴 **WRITE-ONCE SEMANTICS LIVE IN THE LEDGER, AND ONLY THERE.** The tab is cleared and
   repainted WHOLE every run — that is correct *because* the ledger is the memory and already
   refuses to restate a frozen cell (`CM_ASSERT_FROZEN_COUNTS`). **Do not "fix" the repaint into
   per-cell write-once on the sheet** — that duplicates the ledger's job in a second store and
   the two will disagree. Corollary: `--write-sheet` refuses to combine with `--no-ledger`
   (`CM_SHEET_NEEDS_LEDGER`) — the view must never get ahead of the memory.
3. 🔴 **BLANK/`—` ≠ $0, IN THE SHEET TOO.** Every value goes up `valueInputOption=RAW`, so every
   cell lands as literal text and Sheets coerces nothing — a `—` stays a text `—`, never a
   numeric 0 in an un-invoiced cost cell (failure #7 above). No cell in this tab is meant to be
   summed by a formula; the ledger is the queryable store.
4. 🔴 **A HALF-PAINTED TAB MUST BE LOUD.** Write order: clear → main batch (A1 marker title, the
   grid, the D35 note row, the run notes) → **read back the header row** → stamp row
   (`Last refreshed: … ET — paint complete`) written **LAST** in its own call. A missing stamp
   row = the paint died partway; the note row says exactly that and says to rerun. The stamp is
   ET per the standing report-times rule.
5. 🔴 **NEVER OVERWRITE A TAB THIS TOOL DID NOT PAINT.** Repaint is allowed only when the tab is
   absent (create it), completely empty, or carries the A1 marker
   (`Carrier Mix — ship weeks as columns (D35)`). Anything else → `CM_SHEET_FOREIGN_TAB`, a
   refusal, never a clear. The gate is the pure `_foreign_tab()` so `--self-test` exercises the
   refusal arm offline.
6. **Run notes travel with the table.** `Other / Unmapped` is never a bare number (failure #6 /
   `CM_UNMAPPED`), so the itemizing run notes are painted below the note row — the sheet reader
   gets the same itemization the terminal reader gets.
7. **Scope of the write:** a metadata `spreadsheets.get` first (identity echo + tab roster), then
   values calls against `'Carrier Mix'` ranges ONLY. No other tab is ever named in a write. The
   DB side stays `connect_ro`, absolutely.
8. ⚠️ **Still UNOWNED** — `--write-sheet` is idempotent precisely so a future scheduled owner can
   call it as-is, but the writer-ownership gate above remains NOT MET and no scheduled task was
   created (Kurt has not picked the owner). Until then this is a manually-run repaint.

#### D35d — A REFERENCE FROZEN ON MONDAY-ONLY COUNTS BREAKS WHEN THE TUESDAY DALLAS LEG LANDS — THIS REFUSED TO RENDER FOR TWO DAYS (Kurt 2026-08-27)

> Kurt: *"that's because we shipped tuesday as usual."* · *"it always grows tuesday night."*

**The failure.** The reproduce-gate above pinned five literals to `_SHIP_2026-08-24` as measured on
2026-08-25 — OnTrac 1763 / FedEx Ground-HD 648 / FedEx 2Day 69 / UPS 20 / **Total 2500** — and
`main()` raised `CM_REPRODUCE_GATE failed — refusing to extend to other weeks` when they moved.
They moved. From 2026-08-25 the tool **could not render the multi-week table at all**, for two
days, and the numbers it was refusing over were **correct**: OnTrac 1770 / 680 / 70 / 25 / 2545.

**Nothing was wrong. The cohort had done what it does every week.** `fulfilled_at` on
`_SHIP_2026-08-24` splits **2,500 rows dated 08-24 and 45 rows dated 08-25** — the **Tuesday Dallas
leg**. Filtered to the Monday leg alone the cohort reproduces the old reference *exactly*
(1763 / 648 / 69 / 20 / 2500), which is the proof that the reference was a **Monday-only snapshot**,
not a wrong measurement. The 45 Tuesday boxes are OnTrac 7 · FedEx Ground-HD 32 · FedEx 2Day 1 ·
UPS 5.

🔴 **MULTI-LEG UNION DOCTRINE — THE PART THAT MUST NOT BE RE-LEARNED.** A ship week is **not one
event**. The Monday leg lands first; the **Tuesday Dallas leg lands Tuesday night**, every week,
as standard operation ([[multi-leg-shipweek-union-doctrine]]; Tuesday = Dallas-only hub). **A
cohort is only final after Tuesday night, so ANY measurement taken before it is provisional by
construction.** Restating the literals to the post-Tuesday values would have fixed that Wednesday
and re-broken the following Tuesday — resetting a clock, not removing it.

🔴 **The general caution, beyond this file:** anything in the reporting surface that **snapshots a
cohort on Monday** and later compares against it carries this same latent bug. If you freeze a
cohort number, record **which legs were in** when you froze it, or freeze only after Tuesday night.

#### 🔴 The rule: A GATE THAT CATCHES A *LOGIC* REGRESSION MUST NOT BE KEYED TO A *VOLUME*

The volume legitimately changes every week. The logic must not. Pinning the gate to counts
conflated the two, so a routine business event fired an alarm built for a code defect — and the
only available response was to silence the alarm. **The gate now asserts structure and a closed
anchor; not one check moves when the cohort grows.** Cohort growth is **reported**
(`CM_COHORT_GREW`), never fatal.

| Check | Invariant | Why growth cannot move it |
|---|---|---|
| `CM_GATE_CLASSIFIER_GOLDEN` | 12 frozen `(carrier, signals) → lane` cases | **zero DB rows read** |
| `CM_GATE_ALIAS_FOLD` | `normalize_carrier("LaserShip") == "OnTrac"` (D35 failure #2) | pure function |
| `CM_GATE_LANE_PARTITION` | lanes are **pairwise disjoint sets whose union is exactly the cohort's own `order_number` keys** (D35 failure #6) | a ratio of the week to itself |
| `CM_GATE_AIR_SEPARATE` | the FedEx-2Day set read **straight off the routing tag** ⊆ the `FedEx 2Day Air` lane, and ∩ `FedEx Ground-HD` = ∅ (D35 failure #1) | scales with the week |
| `CM_GATE_MATURED_ANCHOR` | `_SHIP_2026-07-27` reproduces exactly, tag basis | **closed cohort — all legs landed 2026-07-28; it cannot grow** |
| `CM_COHORT_GREW` | a column that gained boxes since the last run **prints the delta and keeps rendering** | this *is* the growth path |

- 🔴 **`tag_air` is derived WITHOUT the classifier, and that is the whole point.** `lane_sets`
  returns both the classifier's buckets and an independently tag-derived FedEx-2Day set. **A check
  that asks the classifier whether the classifier is right cannot fail.** If a change folds 2Day
  into Ground-HD, `tag_air` still names those boxes and the gate finds them in the ground lane.
- 🔴 **SUBSET, not equality** — `tag_air ⊆ FedEx 2Day Air`. The direction is measured, not chosen:
  the air reconciliation above found the tag is a **100%-precise but incomplete** air signal (5–9
  boxes/week are billed 2Day with no 2Day tag). Equality would fail every time an invoice landed.
- 🔴 **The anchor is the TAG basis, and the ledger's frozen entry for the same cohort is NOT.**
  The frozen ledger reads `FedEx 2Day Air: 124` for `_SHIP_2026-07-27` because it was computed
  *with* invoices; the tag basis reads **117**. The 7-box gap is this document's own air
  reconciliation, not drift. Verified 2026-08-27 that the tag basis reproduces the published
  air-reconciliation table on all three matured cohorts — 07-27 → **117**, 08-03 → **179**,
  08-10 → **166**, exact. **Do not "fix" the gap by wiring `inv` in** (D35b review finding 2).
- **Why `_SHIP_2026-07-27` is the anchor:** it is 31 days old *and* **all five lanes are non-zero**
  on it — including `Other / Unmapped` = 372 Veho boxes. A cohort with an empty lane would let a
  regression that drops that lane pass unnoticed.
- 🔴 **NEVER RE-PIN THE ANCHOR TO THE CURRENT WEEK.** That is the bug this replaced.

#### 🔴 Proven to still refuse — a gate that only ever passes is worse than none

The point of moving off literals is surviving the weekly leg; that is only an improvement if the
gate still goes red on a real defect. `--self-test` **seeds the faults and asserts the refusal**,
so this is permanent evidence rather than a one-off demo (measured 2026-08-27):

| seeded fault | result |
|---|---|
| **FedEx 2Day merged into Ground-HD** (D35 failure #1) | **REFUSES** — red on `CM_GATE_CLASSIFIER_GOLDEN` + `CM_GATE_AIR_SEPARATE` |
| **fenced UPS boxes silently vanish** (D35 failure #6) | **REFUSES** — red on `CM_GATE_LANE_PARTITION` **alone**; **31** boxes lost from the anchor |
| **matured anchor perturbed by one box** | **REFUSES** — red on `CM_GATE_MATURED_ANCHOR` |
| **cohort grows 500 → 545** | **RENDERS**, logs `GREW … (+45) — a later leg landed` |

🔴 **The vanishing-boxes fault is seeded SURGICALLY — it drops only UPS boxes carrying no service
signal, the shape a fence leaves behind.** Every UPS case in the truth table carries a signal, so
`CM_GATE_CLASSIFIER_GOLDEN` **stays green** and only the live structural check sees it. That
asymmetry is the lesson: **a frozen truth table cannot cover states it does not enumerate**, which
is exactly why the gate needs both halves and why neither alone would be sufficient.

#### `--self-test` is 30/30 — and the arm that was unreachable is fixed here

**It was 20/21 on `HEAD`, and the failing case was the same defect one level down.**
`unresolved_orders: all 3 arms reached` asserted that **live data** reached the below-threshold
arm. It did on 2026-08-26 (the `shopify_orders` replica sat at 40% for `_SHIP_2026-08-24`); the
replica then **caught up**, and a healthy replica read as a **test failure**. 🔴 **A test keyed to a
transient DATA state is not a test of the code it claims to cover.** The four arms
(above / below / empty / replica-absent) are now proven against an **in-memory `sqlite3` fixture**
— deterministic, always reached — and the live sweep only **reports** which arms today's data
happens to hit. (The fixture DB is ephemeral and is **not** `shipping.db`; the `connect_ro`
read-only doctrine is untouched and no writer is ever opened against the live store.)

**Gate for this file (supersedes D35b's):** `ruff` clean, `pyright` 0 errors / 0 warnings,
`--self-test` **30/30**, `--verify-gate` PASS — run all four **from a cwd outside the repo** so the
import path is exercised the way a scheduled owner will exercise it.

**Not touched by this change:** `shipments.hub` (which stopped populating — wk0810 is 1,521/1,719
NULL, wk0817 all 1,449 NULL) is **never read** by this module, nor is `shipments.ship_date`; the
hub truncation and the two-format `ship_date`/duplicate-tracking issues cannot surface here as a
logic failure. `delivery_status.service` remains NULL on all rows and the tag remains the sole
service basis — the gate does not reach for that column to "fix" the service split.

### D36 — `Routing Match`: A FENCE IS NOT A PREDICTION — FENCED BOXES LEAVE BOTH SIDES OF THE RATE (Kurt 2026-08-26)

> Kurt, on the Routing Match tab: *"for routing match, if its a bunch of fences, then they should
> be excluded."*

**The failure this corrects.** `paRoutingValues_` scored every order with a single assignment-shaped
tag, and `paCarrier_('ANY')` returns the raw string `'ANY'` — which never equals any executed
carrier. So every **bare `!ANY - <Hub>_AHB!`** box (the all-carrier fence, D35 failure #5) was
counted as a PERMANENT MISMATCH. On `_SHIP_2026-08-24` that was **739 boxes** — the engine
deliberately declined to pick a carrier, RMFG picked at the dock (all 739 went **OnTrac**), and the
tab read **69.40%** as though the engine had been wrong 739 times. Scoring a fence is a category
error: the engine explicitly declined to predict, so the dock's pick can neither confirm nor refute
it. Fenced boxes leave **BOTH numerator and denominator**; the rate now reads *"of the boxes where
the engine COMMITTED to a specific carrier, how often did reality match."*

**Fence taxonomy — per MATCH DIMENSION, decided per row, whole-token only (never substring; four
partial-label bugs in this project, D35 failure #3):**

| tag class | Carrier match | Hub match (when it matures) |
|---|---|---|
| `!<Carrier> <Service> - <Hub>_AHB!` (explicit assignment) | **scored** | **scored** |
| `!ANY <Carrier> - <Hub>_AHB!` (carrier pin, e.g. `!ANY FedEx`) | **scored** — it COMMITS the carrier; only Ground-vs-HD/service is delegated (`FENCE_FEDEX_HD`, ROUTING_RULES §10) | **scored** — hub is committed |
| bare `!ANY - <Hub>_AHB!` (all-carrier fence) | **fenced — excluded** (carrier delegated to RMFG) | **scored** — the hub IS committed even though the carrier is not |
| fence-only `!NO …` stack (no assignment) | **fenced — excluded** (was already excluded per D11 as uncomparable; D36 makes it visible on the n row) | per D17: exactly-1-open-hub → that hub is effectively committed; ≥2 open → uncomparable |
| no `_AHB!` tag at all | **fenced — excluded** (already excluded per D11) | uncomparable |
| assignment token resolving to no canonical carrier | **`MISSING — needs Kurt`** — logged loudly (`unrecognized assignment token`), excluded, NEVER guessed into a bucket (never-fabricate). Measured count on wk0824: **0**. | same |

- 🔴 **The carrier test is `paCarrier_` (the file's canonical token fold) + a whole-set membership
  check (`PA_ROUTING_CANON`), and the bare-ANY test is `/^ANY$/i` on the trimmed pre-hub token** —
  never `indexOf('ANY')`, which would also swallow `!ANY FedEx` (a real commitment) and any future
  token containing the letters. This mirrors `carrier_mix_pivot.py`'s D35 token treatment; the two
  stay in agreement on what a fence is.
- 🔴 **The Hub row's semantics are documented here but NOT implemented** — `Routing Matched - Hub`
  stays `n/a (immature)` (D11: actual hub needs carrier invoices, ~1wk lag). When it is built, the
  hub column of the table above governs.

**The denominator is ON the sheet (row 4, `Carrier n (committed / fenced)`).** A percentage whose
denominator silently shrank is the misread this sheet keeps generating, and Kurt directed the
exclusion — the count row is what keeps the number falsifiable. Cell text `"<scored> / <fenced>"`
(e.g. `1679 / 824`), written by the same first-measurement run as the rate and FROZEN with it
(`paRoutingIsMeasured_` recognizes the `N / M` shape). Historical columns stay **blank** on this
row — a ship-time fence count for a matured cohort is not reconstructible (D23: tags mutate), and
blank ≠ zero.

**Effect on `_SHIP_2026-08-24` (measured 2026-08-26, cohort age 2d, 2,503 orders excl
cancelled/Reship; old basis reproduced the frozen 69.40% cell EXACTLY before the new basis was
trusted — reproduce-gate):**

| | old basis | new basis (D36) |
|---|---:|---:|
| scored (committed & observed) | 2,418 | **1,679** |
| matched | 1,679 | 1,679 |
| **rate** | **69.4%** | **100.0%** |

Itemized exclusion (sums to the 824 fenced): **739** bare `!ANY - <Hub>` (all → OnTrac at the
dock), **43** fence-only `!NO` stacks, **42** with no `_AHB` tag. The 1,679 scored = 1,080 explicit
assignments + 599 `!ANY FedEx`-class carrier pins; zero committed-but-unobserved, zero unrecognized
tokens. That 100.0% is consistent with the 08-10/08-17 frozen readings (100.00% both) — wk0824's
69.40% was never a routing regression, it was 739 fences scored as misses.

**Cutover / mixed basis (same pattern as D32's window change).** The new basis applies from
`_SHIP_2026-08-24` FORWARD. The wk0824 Carrier cell is the ONE deliberate D23 exception: Kurt
directed the re-measure, the column is still inside its D15 script-owned window (age 2d), and the
old reading was reproduced and recorded here first — the overwrite loses nothing that is not in
this table. Frozen columns **≤ `_SHIP_2026-08-17` keep their old-basis readings untouched**
(89.3% · 91.6% · 90.4% · 98.00% · 100.00% · 100.00%); their n-row cells stay blank. Do not
"restate" them — D23 stands.

### D37 — A BUCKET THAT DRAINS TO ZERO BOXES LEAVES THE UNIVERSE, AND THE REPAIR REFUSES ITSELF (2026-08-26)

> 2026-08-26 08:31 CST, the first armed daily run carrying the D34 fix: the wk0824 current leg
> passed both tabs, then the `_SHIP_2026-08-17` reconcile leg wrote TnT2 and
> `PA_ASSERT_SECTION_SUM` refused it — `hub · 2 Day` block **2,266** against a headline of
> **2,265**, the entire excess the stale `RMFG choice (2+ hubs open) · 2 Day = 1`. The throw
> aborted the tab loop, so `Lost in Transit` (stale `RMFG choice · Not Arrived = 79`,
> `· Arrived = 1`, `ME · Not Arrived = 19`, `NC = 42`, …) was never reached at all. One day before
> the age-10 freeze, the run that existed to repair the column refused itself — and would have
> refused forever.

**The failure, negatives first.**

1. 🔴 **D34's zero-fill universe is "every bucket with ≥ 1 box in the cohort THIS run" — and a
   bucket whose membership is MUTABLE can drain to exactly zero.** Hub attribution is computed from
   LIVE tags on every run (D17): once corrective retagging resolved the last fence-stack order of
   wk0817, `RMFG choice (2+ hubs open)` held zero boxes in every grain, emitted no key, and its
   high-water cells became unreachable by the very writer that was supposed to repair them. D34
   §"The already-written cells" claimed col G "rewrites every dimension cell" on the first armed
   run — incomplete: every dimension cell **whose bucket still has boxes**. The D34 doc's own
   wording ("a residual bucket that DRAINS to ~0") contained the gap: `~0` is repairable, `0` is
   not.
2. 🔴 **The assert did its job — this was never a reason to weaken it.** Post-write is still the
   correct place (D34 rule 4): the sheet genuinely did not partition. The fix is to make the write
   complete, not to make the check forgiving.
3. 🔴 **The current leg was fine, the previous leg carried the defect** — because only an OLD
   column has had time for its residual bucket to drain. wk0824 (age 2) passed cleanly on both
   tabs; the D20 previous-leg catch swallowed the throw (by design, non-fatal), DM'd Kurt, and the
   current column was unaffected.
4. 🔴 **NOT the D36 push.** Live `9c682e71dcb0` vs the D34-verified `e00f7770f15c` diffs ONLY in
   `paRoutingValues_` / `paAssigned_` / `paRoutingIsMeasured_` / two D36 constants — nothing on the
   delivery-tab write path. Confirmed by byte diff, not assumed.
5. 🔴 **NOT a data problem.** The freshly computed buckets partition their headlines exactly (the
   five live hubs summed 2,265 = `2 Day Shipments` to the box). Every failing delta was a stale
   cell of a drained bucket. Nothing "genuinely doesn't reconcile."

**🔴 The rule: RECLAIM.** `paReclaimDrained_` runs between `paValues_` and `paWriteOwned_` on both
delivery tabs, in armed and dry runs alike. For every DIMENSION row of the column that (a) currently
holds a **NUMBER**, (b) parses as `{bucket} · {grain}` (or `· TNT1`) on the whole token after the
LAST ` · `, and (c) has **no key** in this run's value map — the bucket shipped once (a number is a
past claim of membership) and holds zero boxes now, so the honest value is **0**, added to the value
map so the ordinary writer, the dry-run simulation, and the missing-row report all see it. What it
must NEVER do:

- **touch a blank cell** — blank still means "did not exist in this cohort" (A5/D19/D34 rule 2);
  Indianapolis and never-ordered states stay blank;
- **touch a non-numeric cell** — the stray hand-typed `'o'` in `TnT2!Indianapolis · 2 Day` (wk0817)
  survives; a hand edit is Kurt's to clear, and the assert already ignores text;
- **touch the headline block, a rate row (blank label), or anything outside the four dimension
  blocks**, or run outside a script-owned column (`paCurrentCol_`'s age gate is upstream).

A bucket still in the universe is untouched by construction — the zero-fill already gave every
universe bucket a key in every grain, so (c) can only hold for drained buckets.

**Verification (2026-08-26):** local re-computation of the assert over the live grid reproduced the
08:31 refusal exactly (TnT2 wk0817 `hub · 2 Day` 2,266 vs 2,265; Lost wk0817 hub 88/carrier 10/
state 186 vs 7 — unchanged from D34's table because the leg aborted before Lost); vm replay of the
deployed bytes (`d37_test.js`): without reclaim the seeded wk0817 shape throws, with reclaim both
tabs pass, blanks/text/headline/in-universe cells untouched; collision sweep 424 top-level names no
duplicate; `node --check` clean per file and over the 4-file concatenation.

**Freeze-deadline math (why this deployed same-day):** `paCohortAgeDays_` is an ET calendar-day
diff from the ship Monday. wk0817 is age **9** on 2026-08-26 (script-owned, repairable) and age
**10** on 2026-08-27 — both `paCurrentCol_` and the reconcile-leg gate refuse it from tomorrow.
The daily trigger for 2026-08-26 has already fired (08:31 CST, the refusal above), so the repair
requires a **manual `refreshCurrentColumn` run after the deploy, before end of day ET** — after
that, wk0817's dimension blocks are frozen wrong permanently, like wk0810's. No freeze constant is
touched, no per-cohort exception is added: the freeze discipline stands.

### D38 — A TAB SWAP IS ONE MUTATION: NEVER CLEAR-THEN-SET, NEVER PAINT COUPLED CELLS ONE AT A TIME (Kurt 2026-08-26)

> Kurt, reading the Reship tab mid-refresh: **`Potential 1 / Actual 0`** — a pair no run ever
> computed. `Potential = D+I+N (Raw Data reships) + F+K+P (Triage unresolved)`, `Actual = D+I+N`;
> the only state that renders 1/0 with a real reship on the ledger is *Raw Data momentarily empty
> while Triage still stands* — exactly the window `refreshPivotSheet_` opened every hour.

**The failure, negatives first.**

1. 🔴 **`clearContents()` followed by `setValues()` is TWO document mutations, and every reader
   between them sees a tab that no run wrote.** Sheets applies each Range call to the live document
   immediately — there is no transaction. During the gap, every cross-tab `COUNTIFS` over the
   cleared tab collapses to 0 while formulas over other tabs keep their values, so the torn state is
   not "stale", it is *fabricated*: a mixture of two runs that partition-checks on the READER side
   can never reconcile. The Reship transpose (`writeProductMixT_`) snapshots Product Mix
   `getDisplayValues` — a snapshot taken while any substrate tab is mid-swap freezes the torn pair
   as literals for a full hour.
2. 🔴 **The writers this bit:** `refreshPivotSheet_` (Raw Data — the substrate of every reship
   COUNTIFS), `writeTabTo_` (Product Mix, Reship, Triage, `_triage_decisions`, Daily), `saveState_`
   (`_state` — no formula reads it, but a crash or the 6-minute kill between clear and set wiped
   the state outright; the long-standing comment on the `_state` wipe risk described this exact
   hole). `excSaveState_` (Exceptions.gs) had already solved it — "write FIRST, then trim" — and
   the fix generalizes its doctrine.
3. **The rule.** Build the full padded grid, extend it to cover **both** the new rows and the old
   extent (`max` of rows/cols), and land it with **ONE `setValues`** — the covering write IS the
   clear. A reader sees the old tab or the new tab, never neither, never a mixture. Formats/widths
   (the D13 format-carry) stay BEFORE the write; asserts that read the sheet back stay AFTER it —
   the format→header+values→assert ordering is unchanged, the header+values step just stopped being
   two steps.
4. **Coupled cells outside full-tab writers batch by contiguous run.** `holdRefresh_` painted the
   day's snapshot column one `setValue` per cell — a column that is partition-gated
   (`holdGates_`) before writing tore for any reader mid-paint. Planned cells now group per column
   and each contiguous run lands as one `setValues`. The write-once rule is untouched: only
   planned (blank) cells are covered, a gap between planned cells splits the run, and the
   post-flush read-back assert is unchanged.
5. 🔴 **What NOT to do:** do not "fix" this by reordering tab writes (any order still leaves a
   torn window inside each tab); do not suppress the reader-side partition asserts (they were
   correct — the sheet genuinely did not partition); do not re-introduce `clearContents()` on any
   tab a formula or a human reads — grep for it before adding a writer. The dead legacy writers
   (`writeTab_`/`writeRawData_`/`buildTabs_`, no callers) keep the old pattern and must be treated
   as retired, not as templates.

**Verification (2026-08-26):** `node --check` clean per file and over the 4-file concatenation;
collision sweep no duplicate top-level names; write-shape replay under node (writeTabTo_ covering
grid vs old extents: new-shorter-than-old blanks the tail, new-wider extends, 1-row write does not
arm the headerless assert; holdRefresh_ run-batching emits identical cells to the per-cell loop on
seeded plans, including gap splits).

> D33 companion: hold tag taxonomy, _FLOWHOLD reason classes, _UNRESOLVED lifecycle + cancellation terms = [HOLD_BUSINESS_RULES.md](HOLD_BUSINESS_RULES.md) (mirrors Kurt's Google Doc, the authority).

---

### D39 — `Vendor Matrix`: THE WEEKLY MATRIX EXISTED ONLY IN SLACK SCROLLBACK, AND THAT IS NOT A RECORD (2026-08-31)

**The observation.** `weekly-shipping-vendor-matrix` (routine SKILL at
`~/.claude/scheduled-tasks/weekly-shipping-vendor-matrix/SKILL.md`) DM'd Kurt a carrier×issue matrix
every week and persisted **nothing but the Slack fixture it parsed**. There was no queryable
history, nothing to trend week over week, and the whole record disappears the day Slack retention
bites. Its three sibling weekly routines each already leave an artifact — this one did not.

**The rule.** `sync.py --report --history-sheet` records the week in
`_outputs/reports/vendor_matrix_ledger.json` and repaints the **`Vendor Matrix`** tab on the
canonical pivot sheet `1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU`. Implementation:
`AppyHour/ingest/slack_reship/matrix_history.py`.

1. 🔴 **The DM is NOT exception-only and must never become it.** This was proposed and rejected the
   same day. Exception-only is for **monitors** — silent when healthy. This is a **report Kurt
   reads**: the number is the deliverable, not an alert about a number, and "only tell me when
   something's wrong" is meaningless for a metric tracked week over week. The tab is for HISTORY; it
   does not license silencing the DM. Both ship, every week.
2. 🔴 **One aggregation, two renderers.** `counts_by_vendor()` in `sync.py` is the only tally; the
   markdown DM matrix, the sheet grid and the ledger all consume it. A second consumer that
   re-tallies `rows` its own way is how the DM and the tab come to report two different numbers for
   the same week — already paid for once on `Carrier Mix` (D35c).
3. 🔴 **Never a rate over a zero denominator.** `denom <= 0` means the cohort tag is wrong.
   `upsert()` raises `VM_ZERO_DENOM` and **neither** the ledger **nor** the sheet is touched; the
   routine reports the bad tag in the DM and stops. A bogus 0-denom percentage is worse than no
   number.
4. **Weeks as COLUMNS, one tab — deliberately the same shape as `Carrier Mix` beside it.** A
   tab-per-week would add ~52 tabs/yr to a spreadsheet already carrying 23, and would force
   cross-tab reading to answer "is FedEx delayed getting worse". The **ledger is the MEMORY**, the
   tab is a **VIEW** repainted whole each run — do not add per-cell write-once to the tab, that
   duplicates the ledger's job in a second store and the two will disagree.
5. **A prior week CAN legitimately move** — tickets get posted to #reship late, and a ship week is
   multi-leg so its denominator grows when the Tuesday Dallas leg lands. So this is an upsert, not a
   freeze (contrast D23/D33 write-once). What is forbidden is a **silent** restatement: any change
   to a week on record emits `VM_RESTATED` into the tab's notes and appends to that week's log.
6. 🔴 **Additive only.** This tool owns exactly one tab and refuses (`VM_SHEET_FOREIGN_TAB`) to
   overwrite a tab whose A1 is not its own marker. It never reads, writes or reorders any other tab
   — `_exc_state` in particular is the Exceptions sweep's durable state and clobbering it loses
   every open box.
7. **`0` is a measured zero; `—` means not tracked that week.** Blank ≠ zero, same reading as D35's
   un-invoiced cost cells. All values go up `valueInputOption=RAW` so Sheets coerces nothing and
   `0.55%` stays literal text.
8. **The stamp asserts a VERIFIED paint.** The header row is read back before the
   `Last refreshed: … ET` row is written, as a separate final write. A missing stamp row means the
   paint died partway — rerun. All human-read timestamps are Eastern.
9. **The beat is unchanged and is NOT keyed on `--history-sheet`.** `sync.py` beats `vendor-matrix`
   when `--report` and not `--push` — `weekly-reship-report` runs the same `main()` with
   `--report --push` and owns the separate `slack-reship` key. Two routines, two keys.

**Verification (2026-08-31).** `matrix_history --self-test` (10 assertions: grid shape, absent
vendors/untracked issues excluded, `% denom` rows, restatement logged, both zero-denom arms refuse
AND leave the ledger untouched, foreign-tab guard). Live: 9 weeks (2026-06-22 … 2026-08-17)
backfilled from the archived fixtures; tab read back and reconciled cell-for-cell against the
2026-08-17 DM matrix (FedEx 13/0.55%, OnTrac 8/0.34%, UPS 2/0.08%, unjoined 1/0.04%, total
24/1.01%, and every per-issue `% denom` cell). Denominators cross-check exactly against the
`Carrier Mix` totals for the four overlapping weeks (2225 / 2362 / 2365 / 2366). Beat `vendor-matrix`
observed moving to 2026-08-31 4:05 PM ET on the `--report --history-sheet` path. Spreadsheet tab
count 23 → 24: one tab added, none touched.

**Provenance caveat, stated in the tab itself:** the nine backfilled weeks were RE-DERIVED from the
archived Slack fixtures by the same tool, not transcribed from the DMs sent at the time, so a cell
may differ from that week's DM if a `fulfillments` carrier join has changed since.

---

### D40 — `weekly-reship-report`: IT REPORTED THE WEEK IN PROGRESS, AND PUBLISHED denom 0 AND denom 2 (2026-09-01)

**Scope.** The weekly one-tab-per-week reship report: routine SKILL at
`~/.claude/scheduled-tasks/weekly-reship-report/SKILL.md` → `ingest/slack_reship/weekly_task.py`
→ `sync.py --report --push` → `sheet_push.py`, writing a Monday-named tab on the reship sheet
(`1JgyYknIxJ3-UJxJOX-y78rf8cPNhT0uPy5FUw2zO9wE`, id cached in
`_outputs/cache/reship_sheet_id.txt`). Beat `slack-reship`, 10d in `automation_health.EXPECTED`.
Distinct from D39 (`Vendor Matrix`, the history tab) and from the Apps Script report above; the two
Slack-sourced routines share `sync.main()` and must not be conflated.

**The observation — the numbers were wrong on the sheet, not merely missing.** `weekly_task`
computed `current_week_monday()`, the Monday of the CURRENT week, and the routine fires Tuesday
around noon. So it asked how a week that began ~28 hours earlier had gone: the Mon–Sun ticket
window was ~1.5 of 7 days old, and the `_SHIP_<Monday>` cohort had barely begun to fulfil. Two tabs
went out and are still on the sheet:

| tab | generated | denom published | true cohort (recomputed 2026-09-01) | tickets |
|---|---|---|---|---|
| `2026-07-20` | Tue 07-21 12:11 | **0** | 2082 | 5 |
| `2026-08-10` | Tue 08-11 12:10 | **2** | 2365 | 1 |

Every `% denom` cell on `2026-07-20` is `—`; on `2026-08-10` the rates are computed over 2. The
tabs that look right — `2026-06-29` (denom 2554), `2026-07-13` (1987 vs 2026), `2026-08-24` (2545)
— are precisely the ones whose run landed LATE (Wed/Fri catch-ups after the token bug), so being
late was accidentally the only thing producing a correct number. `fulfillments` was NOT stale: the
recomputed denominators for 08-24 and 06-29 match their tabs exactly.

1. 🔴 **Report the LAST COMPLETE week, never the week in progress.** `target_week_monday()` =
   `today − (weekday + 7) days`. Day-agnostic on purpose: `today − 8 days` assumes a Tuesday fire
   and silently reports the wrong window on a catch-up, which is common here. Same formula and same
   reason as the sibling `weekly-shipping-vendor-matrix` routine — they read the same Slack channel
   and the same denominator, so they must not disagree about which week they mean.
2. 🔴 **A zero is a claim: `assert_denom_publishable()` refuses the write, and PROVES which zero it
   is.** `denom == 0` has two causes that render identically downstream, so the gate runs a control
   probe (`fulfillments` carrying ANY `_SHIP_` tag) before it says anything: control 0 → the join
   itself matched nothing (unsynced table, tag format moved); control > 0 → the join works and this
   cohort is simply absent (wrong Monday, or the week has not shipped). A third arm catches the
   partial cohort: below `0.5 ×` the trailing-8-week median. **The floor is a fraction of a measured
   median, never a tuned constant** — the two real failures were 0 and 2 against a median ~2364, so
   no constant needs defending. Scoped to `--push`: `--report` alone prints to stdout where a reader
   sees the number in context; a tab is durable and nobody re-checks it. D39's `VM_ZERO_DENOM`
   guards the ledger path independently — do not merge them.
3. 🔴 **Do NOT route around a refusal.** Not with `--denom`, not by choosing a different `--week`,
   not by re-running. The two bad tabs ARE what publishing past this gate looks like.
3b. 🔴 **`assert_week_complete()` guards the NUMERATOR; the denominator gate cannot.** Found
   2026-09-01 dry-running the backfill below: week `2026-08-31` sailed through
   `assert_denom_publishable` (denom 2471, a full real cohort) and would have published **0
   tickets / 0.00%**, because that week had begun the day before and its Mon–Sun ticket window
   still had ~1.5 of 7 days to run. That is the denom-0 defect wearing the opposite face — there
   a truncated denominator under a real numerator, here a real numerator-window under a full
   denominator — and a denominator check is structurally blind to it, because the denominator
   looks perfect. A week is publishable only once its Sunday has passed. This also makes a
   hand-passed `--week` safe, not just the routine's own path. `NotPublishable` is the base of
   both refusals; catch that to mean "refused".
4. 🔴 **The beat is gated on the PUBLISHED TAB, not on reaching the end of `main()`.**
   `sync.main()` returns the sheet URL and `weekly_task` beats `slack-reship` only if it is truthy;
   otherwise it exits rc=3 and writes no beat. This routine is exception-only in Slack, so the beat
   is the sole evidence it still runs — a beat on a run that published nothing forges exactly the
   signal the dead-man switch withholds. Never move the `beat()` above the URL check.
5. 🔴 **Write the tab BEFORE clearing it — never `clear()` then `update()`.** They are two API
   calls; clearing first opens a window where a refused `update` (quota, 5xx, dropped connection)
   leaves the week's tab EMPTY, the previous good numbers gone and nothing saying so, on a re-run
   that was supposed to be idempotent. `push()` now updates first and then `batch_clear`s only the
   residue below and right of the payload, growing (never shrinking) the grid first. Worst case is
   new numbers plus a few stale trailing rows — visibly wrong beats invisibly blank.
6. **What this report does NOT touch, so do not chase these when it looks wrong:** `delivery_status`
   and `shipments` are not in its path at all. The carrier comes from `fulfillments.tracking_company`
   via `CARRIER_CANON`, and the denominator from `fulfillments.tags LIKE '%_SHIP_<Monday>%'`. So the
   `shipments.hub` dirt (`''`, `HQ_IGNORE`, `Unknown`, `<hub>AHB` suffixes) and the 08-25→09-01
   `delivery_status` outage cannot have moved a single number on any tab. `unknown` in the carrier
   column means the fulfillment row had no `tracking_company` **or** the ticket carried no order
   number; `unjoined` means no `fulfillments` row matched — both are rendered as their own vendor
   rows rather than being folded into a real carrier, and that must stay true.
7. **It runs from the DEV tree** (`Claude Projects/AppyHour`), pinned by the `cd` in the routine's
   one invocation shape. `C:\AppyHourProd\AppyHour\ingest\slack_reship\` holds a stale copy (last
   touched 08-29, i.e. predating even the bootstrap fix) that nothing executes. Do not repoint the
   routine at prod, and do not treat the prod copy as the authority.

**Verification (2026-09-01).** Gate replayed read-only against the live DB over both real failures
and every good week: `2026-07-20`/denom 0 REFUSED (control probe found 121,324 tagged fulfillments,
so the join was proven sound and the cohort proven absent), `2026-08-10`/denom 2 REFUSED (floor 1193
= 50% of trailing median 2386), and 1987 / 2545 / 2554 / 2471 all ALLOWED. Beat gating exercised
with `sync.main` and `heartbeat.beat` both stubbed — URL → `rc=0` + one beat; `None` and `""` →
`rc=3` + no beat; gate refusal → exception + no beat — with the real `C:\AppyHourData\heartbeats.json`
confirmed byte-identical before and after. Push ordering exercised against a fake worksheet: no
`clear()` call at all, `update` strictly before `batch_clear`, and a forced `update` failure left the
prior tab contents intact while the error propagated. `main()` was never run: it writes a live sheet.
