# EXCEPTIONS_ALERT_RULES.md — constraints SSOT for the #exceptions Slack alerter

🔴 **PRE-CHANGE GATE — read this file before touching the exceptions alerter.** Change the rules
HERE first, in the same commit as the code. Constraints were authored before the implementation
(feature-constraints-doc gate).

**Status:** APPROVED design, NOT yet built. Kurt decisions 2026-07-30 (Slack #exceptions thread
with Dan, screenshots in session). Sibling doc: `RESHIP_REPORT_RULES.md` (the host job).

🔴 **IF YOU ARE HERE ABOUT PARCELPANEL CALL VOLUME, READ [P12](#p12--coverage-is-never-rationed-only-speed-is-kurt-go-2026-08-20)
FIRST.** Roughly a third of this file was written to ration ParcelPanel calls against a limit that
**does not exist** — "2,500/week" is Kurt's average weekly *order* count, not an API budget. The
real limit is **120 requests per minute**, reported in `x-ratelimit-limit` on every response.
**Coverage is never rationed; only speed is.** Everything above P12 that subtracts, allocates,
paces against a balance, or caps a run is either deleted or a tombstone — do not implement from it.

---

## 🧭 NORTH STAR

**Dan and Kurt learn a box has failed from Slack, before the customer tells us — and they trust the
channel enough to leave notifications on.** A channel that cries wolf gets muted, and a muted
channel is worse than no channel: it converts a real failure into a silent one. Precision before
recall. (Serves AppyHour's north star: *"every routine decision automated with loud failures,
never silent ones."*)

---

## Why this exists (the burn)

2026-07-30: counting undelivered boxes for one month surfaced **8 damaged in transit, 7 returned to
shipper, 3 never picked up, 1 "Lost by driver" — with zero CS tickets and zero reships on any of
them.** ParcelPanel had reported every one of those events, daily, the whole time. We had no
surface that read them. Customers who didn't write in simply never got their box.

Dan, same day: *"We should try to get pinged on this somehow through parcel panel for exceptions."*
Kurt's caveat, same thread — the thing this doc mostly exists to enforce: *"not all exceptions are
real issues… sometimes you get a notification and they just changed the label."*

---

## 🔴 Constraints (negatives-first — what NOT to do)

1. **NEVER alert on a raw PP `EXCEPTION` status.** On real 6/29–7/20 data, **23 of 71** exception-bucket
   boxes had already been delivered — Veho stamps an exception scan en route and never flips the
   bucket back. Alerting on the status field alone makes ~1 in 3 pings a false alarm on week one.
   **Classify on the checkpoint `detail` text, never the status bucket.**
2. **NEVER read `checkpoints[-1]`, and never look for `description`/`message`.** PP orders checkpoints
   **newest-first** and puts the text in **`detail`**. Getting this wrong produced a blank event on
   30,412/30,422 rows for the life of the local sync (fixed 2026-07-30, `GelPackCalculator@b49a4ba`).
   Skip checkpoints whose `status` is null — those are AppyHour storefront copy injected into the PP
   timeline ("Orders are prepared fresh weekly"), not carrier scans.
3. **NEVER alert twice for the same (order, class).** Dedup in `_exc_state`. An hourly poll re-reads
   the same open box every hour; without dedup one stuck box posts 24×/day and the channel dies.
4. **NEVER auto-create a reship.** Notify only (Kurt 2026-07-30). A false positive ships a free box,
   and the false-positive rate on raw exceptions is ~32%. Reship stays a human call.
5. **NEVER point this job at the live reship sheet.** Data surface is
   `1kk1Qld-7QDIkhIKL93EIc6NGmOhnD_PZcJdTNX9m8Pg` (Kurt's sheet, reached via `openById`).
   Dan's live pivot report is `1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU` and this job never
   writes to it.
5b. **The code lives in the EXISTING "Running Reship" script project** (scriptId
   `15K0MrUssFqacWybQAToz6CeHTouRU4IeNY4-DzZ4NeE1rBCCNGpGjAjv`), not a project of its own
   (Kurt 2026-07-31). It has to: `excSeedCohort_` depends on `Code.gs`'s `shopifyGql_()`, so a
   standalone copy throws "shopifyGql_ is not defined" on its first run. Sharing also reuses the
   already-set properties, OAuth grant and Slack bot.
   🔴 **Price of sharing: a syntax error here takes the hourly reship report down too.** Run
   `excSelfTest()` after every edit. And `hourlyExceptionSweep` must **NEVER** be called from
   `refresh()` — it throws on failure by design, which would abort Dan's report. Separate trigger,
   so the two fail independently.
   🔴 **No duplicate top-level names.** Two `onOpen` definitions in one project do not merge — the
   later silently wins and the other menu disappears. That is why the menu installer here is
   `onOpenExceptions`, not `onOpen`. Verified 2026-07-31: 0 collisions between this file's 22
   globals and the 79 in `Code.gs`/`PivotSheet.gs`.
6. **NEVER post to `SLACK_WEBHOOK`.** That property is bound to public **#reships**. #exceptions is
   **private, `C0BLKKPAW8P`** — requires `SLACK_BOT_TOKEN` + the bot invited to the channel.
   Posting a customer-name-bearing failure into a public channel is a privacy regression.
6b. **NEVER post an OPERATIONAL failure into #exceptions** (directive P8, Kurt 2026-08-19). That
   channel is Dan's box-problem feed. Infra noise there is what gets it muted, and a muted
   #exceptions converts every real customer exception into a silent one — constraint 3's failure
   mode arriving by a different door. Failures go to **#kurt-ops** (`C0BT47XG8CW`).
7. **NEVER let a PP outage look like "no exceptions."** Zero findings on a run where the PP fetch
   errored must alert as a FAILURE, not pass silently as good news (fail-loud, per host job).
8. **NEVER widen the poll set to all cohorts.** Apps Script has a 6-minute execution ceiling and PP
   is one GET per order. Poll only OPEN boxes in the live cohorts (see below).
   🔴 **Directive P10 makes this structural:** the CANDIDATE SET is `cohort IN (current, previous)`,
   derived from Shopify `_SHIP_` tags and ratcheted forward-only. Everything older is not polled,
   not classified, not appended and not alerted — with the tradeoff stated there.

---

## 🔴 Two rules that killed an 87% false-positive rate (Kurt 2026-08-10)

The tab filled with 1,798 rows, 898 of them `never picked up by carrier`. Applying both rules
below to that same data leaves **2**.

### 1. STRUCTURED FIELD BEATS FREE TEXT — and one feed is never enough

**Never classify off checkpoint PROSE when a structured signal contradicts it, and never trust a
single feed.** ParcelPanel's newest checkpoint keeps reading *"the package data was sent to OnTrac,
but we have yet to receive the package"* long after the box has moved — that string is the
`NEVER_PICKED_UP` trigger. Measured: **224 of 598** rows had a movement scan **already visible in
Shopify at the moment the row was written**. The classifier now takes a `movedElsewhere` argument
and returns `IN_NETWORK` when the other feed has any of `IN_TRANSIT` / `OUT_FOR_DELIVERY` /
`ATTEMPTED_DELIVERY` / `READY_FOR_PICKUP` / `PICKED_UP` / `DELIVERED`.

Same family as **`CONFIRMED` is not movement** (it fires at label creation) and as the reship
refresh's union rule (**PP hid 224 deliveries; Shopify missed OnTrac's final scan on 2**). When the
shared truth module lands, this belongs in it — three consumers now.

🔴 **The union costs ZERO ParcelPanel budget.** It rides on the Shopify call
`excResolveDelivered_` already makes to close delivered orders — one request, two jobs.

### 2. `EXC_NEVER_PICKED_MIN_DAYS = 3` — a floor, because feeds lag

Was **1 day**, and rows fired at ~32h while scans were still arriving. Measured: **299 of 598**
rows had their first scan land **after** the row was written (one at +1.5h, one at +16h) — invisible
to *every* feed at sweep time, so no union can catch them. Only patience can.

🔴 **A floor cannot lose a real case.** The sweep re-polls hourly, so raising it **delays**
detection rather than dropping it. The genuine wk0803 never-collected boxes were silent **7–33
days**; a 3-day floor still catches every one, at worst two days later. An earlier analysis claimed
a floor would cost 63 of 64 real detections — that was wrong: it treated *suppressed now* as *lost
forever*.

The two rules reinforce each other: by the time the floor lets a classification fire, both feeds
have had three days to catch up, so the lag that caused the false positives has already resolved.

## 🔴 Four fixes that ended the "it never fired" class (Kurt 2026-08-17)

Two boxes Kurt flagged had failed in plain sight and never produced a row: **#170893** (Lisa Olson,
VA — FedEx `status=FAILED_ATTEMPT`, checkpoint 8/14 19:53 *"Delivery exception, Incorrect address,
HERNDON VA 20171"*, then a benign *"At local FedEx facility"* 8/15 09:31) and **#169174** (Maria
Wood, NY — label 8/10, pickup 8/14, Shopify `displayStatus = DELAYED` / *"Package Delayed."*,
newest PP checkpoint a benign IN_TRANSIT). Four independent holes, all four now closed:

1. **NEVER read only `checkpoints[0]`.** The classifier saw one scan. A single later, harmless
   facility scan hid a real failure *forever* — nothing re-surfaces it, because the newest
   checkpoint only gets newer. It now walks the newest **`EXC_CP_SCAN = 5`** carrier checkpoints,
   newest→oldest, stopping at the first that decides.
   **Precedence, stated:** within one checkpoint, failure text is tested before the bare-text
   `delivered` suppress (substring trap); across checkpoints, a **DELIVERED scan newer than a
   failure still suppresses** — that is what preserves the 23-of-71 already-delivered suppression.
   A *benign* newer scan decides nothing and the walk continues past it.
   The reported `event when` is the classifying checkpoint's time, never the benign one's.
2. **NEVER discard the structured field.** PP's `status` was parsed and never read.
   `status === 'FAILED_ATTEMPT'` → `ATTEMPT_FAILED`. It sits after the window walk so a more
   specific failure text (damaged/returned/lost) refines it and a genuine delivery still
   suppresses — structured-beats-free-text means it must never lose to *benign* prose.
3. **NEVER write a predicate from one carrier's wording.** `ADDRESS_ISSUE` knew only OnTrac's
   "need additional information to complete", so FedEx's "Incorrect address" fell through to
   IN_NETWORK. Widened to
   `/need additional information to complete|lack of an access code|access code|incorrect address|address (is )?(incorrect|invalid)|delivery exception.*address/i`.
4. **New `DELAYED` class** off Shopify `displayStatus === 'DELAYED'`. It rides the Shopify call
   `excResolveDelivered_` already makes — **ZERO extra ParcelPanel budget**, same one-request-two-
   jobs trick as the movement union. 🔴 **Floor `EXC_DELAYED_MIN_DAYS = 3`, and it is not
   optional:** Shopify stamps DELAYED off routine carrier "Package Delayed" scans that clear within
   a day, so firing on the flag alone floods the channel. Three days = the 2-day promise is already
   broken, so it is a real failure rather than feed lag; and because the sweep re-polls hourly a
   floor *delays* detection, it cannot lose a case. Tested BEFORE the movement union, because a
   delayed box HAS moved and the union would otherwise swallow every one.

Measured, not guessed — these are high precision, not a flood: **2 new hits in wk0810, 1 in wk0803,
0 in wk0817.** `excSelfTest()` covers all four with the real carrier strings (25 cases, PASS).

## 🔴 `_exc_state` rot, and the two manual repairs

**A cleared tab does not clear the state, and that silently kills re-recording.** Found 2026-08-17:
`_exc_state` claimed ~1,839 orders carried a `logged_classes` entry while the Exceptions tab held
**3 rows** — the false-positive purge emptied the tab only. `rec.logged` is the tab-write dedup, so
every one of those (order, class) pairs could never be appended again; a genuine exception among
them was permanently invisible. **Any future tab purge must be paired with the repair below.**

- **`excRepairLoggedState()`** — reads the Exceptions tab, builds the set of (order, display-label)
  pairs actually present (order without `#`, label lower-cased: historical rows carry both
  "Address Issue" and "address issue"), and drops from `logged_classes` any token whose label is
  not on the tab. Touches nothing else — `alerted_classes`, `open`, `carrier`, `cohort`,
  `last_seen` copy through untouched, so it cannot cause a duplicate Slack ping (Slack dedup rides
  `alerted`). Idempotent, no network calls.
- **`excMarkRecordedAsAlerted()` — the UNMUTE GUARD.** While `EXC_DRY_RUN` is true the sweep
  appends rows and stamps `logged` but deliberately leaves `alerted` empty, so flipping the flag
  would post the entire recorded backlog in one burst. This unions `logged_classes` into
  `alerted_classes`, so the first live sweep posts **only** exceptions classified after it ran. It
  appends no row, posts nothing, spends no PP budget. `open` is left alone on purpose — the box
  keeps being polled, so a *different* class on it can still fire. Idempotent.
  🔴 **Run it AFTER the muted populate runs and BEFORE flipping `EXC_DRY_RUN` to false.**

The Wed–Sun day gate is unchanged and still enforced in both places (sweep + `excSlackPost_`):
Mon/Tue record to the tab and never alert.

## 🔴 The weekly PP budget starved the ping window — and the starvation was SILENT (2026-08-19)

> 🗑️ **SUPERSEDED BY P12 (2026-08-20) — HISTORY ONLY, DO NOT IMPLEMENT FROM THIS SECTION.**
> Every fix below is pacing arithmetic against a phantom 2,000/week. The *diagnosis* holds and is
> why this section is kept: **a run that polls ZERO is not an all-clear**, and a dead trigger and a
> starved run were indistinguishable from outside. Those two lessons are load-bearing and survive.
> The *cure* — pacing, floors, a per-run cap, a weekly ledger — was treating a symptom of a limit
> nobody had measured. Sections 2 (zero-is-not-an-all-clear) and 3 (heartbeat) remain LIVE;
> section 1 (pace the budget) is dead.

Kurt: *"I need the exceptions to work."* It wasn't. Measured, not inferred:

| evidence | value |
|---|---|
| `Exceptions` tab | **3 rows**, newest `2026-08-12 01:46` |
| `_exc_state` | 9,333 rows · **1,537 open** · **1,451 of them in the live `_SHIP_2026-08-17` cohort NEVER polled** |
| newest `last_seen` anywhere | **`2026-08-16 21:46`** (88 orders). 8/17, 8/18, 8/19 — nothing |
| `#exceptions` Slack | silent since **2026-08-07**; no pings AND no `:rotating_light:` failures |
| Google failure digests | name `refresh` and `refreshCurrentColumn` — **never `hourlyExceptionSweep`** |
| ParcelPanel API | healthy when queried directly 8/19 |
| `alerted_classes` populated | **3 orders** — so `excMarkRecordedAsAlerted()` did **not** over-suppress |

**The classifier was never the fault.** Replayed against the real live PP payloads, as of 8/16:
**#170893 → `ADDRESS_ISSUE`, ping, event `2026-08-14 19:53`** (found *under* the later benign
"At local FedEx facility" scan — the 8/17 checkpoint-window fix working) and **#169174 →
`DELAYED`, ping**. Both boxes have since delivered (8/17 17:55 and 8/18 11:51) and now correctly
classify `DELIVERED`/suppress. They never pinged because **nothing polled them after the fix
deployed**, not because they failed to classify.

### 1. Pace the budget — the ping days were paying for the silent days

`EXC_PP_WEEKLY_BUDGET` 2,000 ÷ 168 hourly runs ≈ **12 calls/run**, but `EXC_PP_MAX_PER_RUN` was
**120** — so ~17 consecutive runs could drain the entire week. From `_exc_state.last_seen`, week 33:
**Mon 8/11 polled 1,676 · Tue 8/12 polled 651 · Wed 8/13, Thu 8/14, Fri 8/15 polled ZERO.**
Mon/Tue are exactly the days the day gate FORBIDS posting on. The two record-only days ate the
whole allowance and the entire Wed–Sun ping window ran with nothing left.

🔴 **A per-run cap is not a budget.** `excBudgetTake_` now takes at most its fair share of what
REMAINS, spread over the runs still left in the week:
`take = min(want, left, EXC_PP_MAX_PER_RUN, max(EXC_PP_MIN_PER_RUN, ceil(left / runsLeftThisWeek)))`,
with `EXC_PP_MIN_PER_RUN = 10` (10 × 168 = 1,680 < 2,000, so the floor cannot itself drain the week)
and `excRunsLeftThisWeek_()` counting hours to Sunday-midnight ET, because the week key rolls Monday.

⚠️ **OPEN KURT DECISION, not fixed here:** 2,000 calls/week cannot poll a ~1,500-box open set
hourly at all — pacing guarantees the ping window is *fed*, it does not make the set *fresh*.
Raising the ParcelPanel plan, or widening `excResolveDelivered_`'s free Shopify narrowing, is the
only way to poll a live cohort quickly. Do not silently raise `EXC_PP_WEEKLY_BUDGET` past the plan.

### 2. A run that polls ZERO is not an all-clear

Constraint 7 says a PP *outage* must not read as "no exceptions". This was the same hole one step
earlier: with `take = 0` the sweep fetched nothing, classified nothing, threw nothing and posted
nothing — **identical in every observable way to a clean week**. That is why three days of total
silence produced no error anywhere. The sweep now posts a rate-limited health warning
(`excSlackHealth_`, at most one per 6h) whenever it polls 0 boxes while any are open, naming how
many are open, how many were never polled, and what the budget allowed.

🔴 **`excSlackHealth_` deliberately IGNORES the Wed–Sun day gate** (it still honours
`EXC_DRY_RUN`). Mon/Tue silence exists to spare Dan customer noise; it must never hide a broken
sweep. It is rate-limited precisely so the alarm cannot become the noise that gets the channel muted.

### 3. Heartbeat — "is the trigger even firing?" must be answerable without a sweep

A dead trigger and a zero-budget run were indistinguishable from outside. `hourlyExceptionSweep`
now stamps `EXC_LAST_RUN_AT` **before anything that can throw**, and the *Check properties* menu
item prints the heartbeat, the `PP_WEEK_USED` counter, runs left this week, and whether today is a
ping day. Stale heartbeat ⇒ the trigger is gone (`installExceptionsTrigger()`); fresh heartbeat ⇒
the fault is downstream. The Apps Script REST API cannot list triggers, so without this stamp the
question has no answer short of opening the editor.

## 🔴 PARCELPANEL DIRECTIVE P1–P13 — SSOT for every PP call

> 🔴 **READ P12 FIRST. IT DELETES THE PREMISE THE REST WAS BUILT ON.** P1–P11 rationed ParcelPanel
> calls against **"2,500/week"** — a number that is Kurt's average weekly **ORDER count**
> (10,000/month plan ÷ 4), never an API request budget. The real limit is **120 requests per
> MINUTE**, ~1.2M/week, and it is in `x-ratelimit-limit` on every response. **P12 replaces the whole
> budget layer with a rate limiter; P13 forbids dropping a 429.** The directives below are kept only
> as tombstones, so nobody re-derives the fence from the burns that motivated it.

| directive | status |
|---|---|
| **P1** charge/refund | 🗑️ **DELETED** — there was nothing to charge |
| **P2** week key `excWeekKey_` | 🗑️ **DELETED** — there is no week |
| **P3** one accountant | ♻️ **REWRITTEN** — per-consumer call counts survive as a *health/rate metric*, never a balance |
| **P4** a crash is not a ping | ✅ **KEPT**, unchanged |
| **P5** poll policy (a)(b)(c) | ✅ **KEPT**, re-justified below — none of the three was ever about budget |
| **P6** conditional pace floor | 🗑️ **DELETED** — nothing to pace against |
| **P7** week-34 counter reset | 🗑️ **DELETED** with the counter |
| **P8** two classes, two channels | ✅ **KEPT**, unchanged |
| **P9** dead record ≠ outage | ✅ **KEPT**, unchanged |
| **P10** cohort scope | ✅ **KEPT**, unchanged |
| **P11** the report starved the sweep | ♻️ **F1 KEPT** (waste is waste); F2/F3/F4 rewritten — see below |
| **P12** coverage is never rationed | 🆕 **THE RULE** |
| **P13** no consumer may drop a 429 | 🆕 **THE RULE** |

🔴 **THE FINAL, HONEST READING OF THE LEDGER, RECORDED BEFORE DELETION** (from the alarm that ended
it, 2026-08-20 — the only place this number survives, since Script Properties are not readable over
the Apps Script REST API):

```
ledger WK2026-08-17  total 4382/2500  (exc 225, rpt 4141, pa 16)
binding leg: the ACCOUNT quota
🔴 rpt 4141 OVER its 200/wk allocation (Code.gs ppLookup_ — the reship report)
ParcelPanel budget allowed 0 of 73 due
```

**Read it against the truth: 4,382 calls is 0.4% of the ~1.2M/week the account can actually serve.**
Every one of those legs was measured honestly and against the wrong denominator. 🔴 Note what the
ledger *did* get right and what survives it: `rpt 4141` really was a wasteful consumer — F1's cache
stays, because **re-buying a fact that cannot change is waste at any price**, and that argument
never depended on the budget. What dies is the conclusion that the waste had to *starve* something.

⏳ **Still interim.** A ParcelPanel → DigitalOcean webhook replaces polling outright (with a slow
daily reconciliation poll as the permanent backstop). P12's limiter is what polling looks like until
then; retire it with the polling, not before.

### P1 — 🗑️ DELETED (was: CHARGE FOR WHAT WAS SERVED, REFUND WHAT WAS NOT)

**Deleted by P12.** `excBudgetTake_` / `excBudgetSettle_` and the reserve-then-settle contract are
gone, because there was never a balance to reserve from.

🔴 **The observation underneath it was real and is preserved in P13**: a request ParcelPanel
*refused* (429/503) is categorically different from one it *served*, and fusing them produces a
number every downstream decision is then computed from. P1 used that distinction to keep an
accounting fiction honest; P13 uses it for the thing it was always actually good for — **a refused
request means retry, and must never be recorded as an answer.**

### P2 — 🗑️ DELETED (was: ONE WEEK BOUNDARY, Monday 00:00 ET → Sunday 24:00 ET)

**Deleted by P12.** `excWeekKey_` existed only to bound a weekly budget. There is no weekly budget,
so there is no week.

🔴 **Do not reintroduce a week key for anything.** The bug it fixed was real and subtle — Java's
`ww` is *locale* week-of-year and the US locale starts weeks on **Sunday**, while the pacer counted
from **Monday**, so a Sunday run could legally spend the entire following week's allowance. If any
future code ever needs a week boundary for a genuinely weekly fact, it must build it from an
ET-calendar anchor at UTC noon (so a DST transition cannot shift it a day) — **and it must not be a
budget.**

### P3 — ♻️ REWRITTEN: CALL COUNTS ARE A HEALTH METRIC, NEVER A BALANCE

**What is deleted:** the ledger-as-budget. `PP_WEEK_USED`, `EXC_PP_ACCOUNT_QUOTA`,
`EXC_PP_WEEKLY_BUDGET`, `EXC_PP_RPT_ALLOC`, `EXC_PP_PA_ALLOC`, `excBudgetRead_/Write_/Take_/
Settle_/LeftAccount_/BindingLeg_` and the "who yields" hierarchy. No consumer yields to another,
because there is nothing to yield.

**What survives, and why it is worth keeping:** knowing *how many calls each consumer makes* is
genuinely useful — it is how `rpt`'s 31.9× waste was found in the first place. So the counters stay,
with three changes that make them impossible to read as a fence:

1. **Daily, not weekly** — `PP_CALLS_TODAY` = `"<yyyy-MM-dd>|<total>|<exc>|<rpt>|<pa>"`. A daily
   count is a **rate**; a weekly count against a cap invites subtraction.
2. **Nothing reads it to decide anything.** 🔴 There is no code path anywhere that gates, paces,
   caps or skips based on this property. If one ever appears, that is the regression this directive
   exists to prevent. The only thing that shapes traffic is the rate limiter and
   `x-ratelimit-remaining`.
3. **Renamed at every call site** — `excBudgetCharge_` → `excPpRecordCalls_`. The word *budget* is
   removed from the ParcelPanel vocabulary of this project on purpose: a helper called
   `excBudgetCharge_` reads as correct at a call site that is about to ration something.

🔴 **P3's original complaint remains valid and is now answered properly.** It said the counter was a
**lower bound** because `sync_delivery_status.py` and ad-hoc probes run in other runtimes and cannot
reach these Script Properties. That is still true of `PP_CALLS_TODAY` and always will be — which is
exactly why it is no longer allowed to decide anything. **The cross-runtime coordination that P3
wanted, and could not have, is `x-ratelimit-remaining`:** every runtime sharing the API key sees the
same bucket drain, exactly, in every response. The fix for "my counter cannot see the other
consumers" was never a better counter.

### P4 — A CRASH IS NOT A PING: failure alerts bypass the Wed–Sun gate

`hourlyExceptionSweep`'s catch block posted through `excSlackPost_`, which applies the day gate — so
**every Mon/Tue sweep failure posted absolutely nothing**, and Mon 8/17 / Tue 8/18 are exactly those
days. It now posts through `excSlackHealth_`. The day gate exists to spare Dan customer noise, never
to hide a broken sweep. 🔴 **Exception PINGS keep the gate; health and failure alerts never get it.**

### P5 — POLL POLICY (a)+(b)+(c), Kurt-approved

- **(a) Shopify triages, ParcelPanel confirms.** `excResolveDelivered_` already fetches fulfillment
  `displayStatus` + events for the whole triage window on a request that was being made anyway —
  free against this quota. ParcelPanel is asked **only** about the ambiguous remainder:
  `DELAYED`, `ATTEMPTED_DELIVERY`, or **no movement scan at all** (`excShopifyFlagged_`).
- **(b) At most once per calendar day per box, longest-unpolled first.** 🔴 **KEPT under P12, and
  explicitly DE-LINKED from budget:** this is *work avoidance*, not rationing. Every ping class has
  a ≥3-day evidence floor, so a second poll of the same box on the same day cannot produce a case
  the first one missed. It is also what makes any cadence safe — daily work is bounded by the number
  of open boxes, never by the number of runs.
- **(c) Cohort age ≥ 3 days**, OR Shopify-flagged. Measured on mature cohorts (07-20, 07-27, 08-03,
  08-10): **94–99% of a cohort is still legitimately in transit on day +1/+2, and no ping class can
  fire then** (`EXC_NEVER_PICKED_MIN_DAYS = 3`, `EXC_DELAYED_MIN_DAYS = 3`). Polling the day-1/-2
  mass is where the whole budget went and it can produce nothing.
- 🔴 **But NOT zero on Mon/Tue.** `EXC_CP_SCAN = 5`: a Monday exception buried under more than five
  routine facility scans by Wednesday is invisible forever — the exact `#170893` failure mode
  `112ba5a` was written to fix. Mon/Tue keep a **thin sweep — Shopify-flagged only, and COMPLETE.**
  🔴 **`EXC_THIN_DAY_CAP = 100` is DELETED (P12).** The *shape* of the thin sweep is work avoidance
  (a box Shopify shows moving normally is not worth asking about); the *cap* was rationing, and a
  Monday with 140 flagged boxes silently left 40 unchecked going into the ping window. Flagged-only
  is the filter. There is no ceiling on it.
- 🔴 **An empty batch is now often CORRECT**, so the starvation alarm splits into two shapes it must
  never confuse: **STARVED** (work was due, budget allowed none) and **BLINDSPOT** (nothing judged
  due while open boxes have never been polled once — a policy bug, and the week-34 signature).
- 🔴 **Log actual spend every run:** `reserved / served / refunded / answered` plus the ledger.

**Projected weekly spend — 🔴 SUPERSEDED BY P12, kept because the SURVIVAL CURVE it measures is
still the reason (a) is right.** The "fits / does not fit" verdicts below were computed against
the phantom 2,000/wk and mean nothing; (b) alone at 10,500–16,100 calls/wk is ~1% of what the
account serves. What survives is the measurement itself: **94–99% of a cohort is legitimately in
transit on day +1/+2 and no ping class can fire then**, so polling that mass is work that cannot
produce an answer. That is work avoidance, not rationing, and it stays.

**The original projection, as written:**

| leg | calls/wk |
|---|---|
| current cohort, day +3…+7, 1×/day (201/198/280/169 measured across four cohorts) | 170–280 |
| previous cohort, day +8…+14, 1×/day (`EXC_COHORTS_BACK = 2`; ~18–40/day) | 130–280 |
| Tuesday Dallas leg (~110 boxes) | ~30 |
| thin Mon/Tue sweep, 100/day × 2 | 200 |
| retry/throttle headroom @ 25% | ~200 |
| **TOTAL** | **≈ 730–990 of 2,000** |

Under P1 the headroom row is now conservative: throttled retries are **refunded**, so they no longer
consume the line they are budgeted for. Account-level, add `pa` ≈180/wk and `rpt` (uncapped, newly
visible for the first time) — the first honest `rpt` figure is itself a deliverable of this change.
🔴 **(b) alone does NOT fit** (1,500–2,300 open × 7 = 10,500–16,100); **(b)+(c) does not fit either**
(5 ping days × 1,500–2,300 = 7,500–11,500). Only **(a)** removes the day-1/-2 mass, and the survival
curve is what makes it cheap.

### P6 — 🗑️ DELETED (was: THE PACE FLOOR IS CONDITIONAL)

**Deleted by P12**, with `EXC_PP_MIN_PER_RUN`, `excRunsLeftThisWeek_` and the whole pacing
denominator. A floor and a ceiling on a budget that does not exist are both zero-valued.

🔴 **The reasoning was sound and is worth remembering in the abstract:** *an unconditional floor is
not a floor, it is a second budget* — 109 remaining runs × 10 = 1,090 calls that pacing is powerless
to prevent. Any future "minimum per run" anywhere in this project should be read that way first.

### P7 — 🗑️ DELETED (was: THE WEEK-34 COUNTER IS RESET TO ZERO)

**Deleted with the counter.** The manual lever `excResetWeeklyBudget` and its menu item *Reset
ParcelPanel weekly ledger* are removed — there is no ledger to reset.

🔴 **What P7 said that is still true, and now applies to a real number:** *"What is NOT known is
ParcelPanel's own quota state and its reset schedule."* It is known now, and it is not a mystery
requiring a proxy — `x-ratelimit-limit` and `x-ratelimit-remaining` arrive with every single
response. The property `PP_WEEK_USED` is now dead data on the project; 🔴 **Kurt should delete it by
hand** (Project Settings → Script Properties), since the Apps Script REST API cannot write
properties and a stale key invites someone to read it as live.

### P8 — TWO CLASSES, TWO CHANNELS, TWO HELPERS (Kurt 2026-08-19)

> Kurt, verbatim: **"you should message failures on the appyhour ops reader channel and not there"** —
> looking at `:rotating_light: exceptions sweep FAILED: Error: ParcelPanel fetch failing: 9/19 hard
> failures (throttled: 0) — results suppressed rather than reported as all-clear` sitting in
> **#exceptions** between two customer pings (posted 01:46 and 02:46 CST, 2026-08-20).

**The suppression itself was correct and does not change.** Refusing to report an all-clear when half
the fetches failed is constraint 7 working. **P8 changes the DESTINATION of the complaint and nothing
else** — not the threshold, not the suppression, not the message.

> 🔴 **Do not read a root cause into this directive.** P8 was written believing the 9/19 was a PP
> outage. It was not: a parallel session reproduced it as **9 CANCELLED Shopify orders that
> ParcelPanel legitimately 404s** — deterministic, the same nine every hour, forever. That is a
> permanently-bad-record class, and it is **P9's** subject, not P8's. The routing rule is
> independent of which of the two it was; the diagnosis here was wrong and is corrected there.

| class | what it is | destination | day gate | `EXC_DRY_RUN` |
|-------|-----------|-------------|----------|----------------|
| **PING** (`excSlackPost_`) | a customer's box has a problem — never picked up, address issue, damaged, delayed, attempt failed | **#exceptions** `C0BLKKPAW8P` (private; Kurt + Dan + bot) | **YES**, Wed–Sun | **YES**, suppressed |
| **OPS** (`excSlackOps_`) | the job telling on itself — sweep FAILED, PP hard-failure ratio, starved/blindspot alarm, budget bit, missing-tab warning, `PA_ASSERT_*` refusal | **#kurt-ops** `C0BT47XG8CW` (Kurt + the bot only; was the ops DM `U08R19137UL` → `D0BG1541F0A` until 2026-08-27) | **NO** | **NO** — still posts |

🔴 **Why the ops channel is that DM and not a new channel.** No channel named "ops reader" exists in
the workspace (searched 2026-08-19: only `#ops-strategy`, `#devops`, `#contara-appyhour`,
`#appyhour_grip` — none plausible). `appyhour-ops-reader` is the **bot** (`U0BG153RTNW`), and the
conversation Kurt sees under that name is its DM, `D0BG1541F0A`. That DM has been this project's ops
feed since 2026-07-13 — it already holds `Code.gs`'s "Reship report (Apps Script) FAILED" alerts and
the 8/19 missing-tab warning, opened by the message *"Reship report alerts will DM you here (private,
not #reships)."* Failures were routed to where the other failures already were, not somewhere new.
**No new Slack scope is needed:** `slack_` reaches it with this same `SLACK_BOT_TOKEN`.

🔴 **`EXC_DRY_RUN` NO LONGER MUTES A FAILURE ALERT.** The old `excSlackHealth_` was hard-blocked by
it, and the sweep's catch block additionally did `if (EXC_DRY_RUN) throw e;`. Both are gone. That
flag is the stop-write on **#exceptions**, justified entirely by "do not dump 4,289 customer
exceptions into the channel yet" — a statement about pings, which no longer share a destination with
failures. A crash *during* a muted period is precisely the one nobody would otherwise notice: the
sweep is silent by design, so broken and working look identical, and this post is the operator's only
signal. **A mute that also mutes the smoke alarm is the silent-failure class this file exists to
kill.** Blast radius of being wrong: one DM to Kurt. If a stop-write must ever cover ops alerts, it
gets its **own** flag — do not re-couple them.

🔴 **ONE HELPER PER CLASS — never one helper with a boolean.** `excSlackPost_(text, isFailure)`
would let a future call site put an infra crash in Dan's channel, or a real customer exception into a
DM only Kurt reads, by getting one argument wrong. Destination is a property of *which function you
call*; no argument changes it. Both funnel through `excSlackSend_(channel, text, what)`, the only
`chat.postMessage` caller in the file, which applies **no** gates of its own — so reading one class
helper tells you the whole truth about when that class posts.

🔴 **`EXC_CHANNEL` IS DELETED — do not reintroduce it.** A lone constant named "the channel" reads as
correct at every call site, which is exactly how the miscategorization happens. There is no "the"
channel any more: `excChannelPings_()` / `excChannelOps_()`.

**Re-routable without a code push.** Script Properties `EXC_CHANNEL_PINGS` / `EXC_CHANNEL_OPS`
override the literals. Deliberately **not** in `EXC_REQUIRED_PROPS` — unset is the normal state and
falls back to the literals; requiring them would turn "nobody set an override" into a preflight crash
that stops the sweep. 🔴 An unset **or empty** property falls back to today's behavior, never to
empty-string (which would post nowhere and lose the alert silently). Reason a property exists at all:
a push into this project takes the hourly reship report down with it if it lands wrong.

**`Code.gs` needed no re-pointing** — every `slack_` caller is already ops-class (FAILED run, empty
Raw Data, ghost-tab creation, breach warning) and already went to that DM. It now resolves its
destination through `excChannelOps_()` behind a `typeof` guard, so one property moves both files, and
a project that has lost `Exceptions.gs` (which happened 2026-08-14) still alerts. 🔴 **`Code.gs` has
no ping-class helper and must never get one** — it must have no way to reach #exceptions.

Static verification, given Apps Script cannot be executed from here: `scratchpad/p8_route_test.js`
loads `Exceptions.gs` into a stubbed context and asserts destination + gates per class — **9/9 PASS**
(ping Thu→#exceptions; ping Mon→suppressed; ping dry→suppressed; ops Mon→DM; ops dry→**still posts**;
legacy `excSlackHealth_` shim→DM; override wins; unset→literal; empty→literal).

🔴 **LIVE PROOF — recorded here because the signal that produced it no longer exists.** The hourly
sweep ran an unintended A/B across the deploy, and it is the only one that will ever occur:

| run | destination | message |
|-----|-------------|---------|
| 01:46 | **#exceptions** | `exceptions sweep FAILED: ParcelPanel fetch failing: 9/19 hard failures (throttled: 0)` |
| 02:46 | **#exceptions** | same, verbatim |
| *(P8 deployed — `Exceptions 0807a062da27 → 3d5a2a20e9a9`)* | | |
| 03:46 | **ops DM `D0BG1541F0A`** | same, verbatim — first exceptions-sweep message ever in that DM |

Three consecutive runs of one job on a known hourly cadence, byte-identical text, destination
flipping exactly at the push, with n=2 before the flip. Nothing else on that path changed.

Two independent discriminators prove it came from `excSlackOps_` and not a fallback:
- **Emoji fingerprint.** `Code.gs`'s `slack_` PREFIXES its own emoji, so anything routed through it
  shows a **doubled** `:rotating_light:` — all three Reship-report failures in that DM do, and the
  missing-tab warning reads `:rotating_light: :warning:`. The 03:46 sweep failure carries a **single**
  one. Only `excSlackOps_` passes the string through untouched, and only `Exceptions.gs`'s catch
  block produces that text.
- **It tripped the OLD fail-ratio guard.** P9 exists to stop that exact message, so a run still
  emitting it was running post-P8, pre-P9 — the one window where both facts hold.

🔴 **Do not re-derive this from timestamps.** The Slack MCP renders those stamps as `2026-08-20 CST`
against a session date of 08-19 — the absolute times are unusable and read as *preceding* the push.
**The ORDERING across two channels carries the claim, not the clock.**

🔴 **What this does NOT prove, and what killed the signal.** P9 (next section) makes the 9/19 stop
firing, so **there is no next FAILED message** — a quiet DM is now evidence for P9, not for P8.
This paragraph is the whole live record. Still unproven: (a) the **PING** side resolving —
`excStatus_` prints both destinations and flags override-vs-literal, read-only, no trigger needed,
and is the fastest way to check it; (b) P9's own quarantine `excSlackOps_` caller, which stays cold
until a future dead record trips the 3-run counter. **Three claims, one evidenced.**

### Still open (Kurt decisions, not fixed here)

- **A.** `DELIVERY_SYNC` on the cloud App Platform env — 0 or 1? If 1, P3's ledger cannot see the
  dominant drain.
- **B.** Re-consent clasp with `script.processes` so execution history (duration / `TIMED_OUT` /
  throttle rate) is readable. Until then, **whether throttling or the 6-minute kill burned the
  1,912 is UNVERIFIED** — P1 fixes both, which is why the deploy did not wait on it.
- **C.** 2,000/wk still cannot poll a ~1,500-box open set hourly. Pacing feeds the ping window; it
  does not make the set fresh. 🔴 **Do not silently raise `EXC_PP_WEEKLY_BUDGET` past the plan.**

### P9 — A DEAD RECORD IS NOT AN OUTAGE, AND A CANCELLED ORDER IS NOT A BOX (2026-08-19)

> The evidence: `:rotating_light: exceptions sweep FAILED: ParcelPanel fetch failing: 9/19 hard
> failures (throttled: 0)` — **the identical ratio, two hours running**. Reproduced from python at
> 15:5x the same day: the same 19, the same 9 failures, byte-for-byte.

🔴 **This was never a ParcelPanel outage.** P8 was written on that assumption and says so; the
assumption was wrong. All nine failures are HTTP **404** with body
`{"errors":"Order (order_number = X) not found"}`, and all nine orders are **cancelled in Shopify**:

| order | cohort | cancelled | Shopify fulfillments |
|-------|--------|-----------|----------------------|
| 173555 | `_SHIP_2026-08-17` | 2026-08-18 06:42 | 0 (UNFULFILLED) |
| 173559 | `_SHIP_2026-08-17` | 2026-08-18 06:43 | 0 (UNFULFILLED) |
| 173560 | `_SHIP_2026-08-17` | 2026-08-18 15:08 | 0 (UNFULFILLED) |
| 173562 | `_SHIP_2026-08-17` | 2026-08-18 06:45 | 0 (UNFULFILLED) |
| 173596 | `_SHIP_2026-08-17` | 2026-08-18 18:12 | 0 (UNFULFILLED) |
| 173632 | `_SHIP_2026-08-17` | 2026-08-17 16:30 | 0 (UNFULFILLED) |
| 174409 | `_SHIP_2026-08-17` | 2026-08-17 15:05 | 0 (UNFULFILLED) |
| 174413 | `_SHIP_2026-08-17` | 2026-08-18 16:06 | 0 (UNFULFILLED) |
| 171813 | `_SHIP_2026-08-10` | 2026-08-10 17:28 | 0 (UNFULFILLED) |

**The exclusion is proven, not assumed.** A cross-check of every one of the 1,201 open `_exc_state`
rows against Shopify `cancelledAt` returns **exactly 9 cancelled orders — the same nine**. The set
that fails and the set that is cancelled are identical, so no real failure is hiding behind the
exclusion. The discriminator is an independent fact (Shopify's cancellation), never "it failed and
excluding it makes the number look better." (`~/.claude/.../exclusion-that-flatters-needs-proof`.)

#### The starvation loop, confirmed against the deployed code

1. `excSeedCohort_` filters `-status:cancelled` **at seed time only**. An order cancelled *after*
   seeding keeps `open = 1` forever; nothing re-checks it.
2. `excResolveDelivered_` closed only **DELIVERED**. Cancelled orders sailed through it.
3. They have no movement scan, so `excShopifyFlagged_` is true → permanently eligible regardless of
   the cohort age gate.
4. PP 404s → old `consume()` did `out.failed++` and **never added the order to `out.seen`**.
5. Unseen ⇒ `last_seen` never stamped ⇒ `excPollSet_`'s "never-polled sorts first" pins them to the
   **head of the queue**, re-selected every single run.
6. 🔴 **And the throw was ABOVE `excSaveState_`.** A tripped guard discarded the *entire* run's
   state — including the `last_seen` of the ten boxes that answered perfectly. Nothing could ever
   change, so the next run was bit-identical. **The sweep was not degraded, it was 100% dead**, and
   every hour it also burned 19 real ParcelPanel calls to stay that way.

🔴 **Pacing is what made it fatal.** Nine dead records out of a 120-call run is 7.5% and trips
nothing; out of the paced 19-call run it is 47%. P5's pacing was correct — it just moved a
pre-existing poison from below the threshold to above it. **A ratio guard whose numerator can be a
fixed set of permanently-bad rows becomes more likely to fire as the batch gets smaller.**

#### The rules

1. **A CANCELLED ORDER IS CLOSED, FOR FREE, IN THE SHOPIFY TRIAGE.** `excResolveDelivered_` now
   selects `cancelledAt` and closes on it exactly as it closes on DELIVERED. Zero extra ParcelPanel
   cost — it rides a request already being made. A cancelled order is not a box: it is never an
   exception, never polled, and never alerted on.
2. **THREE OUTCOMES, NOT TWO.** `excPpFetch_` returns `dead` alongside `failed`:
   - **TRANSPORT** (`failed`) — 5xx, a `fetchAll` throw, a 200 whose body will not parse. The
     request broke. Retry is right; a high rate means PP is down → **suppress the run**.
   - **DEAD RECORD** (`dead`, per-order) — **404/410**. PP answered definitively that it has no such
     order. Retrying returns the identical answer forever.
   - **THROTTLED** — 429/503, unchanged: refused backpressure, not billable, retried next run.
3. **A DEAD RECORD IS STAMPED `last_seen`.** 404 *is* an answer. Stamping it is what lets the box
   leave the head of the longest-unpolled queue; leaving it unstamped is the whole bug. It then
   costs at most one call per day under the once-per-day tier instead of one per run.
4. **QUARANTINE AFTER `EXC_PP_DEAD_QUARANTINE = 3` CONSECUTIVE DEAD ANSWERS.** The counter
   (`_exc_state.pp_dead_runs`, the tab's 10th column) **persists**, and **any real answer resets it
   to 0** — so only a permanent condition ever reaches it. Three, not one: a single 404 could be
   sync lag on a fresh order, and closing a real box on one bad answer is the wk0803 failure. Three,
   not ten: at ~1 call/day/box, ten is a fortnight of a dead record in the queue.
5. **THE RATIO IS TRANSPORT-ONLY, AND THE SPLIT IS EVIDENCE-BASED.**
   `transportFails / transportDenom > EXC_PP_FAIL_RATIO`, where
   `transportFails = failed + deadRegression` and `transportDenom = attempted − deadUnknown`.
   - **deadRegression** — the order has a prior `last_seen`. PP *answered it before* and now denies
     it. Something broke → it counts as a transport failure, both sides of the ratio.
   - **deadUnknown** — PP has never once acknowledged it. Excluded from **both** sides, so it
     neither inflates the numerator nor dilutes the denominator.
   🔴 An unknown-record 404 leaving the denominator is deliberate: a run of 19 that is 18 dead
   records and 1 transport failure is 1/1 = 100% and **still suppresses**. The exclusion cannot be
   used to hide a real outage behind a pile of dead rows.
6. 🔴 **A SUPPRESSED RUN STILL SAVES WHAT IT LEARNED.** `excSaveState_` runs **before** the guard
   throws. The `pp_dead` counters and stamps are the only mechanism that can retire a permanent
   failure; discarding them on the way out is precisely what made the failure permanent.

#### Where a quarantined box is visible — this is the wk0803 class

A quarantined box is **an undelivered box we have stopped checking**. It is surfaced twice, and
**once each — never hourly**, because an alarm that repeats is an alarm that gets muted:

- a row on the **`Exceptions` tab**, class `PP_NO_RECORD`, labelled
  *"NOT BEING CHECKED — ParcelPanel has no record of this order"*, deduped through `logged_classes`;
- one **ops message** (`excSlackOps_`, per P8 → #kurt-ops since 2026-08-27) naming every order
  quarantined that run and saying plainly that if any is a real box it is now unmonitored.

The cancelled-order close in rule 1 is **not** a quarantine and gets neither — a cancelled order was
never a box, so announcing it would be noise. Only the count is logged.

🔴 **THE ROW IS WRITTEN FIRST; THE BOX IS CLOSED ONLY IF THE ROW LANDED.** `excLog_` does sheet I/O
and can throw. If it throws, the order is left **OPEN and still polled**, unlogged, to retry next
run — it is never retired invisibly. Closing a box we then failed to surface is strictly worse than
not quarantining it: that is the wk0803 shape exactly, an undelivered box nobody is checking and
nobody knows about. The `try/catch` around that write is also load-bearing for a second reason —
unguarded, it sits **above** `excSaveState_`, so one transient Sheets error would kill the sweep AND
discard the `pp_dead` increment that had just been made, rendering the dead record permanent all
over again. That is rule 6's bug reintroduced one function further down, and it was caught in review
of this very change. **Any new throw added between the fetch and the save must be guarded.**

#### Verification (2026-08-19)

- `scratchpad/p9_dead_test.js` — **24/24 PASS**. Drives the real `excPpFetch_` from `Exceptions.gs`
  against the **real captured ParcelPanel bodies** for all 19 orders (full bytes, not truncated —
  the first fixture truncated the 200s at 400 chars and `JSON.parse` threw, which the harness
  correctly reported as 10 transport failures; a synthetic `{code:404}` fixture would have passed
  while proving nothing). Asserts: the 9 dead are identified; `failed = 0`; 10 answered; the guard
  **does not** trip (1/11 = 0.09); a genuine 19/19 5xx outage **does** trip; quarantine fires on run
  3 and logs once; a real answer resets the counter; the schema round-trips 10 columns.
- `scratchpad/gas_lint.js` — SYNTAX OK on all four files, COLLISIONS: NONE.
- ParcelPanel calls spent proving this: **29 metered** (19 poll-set probe + 10 full-body re-fetch),
  plus 19 earlier that Cloudflare rejected 403 before reaching the API. **Local python probes do not
  report into `PP_WEEK_USED`** (P3) — this spend is invisible to the ledger and is recorded here
  instead.

#### Still open

- **D.** Order `164878A` sits in `_exc_state` — the only non-numeric order value among 9,333 rows.
  Not investigated; it is not in the failing set. If PP 404s it, P9 quarantines it in 3 days.
- **E.** The cancelled-order close is a **poll-time** repair, not a backfill. The 9 close on the
  next sweep; older cancelled rows already `open=0` are untouched.

### P10 — THE SWEEP IS SCOPED TO THE CURRENT COHORT AND THE PREVIOUS ONE (Kurt GO, 2026-08-19)

> Kurt, on being handed `excSeedBacklogAsLogged` as the fix for stale re-appends: **"why do we need
> to seed it? can't we just look for the latest current cohort?"** — then, on current + previous:
> **"do it."**

**The problem it deletes.** `_exc_state` holds **9,333 rows** going back to July. The false-positive
purge cleared `logged_classes` so genuinely-new exceptions could record again — and the first
working sweep therefore re-appended wk0727/wk0803 exceptions stamped `detected 2026-08-19` against
an `event when 2026-07-31`. The workaround was `excSeedBacklogAsLogged`, and it is a bad one: its
last run **seeded 9, polled 16, and left 476 never polled**, and it permanently silences REAL open
exceptions along with stale ones. Scoping the CANDIDATE SET makes the whole class disappear —
an old box is no longer a candidate, so there is nothing to seed.

#### The rule

**The sweep considers only orders whose cohort is the CURRENT `_SHIP_` cohort or the PREVIOUS one.**
Everything older is out of scope: **not polled, not classified, not appended, not alerted.**

- **The scope is a set-membership test on the cohort tag and nothing else — `cohort IN (current,
  previous)`.** It is written as an in-memory filter today only because the state lives in a Sheet;
  when the sweep reads `delivery_status` / `pp_webhook_events` out of MySQL it is the identical SQL
  `WHERE` clause with no change of meaning. 🔴 Do not re-express it as a date-arithmetic predicate on
  `last_seen`, `detected`, or a row age — those are not the same set and do not survive the cutover.
- **The current cohort is DERIVED, never hardcoded:** walk back Mondays (ET) to the first `_SHIP_`
  tag that has any non-cancelled order, 3-week lookback. It rolls forward on its own.
  🔴 **MIRRORED from `PivotAnalytics.paCurrentShipWeek_`, not called.** Same definition, one
  `orders(first:1)` Shopify probe (free against the ParcelPanel quota, normally exactly one call).
  Calling across files was rejected: `Exceptions.gs` already depends on `Code.gs shopifyGql_`, and
  `PivotAnalytics.gs` was **deleted from this project once** (2026-08-14) — a second cross-file
  dependency would take the sweep down with it. If the two definitions ever diverge, this is the
  known cost of that choice and it is deliberate.
- 🔴 **THE WINDOW RATCHETS FORWARD AND CAN NEVER SLIDE BACK.** Without this, one Shopify probe
  returning zero for the live tag (outage, or a cohort not yet tagged) walks back a week, pulls a
  RETIRED cohort back into scope, and re-appends exactly the stale rows this directive exists to
  stop. The newest tag ever in scope is persisted in `EXC_SCOPE_CURRENT`; a computed window older
  than it is clamped and the clamp is logged. **This is what makes "an out-of-scope row never
  silently reappears" a property of the code rather than a hope.**
- **Multi-leg weeks are handled for free.** The Monday and Tuesday(Dallas) legs **share one `_SHIP_`
  tag**, so scoping by tag keeps both legs of a week together and cannot split them. Verified in
  `p10_scope_test.js` (membership is on the tag, never on a ship DATE).
- **No cohort found and no stored ratchet ⇒ THROW**, which the sweep's catch turns into an ops-DM
  alert. An empty scope must never be a silent all-clear — that is constraint 7 one level up.

#### 🔴 WHY TWO COHORTS AND NOT ONE — Kurt's reasoning, kept

wk0803's never-collected tote sat **7–33 days** before anyone noticed. **A one-cohort window would
have missed it.** Two cohorts ≈ up to ~14 days of coverage, which is the shortest window that still
catches the burn this alerter was built for.

#### 🔴 WHAT THIS GIVES UP — the accepted tradeoff, stated plainly

**An exception on a box older than two cohorts will NEVER be surfaced by this job.** Not delayed —
never. If a box from three weeks ago is damaged, returned, or was never collected, this alerter is
silent about it forever. **Kurt's decision, 2026-08-19, made with that consequence stated.** The
mitigation is that two cohorts covers the 7–33 day window the real wk0803 case needed, and that a
box still undelivered after 14 days has other surfaces (the reship report, CS tickets, the
postmortem). It is not covered here.

#### What happens to out-of-scope rows: LEFT OPEN AND IGNORED, never closed

They keep `open = 1` and are skipped at the top of the sweep. **They are NOT closed**, because
`open = 0` in this schema means delivered / cancelled / alerted — a box whose story ended — and
flipping 41 undelivered boxes to `0` would launder *"we stopped looking"* into *"it was fine"*.
That is the wk0803 shape, and it would also destroy the evidence that we stopped.

They cannot come back: the forward-only ratchet above is the guarantee. And the count is **logged
every single run**, with a per-cohort breakdown, so a shrinking window is visible rather than
implicit:

```
scope: 478 open in _SHIP_2026-08-17 + _SHIP_2026-08-10; 41 open row(s) IGNORED as out of scope
       (_SHIP_2026-08-03:29, _SHIP_2026-07-27:12). 🔴 An exception on a box older than 2 cohorts
       is never surfaced — accepted tradeoff, Kurt 2026-08-19.
```

🔴 **Why they get no tab row and no Slack message, unlike a P9 quarantine.** A quarantine is
*unexpected* — a specific box ParcelPanel permanently denies — so it is surfaced once, per box.
Scope expiry is *expected, bulk, and by policy*: ~40 rows every week, forever. A row per box would
turn the tab into the noise that gets the channel muted, which is the failure mode this whole file
defends against. The per-run log line plus this section IS the visibility.

#### The blindspot alarm had to be sharpened, or scoping would have made it cry wolf

Found while measuring this change: the BLINDSPOT predicate (`nothing due while open boxes have
never been polled once`) counted **every** never-polled box. With the candidate set scoped, a
Tuesday run legitimately has **0 due and ~416 in-scope never-polled** — the live cohort is 2 days
old, the age gate skips it ON PURPOSE, and no ping class can fire that early anyway (both floors
are 3 days). The raw counter would have posted a "policy bug" ops alert every 6 hours forever.

🔴 **The numerator is now never-polled AND past `EXC_POLL_MIN_AGE_DAYS`** (`excNeverPolledDue_`).
Both numbers are still printed in the alert and the log, so the sharpening is visible and cannot be
mistaken for the alarm being quietly weakened.

#### Measured before the push (live `_exc_state` + Shopify, **zero ParcelPanel calls**)

`scratchpad/scope_measure.py`, 2026-08-19, in-scope window `_SHIP_2026-08-17 + _SHIP_2026-08-10`:

| | value |
|---|---|
| `_exc_state` rows | 9,333 |
| open rows | **519** |
| open **IN** scope | **478** (08-17: 460 · 08-10: 18) |
| open **OUT** of scope | **41** (08-03: 29 · 07-27: 12) |
| poll set today, OLD rule | **27** |
| poll set today, NEW rule | **0** |
| of the 27 the old rule would have polled today, how many were out of scope | **27 — every one** |

🔴 **Every box the sweep would have called ParcelPanel about today was from a retired cohort.** That
is the re-append machine measured directly, not argued.

🔴 **CORRECTION to the premise this change was requested on.** The never-polled backlog is **NOT**
out of scope. All **416** never-polled open boxes are in `_SHIP_2026-08-17` — the **live** cohort.
They are never-polled because the cohort is 2 days old and the age gate skips it, not because they
are stale. Scoping does not retire them; it protects them. From Wed (day +3) the in-scope due set
is ~470 for that cohort, which the weekly budget still cannot poll in one day — **that is Kurt
decision C (2,000/wk cannot make a live cohort fresh), unchanged by this directive** and now
answered by the webhook rather than by pacing.

#### `excSeedBacklogAsLogged` — KEPT, no longer needed for backlog control

It is not deleted. Backlog control is now structural, so the seeder has no routine job; it stays as
a **manual mute lever** for the one case scoping does not cover — a genuine flood *inside* the live
window that Kurt wants recorded-not-pinged. Do not schedule it, and **do not delete it without
saying so**: removing a lever is a decision, not cleanup.

#### What becomes dead when the ParcelPanel webhook lands

The webhook (`POST /webhooks/parcelpanel` on the DigitalOcean console, events landing in
`pp_webhook_events`) is live and a deriver into `delivery_status` is being built. When the sweep
reads that table instead of calling PP:

- **Dead:** `excPpFetch_`'s HTTP layer, the whole `PP_WEEK_USED` ledger (P1/P2/P3/P6/P7), pacing
  (`excBudgetTake_` / `excBudgetSettle_` / `excRunsLeftThisWeek_`), the thin Mon/Tue day cap, and
  the once-per-day poll tier — all of them exist only to ration a metered GET.
- **SURVIVES, and is the reason this directive is written as a predicate:** the scope rule itself
  (`cohort IN (current, previous)` becomes the SQL WHERE clause), the classifier and every phrasing
  it learned, the day gate, the two-channel split, the dedup on (order, class), and the P9
  quarantine idea (a record the source has no answer for).
- 🔴 **A webhook can never feed the classifier by itself** — the payload carries **no `checkpoints`
  array**, the exception topics miss **38%** of real failures, and `substatus` is unusable
  (`Exception_007` alone spans 22 delivered / 4 returned / 3 undeliverable / 1 lost). And two of our
  classes are **absences, not events** — "never picked up" and "no scan in 24h+" — so no webhook
  will ever fire for them; they stay derived queries over the scoped set.

#### Verification

- `scratchpad/p10_scope_test.js` — drives the REAL `Exceptions.gs` in a stubbed Apps Script context:
  pure window (incl. a 7-day step across the **Nov 2 DST** boundary), the ratchet in all four
  directions, live-shaped derivation (one probe), walk-back when the live tag is untagged,
  **a bad probe failing to slide the window back**, empty scope throwing, tag membership
  (multi-leg), the candidate filter itself, and both blindspot numerators. **ALL PASS.**
  Collision check re-run against the **CURRENT LIVE bytes** of `Code` / `PivotAnalytics` /
  `Notifications` (another session is pushing `Notifications.gs`): **NONE**.
- `excSelfTest()` gains 10 assertions for the same pure helpers, so the check also exists inside the
  project where a human can run it after an edit.
- `scratchpad/p9_dead_test.js` **31/31 PASS**, `scratchpad/p8_route_test.js` **9/9 PASS**,
  `scratchpad/gas_lint.js` SYNTAX OK ×4 + concat, COLLISIONS NONE — all unchanged by this edit.
- 🔴 **NOT verified: any live execution.** Apps Script cannot be run from here; the sweep's own
  numbers arrive on Kurt's next hourly run.

### P11 — THE SWEEP IS STARVED BY THE REPORT, NOT BY ITS OWN BUDGET (2026-08-20)

> Kurt's ops DM, twice in seven hours: *"exceptions sweep polled 0 boxes while 271 are still open…
> ParcelPanel budget allowed 0 of 271 due"* / *"…allowed 0 of 261 due."*

🔴 **STATUS: root cause PROVEN. Kurt GO 2026-08-20 on F1–F4 — all four DEPLOYED.**
`Code 88b44f93ce14 → 810e02c41d4f` (F1), `Exceptions 22d8ae6ceefe → 7d0dd0edfba0` (F2/F3/F4).
🔴 **F3 is NOT fully live until Kurt edits the triggers by hand** — the Apps Script REST API cannot
list or create them. Until he does, `excOnScheduleET_` makes every leftover hourly run take **0**
PP calls and say so, so the migration is fail-safe rather than fail-expensive.

#### The arithmetic that makes the diagnosis forced, not inferred

`excBudgetTake_` is `take = max(0, min(want, left, EXC_PP_MAX_PER_RUN, pace))` with
`pace = ceil(left / runsLeft)` (floored at `EXC_PP_MIN_PER_RUN` only under P6's condition).
`want = 271 > 0` and `EXC_PP_MAX_PER_RUN = 120 > 0`, and `ceil(left/runsLeft) >= 1` for any
`left >= 1`. **So `take = 0` is possible for exactly one reason: `left = 0`.** Pacing did not
mis-divide, the floor did not misfire, the shrunken poll set changed nothing — the balance is zero.

`left = min(EXC_PP_WEEKLY_BUDGET - exc, EXC_PP_ACCOUNT_QUOTA - total)`. Which leg is zero is
decided by measurement, not by assumption: **`exc` can only grow when the sweep actually polls, and
every poll stamps `_exc_state.last_seen`.**

| measured on live `_exc_state`, zero ParcelPanel calls | value |
|---|---|
| orders stamped `last_seen` in week `WK2026-08-17` | **94** (8/19: 90 · 8/20: 4) |
| therefore `exc <=` | **94** |
| therefore `leftMine = 2000 - exc >=` | **1,906 — NOT the binding leg** |
| open rows in scope (`_SHIP_2026-08-17` 244 + `_SHIP_2026-08-10` 17) | **261 — exactly the "0 of 261 due" in the 10:46 DM** |

🔴 **`left = 0` with `leftMine >= 1906` forces `leftAcct = 0`, i.e. ledger `total >= 2500`. `exc`
cannot reach 2,500 from 94. The drain is `rpt`.** The sweep's own 2,000 was barely touched.

#### What `rpt` actually spends, measured

`Code.gs ppLookup_` is called **three times per hourly `build_()`**, and the sets are re-asked from
scratch every run:

| leg | asks | per run |
|---|---|---|
| `enrichTransitOverride_` | window rows with `transit_days` blank | 11 |
| `productMixBreakdown_` | every distinct order on the **whole `Raw Data` tab** — not windowed | **388** |
| `writeTriage_` | active triage entries | ~75 |
| **total** | | **~474 / run** |

**~474 × 168 = ~79,600 calls/week against a 2,500/week account plan — 31.9×.** From a zeroed
ledger the account quota is gone in **5.3 hours**.

🔴 **The consequence, stated exactly.** The ledger rolls at Monday 00:00 ET (P2). The sweep gets a
paced ~12 calls/run for about five runs, and `leftAcct` hits zero somewhere around 05:00 Monday.
**For the remaining ~163 hours of every week `take` is structurally 0.** So the sweep's entire
weekly allowance — roughly 60 calls — is spent on **Monday, the one day the day gate forbids it to
post on**, and the whole Wed–Sun ping window runs on nothing.

🔴 **That is the ORIGINAL burn, reproduced through a different door.** The top of this file records
it as *"Mon/Tue polled 2,327, Wed/Thu/Fri polled ZERO — the record-only days ate the whole allowance
and the entire ping window ran with nothing left."* P5's pacing fixed that for the sweep's **own**
budget. P3 then added the **account** leg, and the account leg reintroduces the identical shape with
the reship report as the consumer that eats Monday. Fixing a starvation inside one budget does
nothing if a second, larger budget starves the same job from outside.

#### Why this is a CONCEPTUAL failure of P3, not an arithmetic bug

P3 made `rpt` report-but-never-yield and said so deliberately: *"an overdrawing report throttles the
sweep rather than the reverse."* That rule is sound **only if the overdraw is marginal.** P3 also
said the honest `rpt` figure was itself a deliverable of that change — this is that figure, and it
is **31.9× the entire account plan**. Yielding to a consumer that alone exceeds the plan by a
factor of thirty is not yielding; it is an unconditional shutdown of the yielding job.

🔴 **The rule to keep: a consumer may only be exempt from a budget while its measured draw is a
minority of that budget. An uncapped consumer must have its draw measured before it is granted the
exemption, never after.** `ppLookup_` was exempted on the strength of an unmeasured assumption, and
the first measurement invalidates the exemption.

#### The waste is re-asking for facts that cannot change

`ppLookup_` re-fetches a **delivered** box's carrier and transit days every hour, forever.
`transit_days` is set only when a box is delivered with both dates known — it is immutable
thereafter — and 66 of the 77 window rows already have it. `writeTriage_` even carries the comment
*"so we don't re-hit Shopify/Gorgias for same rows hourly"*: **every other API in that function is
cached, and ParcelPanel is the one that is not.**

#### The fix — F1 is the one that matters (Kurt GO 2026-08-20, all four DEPLOYED)

- **F1 (the real fix).** `ppLookup_` gets a per-order answer cache on a hidden `_pp_cache` tab with
  **three** retirement rules. Measured: **~474/run (~79,600/wk) → ~388 one-time warm, then ~11/day
  (~77/wk)** — 1/1000th of the previous draw.
  1. **Terminal fact** — `transit_days` exists only for a DELIVERED box, and a delivered box's
     pickup→delivery span and carrier are immutable. Never ask again. Exact.
  2. **Give up on a dead record** — 🔴 **the memo alone was NOT enough and the measurement said so:
     it retired only 49%.** 169 of the 198 recurring orders are `_SHIP_2026-06-29` (128) and
     `_SHIP_2026-07-06` (41) — **45–52 days old, never delivered, re-bought 24×/day forever.** That
     is the P9 dead-record class on the report side. An order past `PP_CACHE_GIVEUP_DAYS = 21` (the
     report's own 3-cohort window) with still no transit is retired — but only after at least one
     real fetch, so the carrier we display today is captured before it retires and nothing blanks.
  3. **Once per calendar day** for everything else.
  🔴 **THE ONE VALUE THAT CAN DIFFER, STATED BEFORE THE PUSH, NOT DISCOVERED AFTER.** Under rule 3
  a box that transitions to DELIVERED between today's first ask and midnight shows its
  `transit_days` — and the >2d "Delayed supersedes Warm" reclassification derived from it — up to
  ~23h later than before. It converges to the **identical** final value within a day. Nothing is
  lost, only delayed: the same *"a floor delays, it cannot lose"* argument this file already makes
  for `EXC_NEVER_PICKED_MIN_DAYS`. To halve the window, make `ppCacheDayKey_` resolve to date+AM/PM
  (~2× cost, still trivial). **Every other value is bit-identical**, because the return is built
  from the CACHE for every requested order rather than only the freshly-fetched ones — building it
  from fresh responses alone would blank every cached carrier, which is the regression this change
  must not cause.
  **`writeTriage_` folded in, same pass.** That block already cached Shopify **and** Gorgias
  explicitly *"so we don't re-hit them for same rows hourly"* — and ParcelPanel, **the only metered
  one of the three**, was the single API left re-bought every run. One cache inside the helper
  covers all three legs.
- **F2 (allocation). 🗑️ DELETED BY P12 — there was no plan to split.** `EXC_PP_RPT_ALLOC`,
  `EXC_PP_PA_ALLOC` and `EXC_PP_WEEKLY_BUDGET` are gone. Kept below as the record of what was
  believed, because the *reasoning* ("measure a consumer's draw before exempting it") is sound and
  outlives the numbers. **As written at the time:**
  The 2,500 plan is now SPLIT explicitly rather than discovered by starvation:
  `EXC_PP_RPT_ALLOC = 200`, `EXC_PP_PA_ALLOC = 180`, `EXC_PP_WEEKLY_BUDGET` 2,000 **→ 1,800**
  (1,800 + 200 + 180 = 2,180 ≤ 2,500). Measured sweep demand: Mon/Tue thin 100/day = 200, Wed–Sun
  5 × 261 = 1,305, **total ~1,505 of 1,800** — it fits with headroom for the first time.
  🔴 **These are NOT gates, and P3's reasoning is deliberately preserved.** `ppLookup_` and
  `paPpFetch_` feed columns Dan reads; starving them trades a visible number for an invisible one.
  The exemption stays and **the overdraw becomes LOUD** — because the failure was never the spend,
  it was the silence.
- **F3 (cadence). ♻️ REWRITTEN BY P12.** Cadence was chosen as budget yielding; with no budget it is
  purely a **detection-latency** choice, and the capacity table in P12 shows 2×/day cannot cover a
  fresh cohort in a day while hourly clears every case with ≥7× headroom. `EXC_PP_MAX_PER_RUN`
  survives ONLY as the 6-minute execution-ceiling cap (**400**), and the off-schedule guard
  (`excOnScheduleET_`, `EXC_RUN_HOURS_ET`, `excRunsLeftThisWeek_`) is **deleted** — it existed to
  protect a pacing denominator, and making an off-slot run take 0 calls is exactly the coverage
  rationing P12 forbids. **The code is now cadence-agnostic:** more runs are fresher and never more
  expensive per box, because (b) bounds the daily work.
- **F4 (observability). ♻️ REWRITTEN BY P12 into a THROTTLE alarm.** There is no binding leg to name
  any more, so `excBudgetBindingLeg_` and the STARVED alarm are deleted. What replaces them fires on
  the condition that can actually still stop a sweep: **429s over threshold after retries, or
  `x-ratelimit-remaining` hitting 0** — naming the run, the window, and the counts. 🔴 **The
  BLINDSPOT alarm is KEPT and is now the only remaining coverage alarm**, which makes it more
  important than it was, not less: with STARVED gone it is the sole thing standing between "nothing
  was due" and "we stopped looking." F4's original reasoning, which stands: **an alarm that cannot
  say WHY is the silent-failure shape one level up.** As written at the time:
  The starved ops DM printed `allowed` and `elig.length` but **not the
  ledger** — which is precisely why 2026-08-20 took a `_exc_state` reconstruction instead of a
  one-line read. `excBudgetBindingLeg_` now puts the full ledger, **the name of the binding leg**,
  and any consumer over its allocation into the alarm, the *"budget bit"* log line, and the *Check
  properties* menu. A starvation alarm that cannot say **who took the budget** is the silent-failure
  shape this file exists to prevent, one level up again.

#### Cadence: 2 runs/day, and why throughput is not the reason

Pacing makes weekly throughput **cadence-invariant** — every option below delivers the same
~257 calls/day against a 261/day due set, so cadence must be chosen on other grounds:

| cadence | runs/wk | fair share of 1,800 | calls/day | note |
|---|---|---|---|---|
| hourly | 168 | 10.7/run | 257 | 24 full sweeps to place 257 calls |
| 4×/day | 28 | 64/run | 257 | works |
| **2×/day** | **14** | **129/run** | **257** | **recommended**; needs `EXC_PP_MAX_PER_RUN` 120 → 150 |
| 1×/day | 7 | 257/run | 257 | needs `MAX_PER_RUN` >= 300 and presses the 6-min ceiling |

**Recommend 2×/day.**
- Two runs exactly cover the daily due set: the first takes ~130 of 261, and because the
  once-per-day tier retires those, the second takes the remaining ~131.
- **12× less Apps Script overhead.** Each run pays the free Shopify triage over up to
  `EXC_MAX_POLL_PER_RUN = 1200` open rows plus a read/write of a 9,333-row `_exc_state`. Paying
  that 168 times to place 10 calls each is the starved-run shape itself.
- **Latency is free here.** Every ping class has a >= 3-day floor
  (`EXC_NEVER_PICKED_MIN_DAYS = 3`, `EXC_DELAYED_MIN_DAYS = 3`). A box polled twice a day cannot
  lose a case that needs three days of evidence to fire — the same argument this file already makes
  for the floor: **it delays, it cannot lose.**
- **Not 1×/day:** no slack. One run killed at the 6-minute ceiling loses the whole day.
- **Not hourly:** a 10-of-271 grant is barely distinguishable from the zero Kurt is complaining
  about, and would keep tripping STARVED.

🔴 **2×/day REQUIRES the once-per-day tier (P5b) to stay.** Without it the second run re-polls the
first run's boxes and the day's coverage halves.

#### 🔴 THE PACER HAD TO LEARN TO COUNT RUNS, OR THE FIX WOULD HAVE BEEN A NEW STARVATION

`excRunsLeftThisWeek_` returned **hours** to Sunday-midnight — correct only while the trigger was
hourly. Left alone at 2 runs/day it would divide the remaining balance by ~168 instead of ~14, hand
out ~12 calls a run, and leave **~1,600 of the 1,800 unspent every week**: the same starvation
wearing the old one's clothes. It now counts scheduled slots:
`(7 − dow) × EXC_RUN_HOURS_ET.length + slots still ahead today`.

**`EXC_RUN_HOURS_ET = [9, 16]` is the SSOT, and the trigger must match it.** Apps Script cannot list
or create triggers over the REST API, so the schedule lives in Kurt's hands and the constant is the
only thing the code can reason from. `excOnScheduleET_` catches drift: **a run outside the slots
(±1h) takes 0 PP calls and says why.** A leftover hourly trigger is therefore free rather than
ruinous — without this guard, 24 real runs against a 14-run denominator would over-draw by 1.7× and
drain the week early. 🔴 **If that message appears, fix the trigger — do NOT widen the constant.**

🔴 **THE HOURS ARE ET; THE TRIGGER IS SET IN CENTRAL.** `appsscript.json` pins the project to
`America/Chicago` while `EXC_TZ` is `America/New_York`, so a trigger Kurt sets for 9am fires at
**9am CT = 10am ET**. The slots ET 9 / ET 16 are therefore **CT 8 / CT 15**. Both zones observe DST
together, so the offset never moves. This is the kind of mismatch that produced P2.

#### Kurt's trigger clicks (the API cannot do this)

In the Apps Script editor for **Running Reship** → the **clock icon** (Triggers):
1. **Delete** the existing hourly trigger for `hourlyExceptionSweep` (the "Hour timer / Every hour"
   row). 🔴 Deleting it is what actually changes the cadence — the two below are additions.
2. **Add trigger** → function `hourlyExceptionSweep`, deployment *Head*, event source *Time-driven*,
   type **Day timer**, time of day **8am to 9am**.
3. **Add trigger** → same function, **Day timer**, time of day **3pm to 4pm**.

The function keeps its `hourlyExceptionSweep` name on purpose: **triggers bind by name**, and
renaming it would leave any trigger Kurt does not re-point throwing "function not found". The name
is now historical, not a description.

**Why 09:00 and 16:00 ET specifically:** they straddle the day, so the second run retires what the
once-per-day tier left; both sit inside the Wed–Sun ping window at hours a ping is actually read;
09:00 catches the overnight exception scans and 16:00 catches the day's delivery attempts while
there is still time to act on an `ADDRESS_ISSUE` or `ATTEMPTED_DELIVERY`; and they are **7 hours
apart, not 6** — deliberately clear of `EXC_SILENT_ALARM_EVERY_MS`, so a genuinely broken sweep
alarms on **both** runs instead of having its second alarm swallowed by the rate limiter. (The
observed 03:46 → 10:46 spacing on 2026-08-20 is exactly that rate limiter at work.)

#### What becomes dead at the ParcelPanel → DigitalOcean webhook cutover

- **DEAD:** F2's allocation constants and the whole ledger (as P10 already records).
- **SURVIVES as a latency choice:** F3's cadence. Reading MySQL is free, so cadence stops being a
  budget question and becomes purely "how fresh must an alert be".
- 🔴 **SURVIVES, and is THE GAP IN THE CUTOVER PLAN — carried here so it cannot be lost with the
  session that found it (Kurt, 2026-08-20).**

  > **Retiring the sweep's polling at the webhook cutover frees a quota the sweep was never
  > spending. The account stays ~32× oversubscribed unless F1 ships or the report reads the same
  > table.**

  F1 is `Code.gs ppLookup_`, the **reship report's** enrichment. P10's dead-list retires
  `excPpFetch_` — the **sweep's** polling. The sweep is budgeted at ≤ 1,800/wk and was measured
  actually spending **~60**. The report was the **~79,600/wk** consumer. Anyone reading the P10
  dead-list alone would conclude the quota problem ends at cutover; it does not. **That sentence is
  the reason F1 mattered more than everything else queued**, and it stays true for whatever
  replaces `ppLookup_` next: the question to ask of any future consumer is not "is it capped?" but
  **"what is its measured draw against the plan?"**

#### Verification

- Arithmetic above re-derived from the **live** `Exceptions.gs` (`22d8ae6ceefe`) and `Code.gs`
  (`88b44f93ce14`), re-GET immediately before this edit and byte-identical to the worktree.
- `scratchpad/exc_leg_probe.py` — leg elimination from live `_exc_state`. Independent corroboration:
  the in-scope open count it computes (261) equals the "0 of **261** due" in Kurt's 10:46 DM,
  measured from state, not copied from the message.
- `scratchpad/rpt_measure.py` / `rpt_measure2.py` — the `rpt` draw, from live `_state` and
  `Raw Data`. **Zero ParcelPanel calls were made by any of this.**
- `scratchpad/f1_cache_test.js` — drives the **REAL** `ppLookup_` in a stubbed Apps Script context:
  cold cache, same-day identity against the pre-cache values, terminal + age-out retirement, carrier
  retained after retirement, 404-stamps vs 429-never-stamps, the stated deviation class **and its
  convergence**, no-API-key, and the omitted-`cohortOf` path. **23/23 PASS.**
- `scratchpad/f234_pace_test.js` — the real `Exceptions.gs` pacing math at 2 runs/day: the split
  sums under the plan, `runsLeft` counts slots (14 Mon 09:00 → 1 Sun 16:00), on/off-schedule
  detection with slop, an off-schedule run taking 0 **and saying why**, two runs covering the 261
  due set, the binding leg naming the account leg *and the reship report* by name, and a regression
  case proving the exact 2026-08-20 ledger shape now grants a real batch. **25/25 PASS.**
- Regression suites unchanged by this edit: `p10_scope_test.js` PASS, `p9_dead_test.js` 31/31,
  `p8_route_test.js` 9/9, `gas_lint.js` SYNTAX OK ×4 + concatenated project, **COLLISIONS NONE**
  (all 7 new `Code.gs` globals and all 6 new `Exceptions.gs` globals are single-file).
- 🔴 **NOT verified:** the `PP_WEEK_USED` property itself. Script Properties are not readable over
  the Apps Script REST API and the local token holds only `script.projects` / `script.deployments`,
  so `total >= 2500` is established **by elimination**, not by reading the counter. The direct read
  is one click: **Shipping Exceptions → Check properties**. F4 exists so this is never again a
  reconstruction.
- 🔴 **NOT verified: any live execution, and no measured post-fix call count.** Apps Script cannot
  be run from here. Every "after" figure above is derived from live state plus the code path, not
  observed. The first real numbers arrive in the `ppLookup_` log line
  (`N asked, M fetched (K cached)`) on Kurt's next run.
- 🔴 **NOT verified: the trigger edit.** F3's pacing assumes 2 runs/day; until Kurt makes the
  trigger change, `excOnScheduleET_` holds every off-slot run to 0 calls.

### P12 — COVERAGE IS NEVER RATIONED. ONLY SPEED IS. (Kurt GO, 2026-08-20)

> Kurt's alarm, the one that ended the budget era: *"exceptions sweep polled 0 boxes while 87 are
> still open … 55 never polled once … ParcelPanel budget allowed 0 of 73 due … Nothing was
> checked, so this run is NOT an all-clear."*

🔴 **THE PREMISE UNDER P1–P11 WAS FALSE.** Every rationing directive in this file was arithmetic on
**"2,500 ParcelPanel calls per week."** That number is Kurt's **average weekly ORDER count** — the
10,000/month plan quota divided by four — misread as an API request budget. It was never a limit on
requests, and no such limit was ever measured. 55 boxes went unchecked to protect a fence that does
not exist.

**What the limit actually is, measured on this account, not quoted:**

| fact | evidence |
|---|---|
| **120 requests per minute, per API key** | `docs.parcelpanel.com/shopify/api-webhook/api-v2/`, and every response carries it |
| `x-ratelimit-limit: 120` in EVERY response | live probe 2026-08-20 against `open.parcelwill.com` |
| the window is ONE MINUTE and refills whole | probe: `remaining` 119 → 118 → 117 → 116 on four calls, back to **119** after 65 s |
| **quota is ORDERS TRACKED, not requests** | ParcelPanel pricing: *"1 order = 1 quota"*, *"Order lookups do not consume quota … unlimited lookups"* |
| corroboration that lookups are free | monthly fulfilled orders 9,574 / 9,779 / 11,977 / 8,710 track the 10,000 plan, while we issue 19k–33k requests/month. If requests consumed quota we would carry **$500–1,150/month of overage against a $215 plan**, and we do not |

**120/min = 7,200/hr ≈ 1.2M/week. The entire ledger's final reading, 4,382 calls, is 0.4% of it.**

🔴 **THE RULE.** *Coverage is never rationed. Only speed is.* Every ParcelPanel client in this
project:

1. **paces under 120 req/min** — target **100/min** (83%, leaving margin for the other consumers on
   the same key);
2. **reads `x-ratelimit-remaining` from every response** and brakes on it — this, not a Script
   Property, is the real cross-runtime coordination;
3. **never abandons an order because of a 429** — it waits and asks again;
4. **never has a per-run or per-week cap whose effect is that a box goes unchecked.**

**A run may take longer. A box is always checked.**

🔴 **THE HEADER WAS THE ANSWER ALL ALONG.** `PP_WEEK_USED` existed to approximate account-wide
consumption across GAS, Kurt's PC and DigitalOcean — and it **structurally could not**, because
those three runtimes cannot read each other's Script Properties. P3 admitted this and called the
number "a lower bound." `x-ratelimit-remaining` is the same fact, exact, in every response, from
every runtime, and has been there since before any of this was written. **A shared counter was
invented to estimate a number the server was already reporting.** Before building a meter, check
whether the thing being metered already meters itself.

🔴 **THE FAULT WAS NEVER VOLUME — IT WAS BURST SHAPE.** At the moment of the alarm this project had
three ParcelPanel call sites and **two of them fired `UrlFetchApp.fetchAll(slice(i, i + 50))` with
no pause and no retry** (`Code.gs ppLookup_`, `PivotAnalytics.gs paPpFetch_`), while the third
(`Exceptions.gs excPpFetch_`) paced at batch-10 / 1,000 ms — **~600/min, five times the ceiling**.
`Exceptions.gs` has documented this exact class since day one — *"fired fetchAll in batches of 50
with no pause and no retry: 780 of 900 fetches failed"* — so these were **two unfixed instances of a
bug this codebase had already diagnosed and written down.** A 50-wide burst against a 120/min bucket
is 42% of a minute's allowance dispatched in one instant; the budget layer could not have prevented
a single one of those 429s, and rationing coverage to "save" calls made the bursts no smaller.

#### The limiter (numbers derived here, not inherited)

| knob | value | why this value |
|---|---|---|
| target rate | **100 req/min** | 83% of the measured 120; the remaining 20/min is margin for the Python client and the cloud worker on the same key |
| batch / max in flight | **10** | `fetchAll` is Apps Script's only concurrency primitive, and 10 is the value that ended the 780-of-900 failure. 10 requests per 6.0 s cycle **is** 100/min |
| cycle | **6,000 ms, measured from the START of the fetch** | 🔴 sleeping 6 s *after* a fetch that itself took 3 s yields 66/min, not 100. The sleep is `max(0, 6000 − elapsed)`; pacing must subtract the work, or the rate silently under-runs |
| adaptive brake | `remaining < 12` → sleep to the next minute boundary | 🔴 **not** the proposed 30 — see the sanity-check below |
| 429 retry | `Retry-After` if present, else 2/4/8/16/32 s jittered, **5 attempts** | a refused request is not an answer (P13) |
| run-level guard | **>20% of a run's requests still 429 after retries → throw** | so a genuine ParcelPanel outage can never look like a slow day |

🔴 **WHY THE BRAKE IS 12 AND NOT 30 — the one number that did not survive its own sanity check.**
The proposal specified `remaining < 30`. But if we deliberately spend 100 of a 120 bucket every
minute, **our own steady-state trough IS ~20** — already below 30. A brake at 30 would therefore
fire on our own correct pacing, every single minute, and would carry no information about anyone
else. The brake's whole purpose is to detect that **another consumer on this key is eating the
bucket**, so its threshold must sit *below* our own trough and *at or above one batch* (never
dispatch 10 requests you cannot afford). 12 satisfies both. 🔴 **If this brake starts firing often,
that is the signal that a second consumer is active — it is not noise, and the fix is not to lower
the threshold.**

🔴 **THE ONE PLACE "TAKE LONGER" HAS A HARD WALL: the 6-minute Apps Script execution ceiling.**
Waiting is free everywhere except here. At 100/min against `EXC_TIME_BUDGET_MS = 240000` (4 minutes
of fetching, 2 minutes reserved to write state), **one sweep run can place ~400 ParcelPanel calls**
— fewer if a brake or a backoff eats into it. Does current demand fit?

| demand (once-per-day tier ⇒ calls/day ≈ boxes due/day) | calls/day | hourly (24 runs → ~9,600/day) | 2×/day (2 runs → ~800/day) |
|---|---|---|---|
| today's alarm: 87 open, 73 due | 73 | **131× headroom** | 11× headroom |
| measured 2026-08-20 | 261 | **37× headroom** | 3.1× headroom |
| measured 2026-08-10, all open | 487 | **20× headroom** | 1.6× headroom |
| 🔴 worst case — a fresh cohort past the age gate (`_SHIP_2026-08-17` held **1,344** open) | 1,344 | **7.1× headroom** | **0.6× — DOES NOT FIT** |

**Hourly fits every observed and worst-case demand with ≥7× headroom. 2×/day does not survive a
fresh-cohort day** — it would need two days to cover one, which is precisely the coverage gap P12
forbids. This is a hard capacity argument for hourly, independent of the latency argument.

🔴 **A run that hits the ceiling is NOT a run that rationed.** The orders it did not reach are never
stamped `last_seen`, so they stay at the head of the longest-unpolled ordering and are the *first*
work the next run takes. The run says so out loud. Deferral-to-the-next-run is the only form of
"later" this platform permits, and it is bounded by the cadence, not by a balance.

#### 🔴 THE SHARED KEY — why the header brake is load-bearing, not belt-and-braces

The 120/min bucket is **per API key**, and the Apps Script sweep, the reship report, the analytics
union, the Python client and the cloud worker all hold the **same key**. Three processes each
targeting 100/min is 300/min against a 120 ceiling, and **each one's own pacing would look perfectly
healthy** — a local token bucket only knows what that process spent.

`x-ratelimit-remaining` is the only place another consumer's draw appears, which is why it is read
off **every** response rather than only on failure, and why the brake exists at all. Under
contention the limiter degrades to a fair share and keeps going; without the brake it would degrade
to a 429 storm. 🔴 **This is also the honest limit of the design: the brake makes contention SAFE,
it does not make it EFFICIENT.** If braking becomes routine, the answer is a genuinely shared
counter or the webhook cutover — never a lower brake threshold and never a smaller poll set.

#### As implemented (2026-08-20)

| directive / constant | status | where |
|---|---|---|
| P1 charge/refund — `excBudgetTake_`, `excBudgetSettle_` | 🗑️ deleted | `Exceptions.gs` |
| P2 week key — `excWeekKey_` | 🗑️ deleted | `Exceptions.gs` |
| P3 ledger-as-budget — `excBudgetRead_/Write_/Charge_/LeftAccount_/BindingLeg_`, `PP_WEEK_USED` | ♻️ → `excPpRecordCalls_` + daily `PP_CALLS_TODAY` | `Exceptions.gs` |
| P6 pace floor — `EXC_PP_MIN_PER_RUN` | 🗑️ deleted | `Exceptions.gs` |
| P7 reset lever — `excResetWeeklyBudget` + its menu item | 🗑️ deleted | `Exceptions.gs` |
| F2 — `EXC_PP_WEEKLY_BUDGET` 1800, `EXC_PP_RPT_ALLOC` 200, `EXC_PP_PA_ALLOC` 180, `EXC_PP_ACCOUNT_QUOTA` 2500 | 🗑️ deleted | `Exceptions.gs` |
| F3 schedule guard — `EXC_RUN_HOURS_ET`, `EXC_RUN_HOUR_SLOP`, `excRunsLeftThisWeek_`, `excOnScheduleET_` | 🗑️ deleted (it rationed coverage) | `Exceptions.gs` |
| thin-day cap — `EXC_THIN_DAY_CAP` 100, `EXC_THIN_DAY_PROP`, `excThinDayLeft_/Add_` | 🗑️ deleted; the sweep stays **flagged-only and complete** | `Exceptions.gs` |
| STARVED alarm | 🗑️ deleted (it can no longer fire) | `Exceptions.gs` |
| F4 binding-leg alarm — `excBudgetBindingLeg_` | ♻️ → throttle guard, `EXC_PP_THROTTLE_RATIO` 0.2 | `Exceptions.gs` |
| D28 metering — `excBudgetCharge_(charged, 'rpt')` | ♻️ → `excPpRecordCalls_(charged, 'rpt')` | `Code.gs` |
| **the limiter** — `EXC_PP_TARGET_PER_MIN` 100, `EXC_PP_CYCLE_MS` 6000, `EXC_PP_BRAKE_REMAINING` 12, `EXC_PP_RETRY_MS` | 🆕 | `Exceptions.gs` |
| **per-run cap** — `EXC_PP_MAX_PER_RUN` 150 → **400**, redefined as the 6-min ceiling | ♻️ | `Exceptions.gs` |
| `ppLookup_` 50-wide burst → batch 10 + paced cycle + 429 retry | 🔧 **the actual bug** | `Code.gs` |
| `paPpFetch_` 50-wide burst → batch 10 + paced cycle + 429 retry | 🔧 **the actual bug** | `PivotAnalytics.gs` |
| `excPpFetch_` ~600/min → 100/min, `EXC_PP_PAUSE_MS`/`EXC_PP_BACKOFF_MS` replaced | 🔧 | `Exceptions.gs` |
| F1 `_pp_cache` (terminal memo + 21-day give-up) | ✅ **kept**, re-justified in D28 | `Code.gs` |
| P5(a) Shopify-triages-first · P5(b) once-per-day · P5(c) 3-day age gate | ✅ **kept**, de-linked from budget | `Exceptions.gs` |
| P4 · P8 · P9 quarantine · P10 scope · classifier · both absence-derived classes · day gate | ✅ **kept**, untouched | — |
| **BLINDSPOT alarm** | ✅ **kept — now the ONLY coverage alarm** | `Exceptions.gs` |

#### Measured behaviour (`scratchpad/p12_limiter_test.js` — 70/70 PASS)

Drives the **real** `excPpFetch_`, `ppLookup_` and `paPpFetch_` sources in a stubbed Apps Script
context on a **virtual clock**, so the rate is *measured*, not asserted:

| property | measured |
|---|---|
| batch width | **10** (was 50 in two of the three call sites) |
| dispatch-to-dispatch interval | **6,000 ms exactly**, whether the fetch itself takes 100 ms or 3,000 ms |
| 🔴 worst-case requests in any 60 s window | **100** — 20 under the 120 ceiling, at steady state over a 500-order run |
| a 429, retried | order still **answered**; `throttled` 0, `deferred` 0 |
| a 429 that survives all 5 retries | `served` **0**, `throttled` 10/10, `deferred` 10/10, `failed` **0**, `seen` **empty** ⇒ never stamped, stays queued |
| brake at `remaining = 5` | fires, parks to the minute boundary |
| brake at `remaining = 20` (our own trough) | **does not fire** — this is why the threshold is 12, not 30 |
| execution ceiling reached | `served + deferred == asked` (nothing lost), log says CEILING and "NOT dropped" |
| `PP_CALLS_TODAY` at 999,999 | still serves every order ⇒ **nothing gates on the counter** |
| `ppLookup_` on a 429 | stamps **nothing** in `_pp_cache`, and the order **never goes terminal** |

**Today's demand against it:** the alarm's 73 due boxes are **44 seconds** of fetching. The
worst case in the capacity table, 1,344, is 13.4 minutes — i.e. four hourly runs, or ~400 per run
against the ceiling.

#### Verification

- **`scratchpad/p12_limiter_test.js` — 70/70 PASS** (above).
- **Regression suites, all re-run green against the patched sources:** `p8_route_test.js` 9/9,
  `p9_dead_test.js` 31/31, `p10_scope_test.js` PASS, `f1_cache_test.js` PASS.
  🔴 Two of these needed **test-side** updates, and the distinction matters: `p9` asserted
  `pp.charged` (renamed to `pp.served` — same number, and it is a rate now, not a bill), and `f1`
  counted fetch *attempts* on an order that always 429s, which under P13 is retried 6 times instead
  of dropped once. Its substantive cache assertions never moved.
  🗑️ **`f234_pace_test.js` is RETIRED** (`f234_pace_test.RETIRED.js`) — it asserts the pacing math
  P12 deletes. It is kept unrun as the record of what was believed; do not "fix" it.
- **`scratchpad/gas_lint2.js`:** SYNTAX OK ×4 + the concatenated project, **COLLISIONS NONE**, and a
  **deleted-symbol sweep** proving no *live* reference (comments excluded) to any of the 25 removed
  budget identifiers survives in any file, plus every new limiter symbol declared exactly once and
  in one file.
- **The 120/min limit was re-probed live before any of this was written**, not taken from the prior
  analysis: `x-ratelimit-limit: 120`, `remaining` 119→118→117→116 across four calls, back to **119**
  after 65 s. Four ParcelPanel calls total were spent on this entire change.
- 🔴 **NOT VERIFIED — no live Apps Script execution.** Apps Script cannot be run from here; the
  Apps Script REST API cannot execute functions, list triggers, or read Script Properties. Every
  figure above is measured against the real source on a stubbed clock, not observed in production.
  The first real numbers arrive in the `PP fetch:` / `ppLookup_:` log lines on Kurt's next run.
- 🔴 **NOT VERIFIED — the `PP_WEEK_USED` property itself.** Its final reading is recorded above from
  the alarm text, because Script Properties are not readable over the REST API. It is now dead data
  and should be deleted by hand.
- ✅ **The Python side is now DONE** — see “P12/P13 — THE PYTHON SIDE” below (2026-08-20, same day). It was unchanged in the Apps Script pass.

#### 🔴 DEPLOYED (2026-08-20) — and what still needs Kurt's click

| file | was | now |
|---|---|---|
| `Exceptions.gs` | `7d0dd0edfba0` | **`df1ff7e8088b`** |
| `PivotAnalytics.gs` | `b6059ab1c568` | **`50ddf34eb17b`** |
| `Code.gs` | `810e02c41d4f` | **`b38c8b5efbd1`** |
| `Notifications.gs` | `68e3b2245faf` | *untouched, verified byte-identical after all three pushes* |

Pushed one file per PUT via the gated `gas_swap.py`, **never `clasp push`** (`.claspignore` is a
two-file allow-list and it deleted three deployed files on 2026-08-14). `Code.gs` went **last**
because it owns the reserved `onOpen`. Each push re-GET the live project immediately beforehand and
verified every untouched file byte-identical afterwards.

🔴 **Rollback** — the pre-push bytes are snapshotted at
`scratchpad/rollback_p12/` (verified to match the live shas above before anything moved):

🔴 One line, PowerShell, absolute paths — no `cd` and no `&&` (PowerShell 5.1 has no `&&`):

```
python "C:\Users\Work\AppData\Local\Temp\claude\C--Users-Work-Claude-Projects\b974d338-3e7f-4d7b-a87c-88d29670351d\scratchpad\gas_swap.py" push "Exceptions=C:\Users\Work\AppData\Local\Temp\claude\C--Users-Work-Claude-Projects\b974d338-3e7f-4d7b-a87c-88d29670351d\scratchpad\rollback_p12\Exceptions.gs" "PivotAnalytics=C:\Users\Work\AppData\Local\Temp\claude\C--Users-Work-Claude-Projects\b974d338-3e7f-4d7b-a87c-88d29670351d\scratchpad\rollback_p12\PivotAnalytics.gs" "Code=C:\Users\Work\AppData\Local\Temp\claude\C--Users-Work-Claude-Projects\b974d338-3e7f-4d7b-a87c-88d29670351d\scratchpad\rollback_p12\Code.gs"
```

It restores all three in ONE PUT (so the project is never half-rolled-back), needs no
`--allow-create` because all three files exist live, and prints the same
`VERIFY: ALL BYTE-IDENTICAL` proof. ⚠️ That scratchpad is session-scoped — if it has been cleaned,
roll back from git instead: the pre-P12 bytes are `7ff83dd`.

##### Kurt's clicks (the Apps Script REST API can do none of these)

**1. Cadence — recommended: go back to hourly.** F3 set it to 2×/day purely to yield budget. With
no budget, cadence is a **detection-latency** choice, and the capacity table above shows 2×/day
cannot cover a fresh 1,344-box cohort in a day while hourly clears every case with ≥7× headroom.
487 open boxes polled once each per day is ~0.3 calls/minute — the load is irrelevant either way.

🔴 **`installExceptionsTrigger()` is CORRECT AGAIN** — it always created `everyHours(1)`, which was
wrong under F3 and is right under P12. So hourly is **one menu click**:

> **Shipping Exceptions → Install/repair hourly trigger**

It is idempotent: it deletes every existing `hourlyExceptionSweep` trigger first, so it also removes
the two Day-timer rows. Nothing else's trigger is touched.

**If Kurt prefers to keep 2×/day instead:** do nothing. The code no longer cares — the off-schedule
guard that used to zero out unscheduled runs is deleted, so both cadences simply work. 🔴 **In that
case accept the stated tradeoff:** a fresh cohort takes two days to cover once, and the second day's
boxes are 24h staler.

**To set the triggers by hand instead of using the menu** (clock icon in the Apps Script editor for
*Running Reship*): delete the two Day-timer rows for `hourlyExceptionSweep`, then **Add trigger** →
`hourlyExceptionSweep`, *Head*, *Time-driven*, **Hour timer**, **Every hour**.

🔴 **TIMES IN THE TRIGGER UI ARE CENTRAL.** `appsscript.json` pins the project to
`America/Chicago` while `EXC_TZ` is `America/New_York`. That mismatch is what produced P2, and it
still applies to anything hour-specific — the existing Day timers at **8–9am / 3–4pm CT** are the
ET 9 / ET 16 slots. Hourly makes the whole question moot, which is one more small argument for it.

**2. Delete the dead property.** *Project Settings → Script Properties* → delete **`PP_WEEK_USED`**.
It is no longer read or written by anything; leaving it invites someone to read it as live. Its
final value is recorded above. (Properties are not writable over the REST API.)

**3. Confirm it works.** **Shipping Exceptions → Check properties** prints the new
`PP_CALLS_TODAY` line, the per-run ceiling and the cadence note, read-only, no trigger needed. Then
**Run sweep now** and read the `PP fetch:` log line — `N asked, N served, 0 still throttled` is the
success shape, and `min x-ratelimit-remaining` should sit near 20 of 120.

### P13 — NO CONSUMER MAY DROP A 429 (Kurt GO, 2026-08-20)

A 429 is **backpressure, not an answer.** An order refused by one is in exactly the state it was in
before the request: unknown. Every consumer must therefore treat it as *not yet asked* —
🔴 **never stamp it, never count it as served, never let it fall out of the set.**

**What this forbids, concretely — all three were live at the moment of the alarm:**

| call site | the drop | consequence |
|---|---|---|
| `Code.gs ppLookup_` | `if (code === 429 || code === 503) return;` with no retry pass | that order's carrier / `transit_days` silently missing from Dan's column for the run |
| `PivotAnalytics.gs paPpFetch_` | same shape, inside a 50-wide burst | the box drops out of the delivered-union — and PP only ever *rescues* boxes Shopify does not already show delivered, so the loss is invisible everywhere except the few orders where it matters |
| `ShipRouting/server/sync_delivery_status.py` and the `ParcelPanelClient` consumers | ~85 boxes/run, ~340/week | Python side — **proposed separately, not changed in this pass** |

**The contract.** A 429 is retried inside the run (`Retry-After`, else 2/4/8/16/32 s jittered,
5 attempts). If the retries are exhausted, or the execution ceiling arrives first, the order is
**left unstamped and queued** — it is the next run's first work, not a hole. The only thing a 429
may ever cost is time.

🔴 **AND IT MUST BE COUNTED SEPARATELY FROM A FAILURE.** P9 established that fusing two opposite
conditions under one number is how nine dead records suppressed ten good answers. Throttling and
transport failure are exactly such a pair: one means *slow down*, the other means *something is
broken*. They keep their own counters and their own thresholds, and they must never share one.

### P12/P13 — THE PYTHON SIDE (2026-08-20)

The Apps Script pass fixed three call sites in one runtime. Python holds **eight order-loops across
TWO INDEPENDENT HTTP TRANSPORTS**, and that second transport is the part a single fix would have
missed:

| transport | who | why it is separate |
|---|---|---|
| **A** — `ParcelPanelClient` (`AppyHour/GelPackCalculator/parcel_panel.py`) | 7 loops: `sync_orders`, `pp_fetch_concurrent`, `daily_shipping_sync`, `backfill_sync.sync_parcel_panel` (which `pipeline_run.py` and `sync_logon.py` both call), `backfill_pp`, `pp_backfill_aged_out`, `kori/gel_pack_webview` ×2, `ShipRouting/phase0_origin_backfill` | one `requests.Session`, on Kurt's PC |
| **B** — `ShipRouting/server/sync_delivery_status.py` | 1 loop, `fetch_pp` | builds its OWN Session and never imports `parcel_panel` — it deploys to DigitalOcean App Platform where `GelPackCalculator/` does not exist. 🔴 **A limiter on `ParcelPanelClient` therefore does NOT cover everything.** |

#### The limiter — `ShipRouting/server/pp_ratelimit.py` (stdlib-only, deploys with the server)

Kurt's call (2026-08-20): **extract, do not duplicate.** It lives under `server/` so the cloud
writer can import it, and `parcel_panel.py` reaches it through the same `sys.path` shim
`phase0_origin_backfill.py:19-21` already used. One implementation, one set of constants, and no
parity test to rot. If the import fails, `parcel_panel.py` **raises** — an unpaced client is the
bug this exists to prevent, so it must never be the fallback.

| knob | value | why |
|---|---|---|
| target | **100 req/min** | 83% of the measured 120; 20/min of margin for the other consumers on this key |
| bucket capacity | **10** | see the sanity check below — **not** the 20 that was proposed |
| max in flight | **8** | process-wide per key, enforced regardless of a caller's `workers=` |
| brake | `x-ratelimit-remaining < 12` → park to the minute boundary | same threshold and same reasoning as the GAS side |
| retry | `Retry-After`, else 2/4/8/16/32 s jittered, **5 retries** (6 requests) | identical ladder to `excPpFetch_`, so both runtimes back off the same way |
| run guard | `exhausted > 20% of asked` → throw | an outage can never look like a slow day |

🔴 **THE SECOND NUMBER THAT DID NOT SURVIVE ITS OWN SANITY CHECK — capacity 10, not 20.** The
proposal specified a bucket of 20. But the worst case in any sliding 60-second window is
`capacity + target`, because a full bucket dispatches instantly and *then* refills at the target
rate: capacity 20 gives **20 + 100 = 120/min — exactly the hard ceiling, with zero margin**, which
contradicts the entire reason the target is 100 and not 120. Capacity 10 puts the worst window at
**110**, keeps the 10/min of headroom, and matches the batch width that ended the 780-of-900 Apps
Script failure. Same class of error as the brake-30, found the same way: by doing the arithmetic on
the proposed number instead of adopting it.

🔴 **THE LIMITER IS PROCESS-GLOBAL, REGISTERED BY `api_key`.** `pp_fetch_concurrent` builds a
**fresh client per worker thread** (`client.__class__(api_key)`), so a per-instance limiter would
have handed 12 threads 12× the budget — a limiter that makes the burst *worse* while reading as a
fix. The 120/min bucket is per KEY, so the registry is keyed by KEY. `--workers` no longer sets the
rate; raising it buys nothing.

#### What each call site was doing, and what it does now

| site | the fault | now |
|---|---|---|
| `parcel_panel.sync_orders` | `time.sleep(0.3)`; `except Exception: pass` carrying a **404 rationale** ("might not exist in PP yet") applied to *every* failure class | limiter-paced; 404 still an ordinary empty answer, a throttled order is left absent-and-queued, everything else increments `failed` |
| `parcel_panel.pp_fetch_concurrent` | string-sniffed `"429" in str(e)`, slept 65 s, retried **once**, then returned None — per-thread and unsynchronised | retries belong to the limiter; `max_retries` is dead and ignored (kept so callers don't break); None means UNKNOWN, never "no data" |
| `daily_shipping_sync.run_pp_sync` | 0.3 s loop; the dead-feed guard fired on `written == 0` and would have called a **throttle storm a dead feed** | the throttle storm is diagnosed FIRST and separately — the two demand opposite actions |
| `backfill_sync.sync_parcel_panel` | 0.3 s loop; "log the first 3 errors" swallow | limiter-paced, `stats.check()` runs after the store so a partial batch is still persisted |
| `backfill_pp` | advertised its runtime as `orders × 0.3 s` | quotes the 100/min target; a long run is the intended outcome |
| `pp_backfill_aged_out` | `--workers 12` was the de-facto rate control | workers no longer set the rate; a throttled row stays `aged_out` **on purpose**, and the dry-run hit-rate is labelled a **floor** whenever a row was refused |
| `kori/gel_pack_webview.sync_parcel_panel` | 🔴 selected the **ENTIRE fulfillments table** (80k+ rows, all history, no date filter, no delivered filter) and polled every row | reuses the windowed predicate `sync_all_data` step 4 already used (56 days, not-yet-delivered). **A bug fix, not rationing** — narrowing the QUESTION is not rationing the ANSWER; re-asking a box delivered in 2025 answers nothing and crowds out boxes in flight |
| `ShipRouting/phase0_origin_backfill` | `time.sleep(0.12)` = **~500 req/min, 4× the ceiling** — the worst offender anywhere in Python — plus `gap[:pp_limit]` with `build.py:537` passing **1500** | sleep gone; the row cap is replaced by a **wall-clock budget plus a persisted resume cursor** (`phase0_pp_cursor.json`, beside the DB), so a bounded run resumes where the last one stopped and the tail is reached |

🔴 **A ROW CAP AND A TIME BUDGET ARE NOT THE SAME BOUND.** A row cap's effect is that a row goes
unchecked, with nothing recording that it went unchecked. A time budget plus a resume cursor bounds
the RUN while guaranteeing the ROW — that is what makes it P12-legal. Without the cursor,
"newest-first + a limit" re-asks the same head forever and never reaches the tail.

#### 🔴 STILL OPEN — Transport B (`ShipRouting/server/sync_delivery_status.py`)

Not changed here: `ShipRouting/server/**` is claimed by another session (ACTIVE-CLAIMS,
`codex-routing-dashboard`; the repo branch by `routing-coordinator-wk0818`). It is currently
flag-off (`DELIVERY_SYNC=0`), so nothing is burning today — **but it must not be flipped on in its
current shape.** Three compounding faults, and the second is the one that matters most:

1. `PP_CAP_PER_RUN = 300` is applied **per cohort tag**, inside `for tag in _recent_cohorts(cur)`
   with `LOOKBACK_WEEKS = 3` — so the name lies by roughly 3×, and it is a coverage cap either way.
2. 🔴 **The caller sorts `sorted(shop)` — lexical, not the oldest-need-first the docstring
   promises.** With a per-tag cap, **the same first 300 order numbers are re-polled every run,
   forever, and the tail is never reached.** This is the exact shape P12 exists to kill: a job that
   reports a healthy-looking number while a fixed subset of boxes is never checked once.
3. The run guard only fires on `not out and errors >= asked * 0.5` — it cannot see throttling at
   all, and a half-empty run passes it.

The directive for the owning session is
`_outputs/reports/2026-08-20-pp-p12-directive-sync-delivery-status.md`. Prefer a **wall-clock budget
with a persisted resume queue** (stop dispatching after ~45 min, resume next hour, oldest-need-first)
over any call cap — the real constraint there is not ParcelPanel but
`server/ingest_worker.py:263-293`, a single-threaded ordered REGISTRY where `delivery_status_sync` is
index 8 on a 1-hour timer with `invoice_ingest` / `weather_fetch` / `prewarm` queued **behind** it,
and a `freshness()` that raises when the newest `synced_at` is over 3h old.

#### Verification

- **`ShipRouting/server/tests/test_pp_ratelimit.py` — 18/18 PASS.** Rates are **measured** on a
  virtual clock, not asserted: worst 60 s window **110** (≤ 120), sustained **~100/min**, work time
  proven not to leak into the pacing (a 0.5 s request does not slow the cycle), in-flight peak ≤ 8
  under 24 threads, brake fires at `remaining = 5` and **does not** fire at 20, 12 threads building
  their own clients get **one** limiter, and an always-429 order is answered 0 times, retried
  exactly 6, and never counted as served.
- **`ShipRouting/server/tests/test_phase0_pp_cursor.py` — 8/8 PASS**, including a replay proving
  every gap row is reached within one rotation with no row polled twice before the tail.
- **`AppyHour/GelPackCalculator/tests/test_pp_p12_call_sites.py` — 10/10 PASS** (client level: one
  limiter per key, a throttled order absent from results, transport fault counted apart from
  throttling, run guard throws on a storm, 404 still an ordinary empty answer).
- Whole `GelPackCalculator/tests` suite green (14/14); all eight touched modules import clean.
- 🔴 **NOT VERIFIED — no live ParcelPanel traffic.** No Python path was run against the real API in
  this pass. The first real numbers arrive in the `[pp] asked … served … throttled … exhausted …`
  line and `min x-ratelimit-remaining`, which should sit near 20 of 120.
- 🔴 **NOT VERIFIED — Transport B**, unchanged and still flag-off (above).

## Alert classes (Kurt 2026-07-30)

**PING** — hard failures plus address/attempt issues:

| class | matches on `detail` | 6/29–7/20 n |
|---|---|---:|
| `UNDELIVERABLE` | "unable to be delivered", "cannot be delivered", "undeliverable" | 15 |
| `DAMAGED` | "has been damaged", "merchandise has been discarded" | 8 |
| `RETURNED` | "returning package to shipper", "returned to a Veho warehouse", "returned to the sender" | 7 |
| `NEVER_PICKED_UP` | status `info_received` w/ no pickup_date after 24h of ship date | 3 |
| `LOST` | "lost by driver", "will be discarded" | 2 |
| `ADDRESS_ISSUE` | "need additional information to complete your delivery", missing access code | 3 |
| `ATTEMPT_FAILED` | attempted delivery, unable to complete delivery, recipient/business closed, **or PP `status = FAILED_ATTEMPT`** (display: "delivery attempt failed") | 1 |
| `ADDRESS_ISSUE` (widened 8/17) | + "incorrect address", "address is invalid", "delivery exception … address" | — |
| `DELAYED` (new 8/17) | Shopify `displayStatus = DELAYED`, ≥ `EXC_DELAYED_MIN_DAYS` (3) since fulfillment (display: "delayed / stuck in transit") | — |

**SUPPRESS** — never ping:

| class | why |
|---|---|
| event says delivered | 23/71 of the exception bucket. Kurt's "they just changed the label" case. |
| in-network scans | "Arrived at Veho facility", "On FedEx vehicle for delivery" — normal movement. |
| any (order, class) already alerted | dedup, constraint 3 |
| **`DELAYED`** (record-only, Kurt 2026-09-02) | **Not actionable.** Nobody can do anything about "your package is delayed" except wait, so it is noise in a channel Dan reads for boxes that need a HUMAN. Still classified, still written to the Exceptions tab — only the Slack post is cancelled. |

### 🔴 RECORD-ONLY vs SUPPRESSED — not the same thing (Kurt 2026-09-02)

`EXC_RECORD_ONLY_CLASSES` (`Exceptions.gs`, next to `EXC_PING_DAYS`) is a **caller-side** gate,
and every part of that placement is load-bearing:

- **The gate is NOT in `excClassify_`.** The classifier still returns `DELAYED` with `ping: true`.
  Muting it inside the classifier would DELETE the signal — the tab record, the self-test case at
  `excSelfTest`, and any future consumer (the Klaviyo flow among them) all still need the
  classification. Record-only means *the ping is cancelled*, not *the class stops existing*.
- **It sits ABOVE the seeding and dry-run branches**, so no mode can post a record-only class.
- **It sits ABOVE the Mon/Tue gate**, and that is the subtle one: the Wed–Sun gate *defers* a ping
  to Wednesday by deliberately leaving `alerted` unstamped. A record-only class routed through
  that path would accumulate all week and then post on Wednesday — the exact opposite of the
  intent. This branch returns first.
- **`alerted` is left untouched; tab-write dedup rides `logged`.** Nothing was alerted, so nothing
  claims to have been. That is the same `alerted`/`logged` split the Mon/Tue branch already uses.

To mute another class, add it to the map — do not touch the classifier, and do not reach for
`ping: false`.

**Measured volume (replay of the 71 real 6/29–7/20 boxes, 2026-07-30): 40 PING / 31 SUPPRESS
across 4 ship weeks ≈ 10 pings/week.** Higher than the 6–7 first estimated, because the replay
surfaced carrier phrasings the first pass missed (below). Class split: UNDELIVERABLE 16 ·
DAMAGED 8 · RETURNED 7 · LOST 3 · NEVER_PICKED_UP 3 · ADDRESS_ISSUE 2 · ATTEMPT_FAILED 1.

🔴 **Carrier phrasing varies far more than it looks — always replay before trusting a bucket.**
The first pass suppressed **5 genuine failures (14% of the suppressed set)** because the patterns
were written from one sample each: "returned to the **seller**" (not sender), "unable to
**deliver**" (not "to be delivered"), "unable to **locate** your package", and a bare
"Delivery exception, **Damaged**, handling per shipper instructions". Adding a carrier, or seeing
a new phrasing, means re-running the replay — not eyeballing the regex.

---

## Cadence

### 🔴 WEEKLY RHYTHM — tab every day, Slack Wed–Sun only (Kurt 2026-08-10, committed to Dan)

| | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---|---|---|---|---|---|---|
| **Exceptions tab** | record | record | record | record | record | record | record |
| **Slack #exceptions** | silent | silent | ping | ping | ping | ping | ping |

Mon/Tue are labels-created-nothing-moving days — pinging them is noise. Those exceptions
**accumulate silently and post on WEDNESDAY if still live**.

🔴 **The day gate must be in TWO places, and the sweep one is the load-bearing half.**
`excSlackPost_` only suppresses the HTTP call; the sweep stamps `rec.alerted` *after* that call
returns. Gating only the post path would mark an exception alerted with nothing ever posted, and
Wednesday would skip it — **silently swallowed, the exact failure this job exists to prevent.**
So the sweep checks `excPingDayET_()` first: on Mon/Tue it records the row, leaves `alerted`
untouched, and returns. `excSlackPost_` keeps its own gate as belt-and-braces for any future
call site.

Day-of-week is evaluated in **ET** (`Utilities.formatDate(..., 'America/New_York', 'EEE')`), not
the script timezone, so a late-evening run cannot land on the wrong side of midnight.

Ping classes are unchanged — not-picked-up, damaged, weather delays. The rhythm is a *day* gate,
not a class filter.

### Base schedule

- **Hourly**, on the host sheet's existing time trigger (Dan accepted hourly as the fallback;
  Kurt: *"I'll look into that, if not, fallback to hourly status checks"*).
- **Webhooks: investigated 2026-07-31, NOT adopted as the primary mechanism.** ParcelPanel does
  support outbound webhooks v2 (HMAC-SHA256 signed with the API key, topics incl.
  `shipment_status/exception` and `/failed_attempt`, 5 retries with backoff).
  🔴 **Subscribing to the exception topics would miss 38% of real failures.** Measured on the 71
  real boxes: **15 of the 40 ping-worthy failures carry `substatus = InTransit_001`** — 11
  undeliverable, 3 damaged, 1 lost — i.e. the carrier posted a failure event while PP's status
  still reads "in transit". No exception topic ever fires for those.
- 🔴 **PP `substatus` is unusable as a classifier for our carrier mix — the detail TEXT is the
  authority.** `Exception_007` alone spans 22 already-delivered, 4 returned, 3 undeliverable and
  1 lost. `Exception_004` is documented as "Address issue" but carries damaged and lost in our
  data. Do not "simplify" the classifier to substatus codes; this was tried and measured.
- The webhook body also carries **no `checkpoints` array**, so even a webhook-driven design needs
  a follow-up `GET /tracking/order` per event to obtain the text it must classify on.
- **Therefore hourly polling stays primary.** A webhook on exception/failed_attempt may be added
  later as a low-latency supplement for the subset it does catch, but it can never be the only
  path. The alternative — subscribing to `any_update` — fires on every scan of every box
  (~2,100 boxes/cohort), which no Apps Script endpoint should absorb for ~10 real events a week.
- **Poll set = OPEN boxes only.** An order leaves the poll set once it is delivered or has been
  alerted. Seeded from the current `_SHIP_` cohort; by mid-week the open set is small.
- Apps Script, not a local Python job, **because Kurt's machine sleeps 06:00–08:00** and an hourly
  local task would go dark. Cloud execution has no such gap.

## Message shape

One Slack message per (order, class). Must carry: order #, customer name, carrier, destination
state, the class, the **verbatim PP `detail` text**, and a Shopify order link. Verbatim text is
non-negotiable — it's what lets Dan judge in 2 seconds whether it's real without opening anything.

## Verification

Done (2026-07-30, `Exceptions.gs` replayed under node against live PP payloads):
- ✅ `excSelfTest()` — 20/20 cases.
- ✅ Replay of the 71 real 6/29–7/20 boxes: **40 PING / 31 SUPPRESS**, and **0** of the 23
  already-delivered boxes in the PING set.
- ✅ Audit of the suppressed set for missed failures: **0 remaining leaks** (was 5 before the
  phrasing widening).
- 🔴 **Ordering trap, caught by the self-test:** `/\bdelivered\b/` matches "unable to be
  **delivered**". Testing the delivered-suppress before the failure classes silently swallowed
  UNDELIVERABLE — the largest ping class. Failure classes are tested FIRST; do not reorder.

Still to verify on the live host:
- A test post lands in `C0BLKKPAW8P` and nothing lands in #reships.
- Two consecutive runs on the same open box produce exactly one message.
- The host sheet's `SLACK_BOT_TOKEN` is `appyhouropsreader` (`U0BG153RTNW`) — verified in the
  channel with `chat:write` + `groups:read` + `groups:history`. The Apps Script property has NOT
  been confirmed to be that same app.

### P14 — A FAILURE COUNT IS NOT A DIAGNOSIS (2026-08-24)

🔴 **NEVER report a ParcelPanel failure as a bare tally.** `excPpFetch_` counted every non-200 into
`out.failed` and then threw the status code away, and the `catch (err)` around `UrlFetchApp.fetchAll`
discarded the exception object without ever logging it. The two are indistinguishable downstream: a
**returned** 401/403 and a **thrown** transport error ("Address unavailable", a urlfetch quota wall)
increment the SAME counter, and neither leaves a trace.

**The burn.** On 2026-08-24 the sweep posted five identical hourly alarms:

> `exceptions sweep FAILED: ParcelPanel fetch failing: 400/400 hard failures (throttled: 0, dead
> records excluded: 0) — results suppressed rather than reported as all-clear`

Everything about that message is true and none of it is useful. `400/400` is not a status code — it
is `EXC_PP_MAX_PER_RUN` attempted and all of them failed. From outside the runtime it was not
possible to tell whether ParcelPanel refused the key, Cloudflare blocked Google's egress, or
`fetchAll` never got a response at all. Diagnosis burned an incident and still could not name the
cause, because **the evidence was destroyed at the point of failure**. Independent probes proved only
what it was NOT: the API was up (HTTP 200 with full payloads for five of the sweep's own orders), the
key in `.env` was valid, an unknown order returned a clean 404 (which classifies as `dead`, not
`failed`), and Apps Script's exact User-Agent was not blocked.

#### The rules

1. **Every non-200 records its status code** into a histogram, and the FIRST one records a truncated
   body sample. Both ride into the Slack alarm and the execution log (`excPpNote_` / `excPpDiag_`).
2. **A thrown fetch is never swallowed.** `catch (err)` logs `err` and records it separately from
   returned statuses (`out.threw` / `out.throwMsg`). "It threw" and "it answered 403" are different
   incidents and must never share one number — the same principle P9 established for dead records
   and P13 for throttling. This is the third time fusing two conditions into one counter has cost a
   diagnosis; stop doing it.
3. **🔴 A blanket wall is abandoned, not spent.** If the first two full batches produce nothing but
   failures — no 200, no 404, no 429 — the run stops and reports the remainder as `abandoned`.
   Pre-P14 it spent all 400 calls and four minutes proving the same point, every hour, against an API
   refusing every request.
   **This is NOT a ration and must never become one (directive P12).** It arms only at a 100% failure
   rate, a single successful answer anywhere disarms it (`seen_any_ok`), and abandoned orders are
   left UNSTAMPED — so they stay at the head of the longest-unpolled ordering and are the next run's
   first work. If it ever fires on a run that had even one good answer, that is a bug.

#### What is still open

- **The 2026-08-24 root cause is NOT established.** The failures began abruptly at 04:25 ET on 08-24,
  two days after the P12/P13 deploy and after ~2 days of the limiter working correctly (real pings
  posted 08-21 06:57 through 08-22 22:21). A mechanical diff proves P12 changed neither the request
  URL, the headers, `muteHttpExceptions`, the `fetchAll` shape, nor the non-200 classification. The
  perfectly constant `400/400` across five consecutive hours points at a deterministic per-request
  rejection rather than a quota or a race.
- **First thing to check, and it needs no push:** the sheet menu → *Exceptions* → **Check properties**
  prints whether `PARCELPANEL_API_KEY` is set and its character length WITHOUT printing the value.
  The working key is a 36-character UUID. A different length means the Script Property and `.env`
  have diverged, which reproduces this signature exactly — a wrong key returns
  `401 {"errors":"[API] Invalid API key or access token"}`, which lands in `failed`, with
  `throttled: 0` and `dead: 0`. Script Properties are not readable from outside the runtime, which is
  why this check cannot be done for you.
- P14 does not by itself restore the sweep. It guarantees the NEXT failure names its own cause.

#### P14 post-mortem verdict (2026-08-26): RECOVERED BEFORE P14 EVER RAN — cause never named

Read on 2026-08-26 from the ops DM (`D0BG1541F0A`) and `_exc_state`, after P14 deployed:

- **The outage was FIVE runs, not two days.** The DM holds exactly five `400/400` FAILED alarms —
  2026-08-24 16:25, 17:26, 18:25, 19:25, 20:25 **CST** (5:25–9:25pm ET) — and nothing after. The
  "began abruptly at 04:25 ET" above is a 12/24-hour + timezone misread of the first alarm's
  `16:25 CST` stamp; the DM is the artifact. `_exc_state` corroborates: `last_seen` stamps landed
  on 08-24 before and after that window (1,507 that day), 178 on 08-25 (Tue thin sweep), 733 on
  08-26 by ~12:25 CST — the 21:25 CST run on 08-24 was already answering again.
- **No P14-instrumented run ever saw the failure.** P14 (`18522698f067`) deployed 08-26; the last
  failing run was 08-24 20:25 CST. The histogram/body-sample/`threw` evidence for THIS incident
  does not exist anywhere, and nothing retroactively creates it. Verdict: **recovered, cause never
  named** — a ~5-hour deterministic per-request rejection (PP-side incident is the surviving
  hypothesis; the key was proven valid and the API reachable by independent probes the same night).
  P14's guarantee is prospective only.

### P15 — A DOCK-MISS IS ONE EVENT, NOT N CUSTOMER PINGS (Kurt GO, 2026-08-26)

> Kurt, on the ~660 `_SHIP_2026-08-24` Swedesboro boxes with zero pickup scans crossing
> `EXC_NEVER_PICKED_MIN_DAYS = 3` on Thu 08-27, inside the Wed–Sun ping window:
> **"i don't want another stream of exceptions though."**

**The failure this prevents.** NEVER_PICKED_UP is the one class whose real-world cause is usually
COLLECTIVE — a dock that wasn't swept strands hundreds of boxes at once — while the alerter's unit
is the box. Unmodified, the 08-27 crossing posts ~600+ individual pings into #exceptions across 2–4
hourly runs (each one `chat.postMessage` + an `appendRow`), which is precisely the flood that gets
the channel muted, and a muted #exceptions is the failure this whole job exists to prevent. A
mid-flood Slack 429 would also throw out of the posting loop AFTER `excLog_` rows landed but BEFORE
`excSaveState_`, losing the `alerted` stamps and re-pinging the same boxes next run.

**The rule.** NEVER_PICKED_UP verdicts are deferred within the run and flushed grouped by hub
(`excNpuFlush_`). Per hub, per run:

- **`count < EXC_NPU_COLLAPSE_MIN` (25): per-box behavior, verbatim** — ping-day post + tab row +
  `alerted` + closed; Mon/Tue tab row + `logged`, alerted untouched.
- **`count >= 25`: ONE alarm** through `excSlackPost_` (hub, count, carrier split, cohort tags,
  8 sample orders) + **ONE summary tab row** (order cell `(N boxes)` — never '#'-prefixed, so it
  can't be joined as an order; carrier cell carries the split verbatim). Every member is stamped
  `alerted` + closed in `_exc_state` — covered by the hub alarm, no per-box posts, no per-box rows.
  On Mon/Tue the group writes ONE summary row + per-box `logged` stamps (state only), `alerted`
  stays untouched so the first ping day still alarms once; the summary row dedups on those same
  `logged` stamps so hourly record-only runs cannot re-append it.

**Why 25:** normal weeks produce 1–15 never-picked stragglers and the worst observed single run
posted ~10 (08-19 21:46–23:46); the flood class is 500+, and a single missed pallet is ~50+ boxes.
25 clears every observed straggler run with headroom and every real dock-miss lands far above it.

**Hub attribution fabricates nothing.** ~~It costs nothing~~ — 🔴 **superseded by P16: it cost
bytes, and bytes are the budget that binds this project.** The `tags` field was originally harvested
by adding it to the Shopify request `excResolveDelivered_` already makes (the P5a/P9
one-request-many-jobs pattern — zero extra *calls* anywhere, which was true and was the wrong
meter). It now comes from a targeted cold-path lookup (`excHubsForOrders_`) over only the boxes
being alarmed. The PARSE is unchanged and still authoritative: hub is read from the order's LIVE
routing tags (`... - <Hub>_AHB!`).
Assignment tags outrank `!NO` fences; several surviving hubs read `(multi-hub tags)`,
no routing tag reads `(unknown hub)` — grouped honestly, never guessed (never-fabricate applies to
hub attribution). Hub is re-derived each run and NEVER persisted — `_exc_state`'s schema is
untouched, and a corrective retag moves a box's attribution instantly (the D17/D37 lesson).

**What P15 deliberately does NOT change:**

- 🔴 **Classification still requires the PP poll per box** (two-feed doctrine). "Classify the flood
  off the local `delivery_status` feed + a sample of live probes" was considered and REJECTED: the
  sweep runs in Apps Script, which cannot reach that store, and NEVER_PICKED_UP is defined as BOTH
  feeds silent — a hub-level signal is not license to mint per-box verdicts from one feed. The cost
  is bounded and already paced: ~1,300 due boxes on the crossing day ≈ 2–4 runs at
  `EXC_PP_MAX_PER_RUN`/`EXC_TIME_BUDGET_MS`, inside P12/P13 discipline, no quota to protect (P12).
- The dead-record bookkeeping, the limiter, the blanket wall (P14), and every dedup key: the hub
  alarm stamps the same `(order, NEVER_PICKED_UP)` `alerted` token the per-box ping would have.
- 🔴 The threshold is **PER RUN** (the alerter's natural unit). A flood wider than one run's cap
  spans 2–4 hourly runs and emits one alarm per run with the residual count — bounded and
  informative. Do not "improve" this into a per-day dedup that hides the residual counts, and do
  not lower the threshold toward the straggler band (every alarm below ~15 is channel noise again).
- `excSeedBacklogAsLogged` remains the manual full-mute lever; P15 is the automatic, targeted one.

**Verification (2026-08-26):** vm replay of the deployed bytes (`p15_test.js`) 22/22 — one
post/one row at ≥25 with alerted+closed on every member, per-box verbatim below 25, mixed hubs
split correctly, record-only day writes one row + `logged` only, repeat record-only runs append
nothing, the first ping day after a record-only flood alarms exactly once; `excSelfTest` 25 cases
PASS under node (hub parsing: assignment beats fence, lone fence names its hub, multi-fence reads
`(multi-hub tags)`, no tag reads empty); `node --check` ×5 + concat-of-4 clean; collision sweep
0 duplicates.

### P16 — "ZERO EXTRA CALLS" IS THE WRONG METER; THE BUDGET IS **BYTES**, AND IT IS GOOGLE'S (2026-08-31)

**The burn.** At 14:20 ET the hourly sweep died and posted:

> `exceptions sweep FAILED: Exception: Bandwidth quota exceeded: https://504ac4.myshopify.com/admin/api/2026-04/graphql.json. Try reducing the rate of data transfer.`

The whole sweep died — no ParcelPanel polling, no exception alarms, nothing — for one hour. Two
things were wrong, and the alarm text actively encouraged both mistakes.

🔴 **MISTAKE 1: it is NOT Shopify throttling us.** The message names a Shopify URL and says "rate",
so it reads as Shopify's leaky cost bucket. It is not. `UrlFetchApp` **throws** this when the SCRIPT
OWNER's **daily url-fetch data-received quota** runs out; the Shopify URL is merely what was in
flight. Two independent proofs:

- **Measured the same day** against live Shopify: the sweep's own queries cost `actualQueryCost`
  **20** (resolve) and **35** (seed) against a **4,000**-point bucket restoring at **200/s**. Observed
  `currentlyAvailable` never dropped below **3,965**. Shopify throttling is not remotely in play.
- **Structural**: a real Shopify GraphQL error surfaces through `shopifyGql_` as
  `Error: [{"message":…}]` (it throws `JSON.stringify(d.errors)`). The alarm read `Exception: ` +
  prose + the URL — the Apps Script platform-throw shape, identical to the documented
  `Exception: Address unavailable: <same url>` precedent that `muteHttpExceptions` never sees.
  Shopify never formats errors that way and never echoes the request URL.

🔴 **Never chase Shopify's cost bucket off this message.** Tune BYTES and CALLS. Shopify's cost is
now logged every run anyway (`shopifyIoSummary_`) so the question is answerable with a number.

🔴 **MISTAKE 2 (the one that actually mattered): an ENRICHMENT took down the ALARM.** The sweep's job
is telling Kurt a box is stuck. It died on `excSeedCohort_` — a *discovery* pull — while `_exc_state`
already held every open box with its cohort and could have been swept normally. Now: the seed and
the cohort-scope probe are **fail-soft**. A Shopify failure costs the discovery of newly-added
orders and posts a loud ops note saying so; it never costs the alarms. Only "no stored scope at all"
still refuses to sweep.

**Was P15 the cause? Partly, and not in the way the alarm implied.** Measured on the same 100 orders,
adding `tags`:

| | requestedQueryCost | actualQueryCost | response bytes |
|---|---|---|---|
| pre-P15 | 101 | 20 | 6,692 |
| P15 (`tags`) | 101 | 20 | **15,577 (+132.8%)** |

P15 added **exactly zero** query cost and **+133% bytes** on the hot path — every open box, every
hour. Its report ("zero extra API calls") was true and measured the wrong thing. But P15 is a
**minor** contributor, ~**+2.56 MB/day** of a sweep drawing ~**24 MB/day** from Shopify. The
dominant term is `excSeedCohort_`: **798 KB/run — 19.16 MB/day** re-paginating **5,186 orders every
hour** to discover the few that are new.

**Ruled out with evidence, not assumed:**

- **Our own session's Shopify load** (bulk order lookups, tag reconciles). Structurally impossible:
  the Apps Script url-fetch quota is charged to the script owner's Google account for Apps Script
  executions. Local Python/MCP calls cannot touch it — they draw on Shopify's bucket, measured
  healthy. **The victim/cause framing does not apply here.**
- **First occurrence ever.** A workspace-wide Slack search for "Bandwidth quota exceeded" returns
  exactly **one** message. Every prior `sweep FAILED` (8/07, 8/20, 8/24) was ParcelPanel. It has not
  recurred — Google's quota resets on a daily cycle.

**Fixes (this commit):**

1. **Fail soft.** `excSeedCohort_` wrapped; `excCurrentCohortTag_` falls back to the stored ratchet
   property. Alarms survive a Shopify outage or a byte wall. *This is the blast-radius fix and it
   matters more than the tuning below.*
2. **Instrument the meter.** `shopifyGql_` now counts response bytes/calls and keeps
   `extensions.cost` (previously parsed and thrown away). `shopifyIoSummary_()` is logged every run
   and attached to the failure alarm. **You cannot manage a cost you do not measure** — this number
   did not exist on 8/31.
3. **Name the right system.** `shopifyQuotaWall_()` classifies the wall so the alarm says
   *Apps Script daily data quota, NOT Shopify* — it sent this investigation at the wrong meter once.
4. **Cut bytes, keep behaviour.** Dropped the **unused `id`** from the seed (never read; **10,250 B
   per 250-order page, 26.6% of it — 5.10 MB/day**) and moved `tags` off the hot path to a targeted
   `excHubsForOrders_` over only the boxes being alarmed (**2.56 MB/day**; a 40-order flood costs
   ~11.5 KB once). **~7.7 MB/day, ~32% of the sweep's Shopify draw, with no feature lost.**

🔴 **Do NOT retry a quota wall.** `netRetryable_` deliberately excludes it: a DAILY byte budget does
not refill in seven seconds, and retrying spends more of the thing that just ran out.

🔴 **Standing rule for this project: a field list on a paginated hourly job is a byte budget.** Never
add a field because it costs no extra call, and never leave a field nothing reads. Both were true
here and both were expensive.

**Open / unmeasured:** ParcelPanel's byte draw — **9,600 calls/day** (400 × 24) — is almost certainly
the largest single consumer of the account's daily quota, and its response size is **not measured**
(the PP key lives only in Script Properties, not locally). Sizing it is the next step before any
further tuning; the per-run byte log added here will report it once deployed. Also unaddressed: the
seed still re-pulls both full cohorts hourly. A watermark (`updated_at`, not `created_at` — a
drift-in is tagged *after* creation) would remove nearly all of the remaining 19 MB/day, but it
changes which orders get discovered and is a **Kurt decision**, not an agent edit.

**Verification:** `node --check` ×5 + concat-of-4 clean; collision sweep 0 duplicates across 438
top-level globals; all new symbols resolve (`Exceptions.gs` → `Code.gs`, the existing
`shopifyGql_` dependency shape — **both files must deploy together**). Cost/byte figures above are
live measurements, not estimates.

## Known gaps (v1)

- **Returned-to-origin reads as delivered.** Order 154810 (FedEx, dest AL) shows
  "Delivered, Lebanon TN" — delivered back at the origin hub, not to the customer. v1 suppresses
  it. Catching this needs the event location compared against the destination state.
- **Mid-transit exceptions that later deliver are still invisible** — that's the `delivery_status`
  schema gap tracked in `.claude/plans/2026-07-30-delivery-exception-visibility.md` (item 2),
  not something this alerter can fix.

## Owner

Hourly trigger on the host sheet (Kurt's account) — same owner as the reship report. Per the
writer-ownership gate this is not shipped until that trigger exists AND a stale-run check covers
it; a silent alerter is indistinguishable from a quiet week.
