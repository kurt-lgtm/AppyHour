# EXCEPTIONS_ALERT_RULES.md — constraints SSOT for the #exceptions Slack alerter

🔴 **PRE-CHANGE GATE — read this file before touching the exceptions alerter.** Change the rules
HERE first, in the same commit as the code. Constraints were authored before the implementation
(feature-constraints-doc gate).

**Status:** APPROVED design, NOT yet built. Kurt decisions 2026-07-30 (Slack #exceptions thread
with Dan, screenshots in session). Sibling doc: `RESHIP_REPORT_RULES.md` (the host job).

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
7. **NEVER let a PP outage look like "no exceptions."** Zero findings on a run where the PP fetch
   errored must alert as a FAILURE, not pass silently as good news (fail-loud, per host job).
8. **NEVER widen the poll set to all cohorts.** Apps Script has a 6-minute execution ceiling and PP
   is one GET per order. Poll only OPEN boxes in the live cohorts (see below).

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
