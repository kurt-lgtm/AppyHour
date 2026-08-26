# TAB_NORTH_STARS.md — Running Reship sheet: per-tab NORTH STAR + GOTCHAS

**Sheet:** `1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU` ("Running Reship" pivot sheet), bound
Apps Script project "Running Reship". One section per visible tab: what the tab is FOR and what a
reader must NOT do with it.

> 🧭 **NORTH STAR OF THE WHOLE DOC (Kurt 2026-08-26: "this whole thing has to be headless —
> that's the north star of the whole doc"):** every tab on this sheet fills, matures, freezes,
> and self-corrects **with zero human steps**. No manual refresh, no hand-typed cell, no
> UI-only arm switch, no "Kurt runs this in his terminal." DB writes go through real-context
> scheduled tasks; sheet operations go through the service account; corrections ship as gated
> self-applying one-shots. A change that adds a manual step moves AWAY from this doc's north
> star — flag it, don't ship it. A tab with no scheduled owner (writer-ownership gate) is a
> violation of this north star, not a footnote.

> 🔴 **This is a READER'S companion, not a rules SSOT.** The rules live in
> [`RESHIP_REPORT_RULES.md`](RESHIP_REPORT_RULES.md) (R1–R17, D1–D35) and
> [`EXCEPTIONS_ALERT_RULES.md`](EXCEPTIONS_ALERT_RULES.md) (P1–P14). Change rules THERE first;
> this file cites them and must be updated in the same commit when a cited rule changes.
> Every north star below is derived from a recorded Kurt/Dan directive (cited). Where none exists
> the section says **`NORTH STAR: MISSING — needs Kurt`** — per the elicit rule, nothing here is
> invented to fill a gap.

## Cross-tab facts (apply to every tab — misread any one and the numbers lie)

- **Delivered = Shopify DELIVERED event ∪ ParcelPanel `status='delivered'`. Neither feed is
  complete alone** (D6: PP hid 224 deliveries; Shopify missed OnTrac's final scan on 2).
- **`CONFIRMED` fires at LABEL CREATION and is NOT movement** (D10). A real scan is
  IN_TRANSIT / OUT_FOR_DELIVERY / ATTEMPTED_DELIVERY / READY_FOR_PICKUP / PICKED_UP / DELIVERED.
- **Blank ≠ zero, everywhere** (A5, D19, D29c, D33). A blank cell means "not measured / did not
  exist"; a 0 is a claim. Never sum a blank, never fill one with 0.
- **OnTrac ≡ LaserShip — one carrier, canonical name `OnTrac`** (D5). Two buckets halves the share.
- **The promise clock is PER BOX, from that box's own pickup (ET), never the cohort's Monday**
  (D18). Ship weeks are multi-leg (Tuesday Dallas is standard).
- **Matured/frozen columns are Kurt-owned history** (D15/A1). The script refuses them; so should
  a hand edit. A disagreement inside a live window is a bug report, not an edit.

---

## Hold

> ⏳ **PLACEHOLDER — SEMANTICS CHANGING TODAY (2026-08-26).** Kurt directed a cutover to
> **unfulfilled-only, all hold types**; that change is in flight in a parallel session
> (HoldRow12) and its D33 amendments had not landed in `RESHIP_REPORT_RULES.md` when this file
> was written. Read D33 for the final semantics; columns **before 2026-08-26 are on the original
> snapshot basis** (all `_HOLD` orders, fulfilled + unfulfilled split out) and are **not
> comparable** to post-cutover columns. Do not extend this section until D33's update commits.

**NORTH STAR** (D33; `ShippingReports/CLAUDE.md`): a daily snapshot of the
`_HOLD` → `_CSHOLD`/`_FLOWHOLD`/`_UNRESOLVED` migration backlog, so the drain is visible day
over day — **"`_HOLD` reaching zero is the goal"** (D33 rule 6). Readers: Kurt + Dan.

**GOTCHAS (negatives-first):**
- 🔴 **A hold snapshot CANNOT be back-filled — a missed day is LOST, not late** (D33). Shopify
  tags carry no application timestamp. Columns 08-21…08-24 are blank and always will be; the tab
  "looked maintained" for five days while nothing wrote it.
- 🔴 **WRITE-ONCE per date.** A filled cell is never overwritten, corrected, or blanked — by
  script, backfill, or human. Disagreements are reported (`Hold DISAGREEMENT`), never repaired.
- 🔴 **The HOLDS-OPENED rows in columns B–J (08-12…08-20) are on the UTC basis** — the 08-20
  one-shot sliced `createdAt` raw. Four cells (08-17/08-18, two rows) differ from the Eastern
  truth; the correction is an ARMED one-shot (`holdFixEtBasis`), **NOT APPLIED** as of 2026-08-26.
  Everything from 08-21 on is Eastern (Kurt 2026-08-25: "it has to be all Eastern").
- **"Moved to _HOLD" is a PROXY** (orders *created* that date carrying a hold tag *now*) — it
  undercounts released holds and misdates late-applied CS holds. "Aging" = days since order
  created, not days since held.
- **`_UNRESOLVED` is terminal, not an active hold** — it is what replaces the tag after 2 CS
  pings; it is excluded from the active union and has its own row.
- **The tab is named `Hold`; `_HOLD` is the Shopify tag.** `(none)` in an id list = measured
  zero; an empty cell = not measured.

## Reship

**NORTH STAR** (Kurt 2026-08-25 — this tab is CANON for cohort reship numbers,
[[reship-tab-is-canon-not-product-mix-t]]; serves the report's approved north star, Dan
2026-07-09: *"How can I check on this without asking"*): the one place Dan/Kurt read a cohort's
reship performance — metrics as rows, cohorts as columns, with By Issue / By Carrier / Arrived
Warm by State / Delayed by State breakdowns in both % and counts.

**GOTCHAS (negatives-first):**
- 🔴 **`Product Mix (T)` is DEAD — deleted by Kurt 2026-08-25.** It drifted stale silently after
  Dan renamed the live tab (2026-08-12): `writeTabTo_` minted an empty ghost `Product Mix (T)`
  and fed it for days while Dan's tab froze. Never recreate it; tab creation is loud now and the
  write target lives in `PM_T_TAB` only.
- **`Unresolved` / `Potential` here reflect the CURRENT Triage tab** (built after `writeTriage_`
  + flush) — they move when a Triage decision lands, and they inherit every Triage caveat below
  (register latency, D26 vocabulary).
- **`Potential` = all reships + all unresolved; `Actual` = entered reships only** — do not quote
  Potential as a failure count; part of it is un-triaged candidates.
- **% sections divide by the reship-excluded cohort denominator** (R7: live Shopify tag count,
  `-status:cancelled -tag:'Reship'`) — never re-derive against a raw tag count.
- **Warm ≠ routing** (R12): Arrived Warm/Burst is a packaging bucket, Delayed/3+Days a routing
  bucket; a reship delivered in >2 transit days is reclassified warm→delayed
  (late-supersedes-warm override) — the By Issue block reflects the override, not raw ticket text.
- **Column creation follows format → header → values → assert** (D13 procedure); rows are never
  inserted by the refresh (D13) — a missing bucket is a human row-add, not a hand edit.

## TnT2

**NORTH STAR** (locked definitions Kurt 2026-08-06; D16 approved model; D18): answers *"did this
cohort's boxes arrive inside the 2-day promise?"* on **each box's own pickup clock** — `2 Day`
vs `3+ Day` per cohort with hub/carrier/state/box cuts, plus the four observation rows that say
*why* the undelivered are undelivered. Kurt's shape, verbatim: *"fine we go with tnt3, tnt4+,
still in transit."*

**GOTCHAS (negatives-first):**
- 🔴 **D34: the `_SHIP_2026-08-10` dimension blocks are FROZEN WRONG and NOT re-derivable.**
  Empty buckets kept high-water marks for weeks (hub +2, carrier +9, state +18 over the
  headline). `PA_ASSERT_SECTION_SUM` now guards new writes, but those frozen cells cannot be
  repaired — **do not trust wk0810 dimension cuts on this tab**, and check the headline (which
  was always correct) instead. wk0817 self-heals only if refreshed before its 2026-08-27 freeze.
- 🔴 **`3+ Day` includes EVERY undelivered box past its own promise** (D9 survivorship) — it is
  not "delivered in 3+ days". `2 Day + 3+ Day + pending == Total`. A box with no pickup anywhere
  is `pending`, never late (D18).
- **The observation rows (Still Moving `=< TNT2` / `> TNT2` / no scan 24h+ / never picked up)
  partition Not Arrived and are summed into NOTHING** (D16/D27). A big `Still Moving =< TNT2`
  mid-week is boxes inside their own promise — Kurt: *"1396 … is misleading … its only
  wednesday."* Kurt's `=<` spelling is deliberate; do not "correct" it.
- **`TNT1` is a nested SUBSET of `2 Day`, never a sibling** (D22/D22b); frozen columns stay
  blank on it forever — blank ≠ "no next-day boxes that week".
- **The words "Lost in Transit" appear nowhere on this tab by design** (D16) — that phrasing is
  what Dan reacts to; the Lost in Transit tab owns it.
- **The blank-label rate rows pair `2 Day`+`3+ Day` by LABEL, not adjacency** (D19a) — nested
  rows sit between them; a positional read of the tab's rows is wrong.
- **Columns freeze at age 10** (`PA_MATURITY_DAYS`, D15) — a matured column is history, and the
  07-27 column carries Kurt's deliberate even haircut (105, D4): a recompute "fixing" it to 126
  is the exact thing A1/D4 forbid.

## Notifications

**NORTH STAR** (D24/D30/D32; Kurt 2026-08-20, verbatim: *"Orders have to be delivered by 8/14 at
the latest. any email after that is an issue"* · *"it should be mature on the friday"*): did each
cohort's customers get their Order Placed / Shipped / Delivered emails and SMS **by the delivery
SLA — ship + 4 days, the Friday**. These rows are a delivery-PERFORMANCE number measured to a
deadline, not an eventually-sent count.

**GOTCHAS (negatives-first):**
- 🔴 **Numbers straddling the 2026-08-20 maturity-window change are NOT comparable across
  weeks.** The window went from ship+12d to ship+4d (D32) and `_SHIP_2026-08-03` was restated
  under it (10 cells, D32 — *"these numbers MOVED after publication — Dan may have quoted the old
  ones"*). The wk0817 delivered-email figure (~84%) sits across that change — do not trend it
  against pre-D32 columns.
- 🔴 **A late send is EXCLUDED, not lost** — sends after the Friday are logged
  (`ntLateSignalNote_`), not published. A drop here can mean "late", not "never sent"; whether
  the late count gets its own row is an open Kurt call (D32).
- **`Order Placed` and `Order Shipped` emails are SHOPIFY order events, not Klaviyo** (D24/D30);
  `Order Shipped` = the fulfiller's confirmation at fulfillment, NOT Klaviyo's carrier-scan
  In-Transit flow — different events, different scales (D30). Delivered email is Klaviyo-only
  and stays BLANK in Apps Script (sweep declined — the metric cannot fit the 360s ceiling, D31).
- **Grains differ:** Shopify rows are per ORDER, Klaviyo rows per DISTINCT PROFILE (D24) — the
  gap is repeat customers; do not "fix" it.
- **`SMS Sent` = Klaviyo `Received Text Message` (delivered to handset); `SMS Engaged` =
  `Clicked Text Message`, clicks only — it UNDERSTATES engagement** (no reply metric exists,
  D24). Never read it as "responded".
- **`Arrived` is a MIRROR of `Lost in Transit` and follows THAT tab's 10-day clock**
  (`NT_MIRROR_MATURITY_DAYS`, D32) even though the rest of the column freezes at 4.
  🔴 Standing disagreement: `_SHIP_2026-08-03` holds a hand-typed 2,075 vs authority 2,268
  (D29c/D32) — deliberately left visible; a disagreement is reported, never corrected.
- **Human-typed values are never overwritten** (D29c fill-blanks-only on matured columns) — to
  hand a cell to the script, clear it.

## Cost

**NORTH STAR: MISSING — needs Kurt.** *(Best guess, clearly a guess: the on-sheet home for the
cost half of the D35 Carrier Mix — Kurt 2026-08-25: "have a second row under each carrier service
lane outline cost at a high level. of course those cells have to be on a different refresh
because digital ocean will get those invoices later." Confirm or rewrite.)*

**GOTCHAS (negatives-first):**
- 🔴 **This tab has NO WRITER.** It holds only ship-week headers and Dan's note *"Let's talk this
  through before you do it Kurt"* — anything that appears here today was hand-typed. The
  canonical carrier-mix/cost tool (`carrier_mix_pivot.py`, D35) deliberately has **no sheet-write
  path** and renders to `_outputs/reports/carrier-mix-pivot.md`.
- 🔴 **Do not wire a `.gs` writer for it** — the Apps Script project cannot reach `shipping.db`,
  where the invoice cost and routing-tag service token live (D35 "Why it is not a `.gs` tab").
- **If it is ever filled, D35's cost rules bind:** an empty cost cell is "not invoiced yet",
  never `$0`; partial cells lead with invoice coverage; invoices only, never quoted/estimated
  rates; OnTrac invoices lag up to ~4 weeks.

## Routing Match

**NORTH STAR** (D23, Kurt verbatim: *"for Routing match, let's do this walk forward or something
8/10 is already matured. we shouldn't refresh this."* — motivated by
`2026-07-29-tag-mismatches-vF.csv`, matured 07-13 hub-match ~32%): did RMFG execute the routing
the engine assigned — routing TAG vs the carrier that actually carried the box, one ship-time
snapshot per cohort.

**GOTCHAS (negatives-first):**
- 🔴 **Frozen ≠ stale — the freeze IS the correctness.** Tags are MUTABLE after ship (376
  corrective writes on wk0810 alone), so this number **degrades with age instead of
  converging**; the first measurement is the only valid one. Kurt: *"matured on the carrier end.
  Shopify had the wrong tags so the data is wrong."* Write-once per cohort
  (`PA_ASSERT_ROUTING_FROZEN` refuses, never repairs); it does NOT follow the 10-day model.
- 🔴 **The Hub row's `n/a (immature)` is a PLACEHOLDER, not a failure** — actual hub needs
  carrier invoices (~1wk lag). Never fill it from the routing tag: that compares the tag to
  itself and always reads 100% (D11).
- **Orders with no assignment tag are uncomparable, not "matched"** — counting them matched once
  inflated the rate to a false 96.6% (D11).
- 🔴 **A FENCE IS NOT A PREDICTION (D36, from `_SHIP_2026-08-24` forward).** Bare `!ANY - <Hub>`
  delegates the carrier to RMFG — it leaves BOTH numerator and denominator (739 of them scored as
  misses once dragged wk0824 to a false 69.4%; true committed-only rate 100.0%). `!ANY FedEx - <Hub>`
  IS a carrier commitment and stays scored. The `Carrier n (committed / fenced)` row carries the
  denominator so the shrink is visible; columns ≤ 08-17 keep their old-basis frozen readings.
- **There is no measured-at stamp yet** (recommended, not built, D23) — a reader cannot see WHEN
  a column was snapshotted; treat cross-week comparisons accordingly.

## Lost in Transit

**NORTH STAR** (locked definitions Kurt 2026-08-06; D16 history — Kurt: the lost number *"should
go down, not up"*): for each matured cohort, did every box eventually ARRIVE — `Arrived` vs
`Not Arrived` with hub/carrier/state/box cuts — so genuinely lost boxes are countable without
mid-flight noise.

**GOTCHAS (negatives-first):**
- 🔴 **D34: the 08-03 (state block) and 08-10 (all blocks) dimension cells are FROZEN WRONG and
  NOT re-derivable** — stale high-water marks from buckets that emptied (state summed 149 vs a
  headline of 16 on wk0810; 186 vs 7 on wk0817 pre-fix). `PA_ASSERT_SECTION_SUM` guards new
  writes; the frozen damage stays. **A reader must not trust those blocks** — the headlines were
  always correct.
- 🔴 **A live cohort's `Not Arrived` is mostly in-flight, not lost** — matured cohorts only
  (locked definitions). The current column is provisional (A4).
- **Veho quirk:** `exception` status WITH a `delivery_date` = **delivered**, not lost.
- **Arrived uses the D6 union** (Shopify ∪ PP ∪ invoice-confirmed); `Not Arrived` is monotone
  non-increasing within a cohort — the writer refuses a rise (D16).
- **Arrival is clock-independent** — D18's per-box promise clock does NOT move these rows.
- **No TNT1 rows here, ever** — Arrived/Not Arrived are arrival measures; a transit-time row on
  this tab is a category error (D22b).

## Triage

**NORTH STAR** (Current-shipped-state spec + D26; feeds Dan's unresolved table): the queue of
Slack `#reship-and-order-requests` posts with **no reship entered yet** — candidates surfaced so
nothing a customer reported falls through between Slack and Shopify entry.

**GOTCHAS (negatives-first):**
- 🔴 **"Unresolved" measures REGISTER LATENCY (Slack post → Shopify entry / decision), not
  customer neglect.** Reading it as "customers we ignored" repeats the 7/06–08 backlog-read-as-
  surge panic (R4's class).
- 🔴 **`no action` and `cs error` are NOT shipping failures** (D26, Kurt: *"it was customer
  service making the wrong call … make sure its not counted"*) — they resolve a row without ever
  counting as a reship anywhere. The CS-error tally (col J/K) exists so that rate is visible.
- **The Decision column is effectively unused in practice** (2 entries ever) — rows mostly leave
  by the reship being entered in Shopify, not by a typed decision. An old row is far more likely
  un-actioned bookkeeping than an ignored customer.
- **Decisions are a CLOSED vocabulary** (D26): unrecognized text keeps the row ACTIVE and warns —
  before D26 a typo (`no acton`) silently deleted a live failure from the count.
- **The Posted column renders in the script timezone = America/Chicago** (project manifest, per
  D33) — **add +1h for ET**.
- **Issue here is the CANONICAL `Shipping::…` label** (Raw Data carries the SHORT label);
  late-supersedes-warm is applied here too. Typed decisions persist in hidden
  `_triage_decisions` — deleting the row alone does not stop it reappearing; a decision does.

## Raw Data

**NORTH STAR** (R17, Kurt 2026-07-13 "Option 2"): the walk-forward, append-only LEDGER of entered
reships — 9 columns (Order · Requested · Created · Issue · Incoming week · Outgoing week ·
Status · Original · Box Type) — the substrate every count and mix tab derives from.

**GOTCHAS (negatives-first):**
- 🔴 **Deleting a row is PERMANENT removal from all counts** (watermark: order# ≤
  `PIVOT_WATERMARK` is never reconsidered; re-add the # to undo). There is no Exclude column —
  delete IS the exclusion, and the Count tabs + Product Mix count ALL remaining rows, no filter.
- 🔴 **Requested ≠ Created** (R4): requested = ticket date, created = Shopify entry date. Dan's
  7/05 entry freeze turned a 5-day backlog into a phantom 3-day surge — week-over-week uses
  REQUESTED only. Blank Requested = UNKNOWN, never estimated (script backfills BLANK date cells
  only; typed dates stick).
- **Everything except the two date columns is script-refreshed each run** — hand edits to
  Status/Issue/cohort/Box are clobbered. Issue is the SHORT label with the late-supersedes-warm
  override re-applied every run (a hand "fix" of the issue reverts).
- **The counting rules R1–R13 are baked into what gets a row:** never Gorgias tags (R1), body-
  confirmed only (R2), attributed to the ORIGINAL order's cohort (R3), deduped orders never tag
  matches (R6). Do not "reconcile" this tab against a tag count.
- **A full-tab wipe DMs Kurt and is NOT auto-restored** (R17). No auto-aging — rows accumulate
  until Kurt prunes.

## Product Mix

**NORTH STAR** (Current-shipped-state spec; normalization per R7): per-cohort reship and
unresolved RATES split by box type (Regular / Medium Tray / Large Tray), with `Potential` (all
reship + all unresolved) vs `Actual` (entered reships only) — rates over the reship-excluded
cohort denominator, never raw counts.

**GOTCHAS (negatives-first):**
- 🔴 **Every % cell is a TEXT-formatted string** (`"1.70%"`) so new cohort columns render without
  manual formatting — they do not survive arithmetic; do not SUM/AVERAGE them.
- **Denominator = LIVE Shopify tag count** (`-status:cancelled -tag:'Reship'`, R7) — never the
  local `fulfillments` table (dead-cadence class) and never RMFG email counts.
- **It is COUNTIFS over Raw Data + Triage** — a Raw Data row deletion or a Triage decision moves
  history here instantly; there is no frozen copy.
- **The `Reship` tab is the transpose and the canon** (Kurt 8/25) — quote cohort numbers from
  there; this tab is the box-type working view.

## Count of requested

**NORTH STAR** (mirrors Pivots block 2, Dan 2026-07-09; R4/R8): reship demand by TICKET date —
the week-over-week comparison series. R4: comparisons use requested date only.

**GOTCHAS:** 🔴 A native sheet pivot over Raw Data — counts ALL rows, no filter (R17): deleting a
Raw Data row rewrites this history. The blank/`(blank)` bucket is UNKNOWN-requested (no ticket
found), never estimated (R11) — the Daily writer's note quantifies it. Never compare a partial
current week raw against a finished one (R8: same-day-offset or maturity-adjusted only; tails run
~7+ days). Unit = deduped reship orders, never tag matches (R6).

## Count of created

**NORTH STAR** (mirrors Pivots block 1, Dan 2026-07-09): reship ENTRY workload by Shopify
creation date — when the team actually keyed the orders.

**GOTCHAS:** 🔴 NOT a demand signal — batch entry makes this series spike independently of
requests (the 7/06–08 batch-entry surge, R4). Comparing this tab week-over-week is the exact
conflation R4 forbids. Same pivot-over-Raw-Data caveats as above.

## Count of incoming week

**NORTH STAR** (mirrors Pivots block 4, Dan 2026-07-09; R3): reships by the ORIGINAL order's
ship cohort — *which shipped week broke* — the attribution view behind the per-cohort rate.

**GOTCHAS:** 🔴 Attribution is to the original order's `_SHIP_` tag, confirmed by ticket where
possible (R3) — the 7/06–08 "surge" was 64 orders all remediating the 06-29 cohort. Counts here
are raw; the RATE against the cohort denominator lives on Product Mix / Reship (R7). Recent
cohorts are immature (R8) — their counts still grow for ~7+ days.

## Count of outgoing week

**NORTH STAR** (mirrors Pivots block 3, Dan 2026-07-09): reships by the REPLACEMENT order's
`_SHIP_` tag — the reship load landing on each upcoming ship week (capacity signal).

**GOTCHAS:** 🔴 Never read this as a cohort's failure count — outgoing week is when the
replacement ships, not when or why the original failed (that is Incoming week). Replacement
orders carry the outbound cohort tag, which is exactly why R7 excludes `tag:'Reship'` from every
denominator — do not re-add them.

## Exceptions

**NORTH STAR** (`EXCEPTIONS_ALERT_RULES.md` 🧭, approved Kurt 2026-07-30): *"Dan and Kurt learn a
box has failed from Slack, before the customer tells us — and they trust the channel enough to
leave notifications on."* Precision before recall — this tab is the durable record behind the
#exceptions pings (the 2026-07-30 burn: 19 failed boxes, zero tickets, zero reships, nobody knew).

**GOTCHAS (negatives-first):**
- 🔴 **Label creation ≠ movement.** The never-picked-up class false-fired off label-creation
  signals (`CONFIRMED` fires at label creation; PP's *"package data was sent to <carrier>…"*
  prose lingers after the box moves) — the tab once flooded to 1,798 rows, 898 of them
  never-picked-up, of which the true count was **2** (the 2026-08-10 87% false-positive purge).
  Old rows from before that purge are not evidence.
- 🔴 **A quiet tab is NOT a healthy sweep.** The PP fetch has failed silently before (3 days of
  zero polling read exactly like a clean week; later a 400/400 hard-failure wall posted five
  identical useless alarms, P14). Before trusting silence, check the heartbeat
  (`EXC_LAST_RUN_AT`, menu → Check properties) — a run that polls zero is not an all-clear.
- **Cadence is record-daily, ping Wed–Sun** (Kurt 2026-08-10, committed to Dan): Mon/Tue rows
  accumulate silently and post Wednesday if still live. Rows without pings on Mon/Tue are
  normal, not a Slack failure.
- **`PP_NO_RECORD` rows mean "NOT BEING CHECKED"** (P9 quarantine) — an undelivered box we
  stopped polling because ParcelPanel has no record; if it is a real box it is unmonitored.
- **Scope = current + previous cohort only** (P10) — older exceptions are deliberately absent,
  not resolved.
- 🔴 **Never clear this tab without running `excRepairLoggedState()`** — the tab and `_exc_state`
  are separate; a tab-only purge permanently blocks re-recording of every cleared (order, class).
- **Never auto-reship from a row** (constraint 4) — notify only; reship is a human call.

## Retired: `Product Mix (T)`

Deleted by Kurt 2026-08-25. It was the pre-rename write target; after Dan renamed the live tab
to `Reship` (2026-08-12) a ghost was silently recreated and drifted stale
([[reship-tab-is-canon-not-product-mix-t]]). **`Reship` is canon.** Do not recreate; tab
creation is loud (Slack) and the write target lives in `PM_T_TAB`.

## Machine tabs — DO NOT EDIT

`_pp_cache` · `_nt_sweep` · `_triage_decisions` · `_seed` · `_state` · `_exc_state` — hidden
script state, not reports. Hand edits corrupt dedup/caching/resume logic:
- `_pp_cache` — terminal ParcelPanel memo (delivered facts are immutable; 21-day give-up, P11/F1).
- `_nt_sweep` — resumable Klaviyo sweep checkpoints (D29); clearing one throws away paid pages.
- `_triage_decisions` — persisted Triage decisions (D26); this is why a deleted Triage row stays
  gone.
- `_seed` — retired (the R15 `_seed`/`CUTOVER` model is dead); kept only as history.
- `_state` — the reship ledger state (R15's hidden-tab store).
- `_exc_state` — Exceptions dedup + poll state; 🔴 clearing the Exceptions TAB does not clear
  this, and clearing THIS without the repair functions breaks dedup both ways (see
  `EXCEPTIONS_ALERT_RULES.md` "_exc_state rot").
