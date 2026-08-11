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

## Alert classes (Kurt 2026-07-30)

**PING** — hard failures plus address/attempt issues:

| class | matches on `detail` | 6/29–7/20 n |
|---|---|---:|
| `UNDELIVERABLE` | "unable to be delivered", "cannot be delivered", "undeliverable" | 15 |
| `DAMAGED` | "has been damaged", "merchandise has been discarded" | 8 |
| `RETURNED` | "returning package to shipper", "returned to a Veho warehouse", "returned to the sender" | 7 |
| `NEVER_PICKED_UP` | status `info_received` w/ no pickup_date after 24h of ship date | 3 |
| `LOST` | "lost by driver", "will be discarded" | 2 |
| `ADDRESS_ISSUE` | "need additional information to complete your delivery" | 3 |
| `ATTEMPT_FAILED` | "delivery was attempted but could not be completed" | 1 |

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
