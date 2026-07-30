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
5. **NEVER point this job at the live reship sheet.** Host sheet is `1kk1Qld-7QDIkhIKL93EIc6NGmOhnD_PZcJdTNX9m8Pg`
   (Kurt's copy). Dan's live pivot report is `1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU`.
   ⚠️ **A Sheets copy also copies the bound script**, so the copy ships with `PIVOT_SHEET_ID` still
   aimed at Dan's live report. Triggers do NOT copy, so nothing runs by default — but adding a
   trigger before repointing that constant would clobber the live report. **Repoint first.**
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

Expected volume: **~6–7 pings/week** (raw exceptions run ~7/week, 0.30% of cohort; the 07-06 spike
hit 17 = 0.70%, concentrated in Veho with 4 boxes frozen at one facility, Avenel NJ).

---

## Cadence

- **Hourly**, on the host sheet's existing time trigger (Dan accepted hourly as the fallback;
  Kurt: *"I'll look into that, if not, fallback to hourly status checks"*).
- **Open item:** whether ParcelPanel supports outbound webhooks for true push-on-status-change.
  If it does, upgrade and keep hourly as the backstop. Not a blocker for v1.
- **Poll set = OPEN boxes only.** An order leaves the poll set once it is delivered or has been
  alerted. Seeded from the current `_SHIP_` cohort; by mid-week the open set is small.
- Apps Script, not a local Python job, **because Kurt's machine sleeps 06:00–08:00** and an hourly
  local task would go dark. Cloud execution has no such gap.

## Message shape

One Slack message per (order, class). Must carry: order #, customer name, carrier, destination
state, the class, the **verbatim PP `detail` text**, and a Shopify order link. Verbatim text is
non-negotiable — it's what lets Dan judge in 2 seconds whether it's real without opening anything.

## Verification before this is called done

- Replay the 71 known 6/29–7/20 boxes through the classifier: expect 36 PING / 35 SUPPRESS,
  and specifically **0** of the 23 already-delivered in the PING set.
- Confirm a test post lands in `C0BLKKPAW8P` and nothing lands in #reships.
- Confirm two consecutive runs on the same open box produce exactly one message.

## Owner

Hourly trigger on the host sheet (Kurt's account) — same owner as the reship report. Per the
writer-ownership gate this is not shipped until that trigger exists AND a stale-run check covers
it; a silent alerter is indistinguishable from a quiet week.
