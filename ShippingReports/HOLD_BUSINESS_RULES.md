# HOLD_BUSINESS_RULES.md — hold tag taxonomy + lifecycle (Dan/Jess terms)

**Source of authority:** Kurt's Google Doc
(https://docs.google.com/document/d/11CiQcjctj5vATvfpqp-2NGRh0DCZQOe3wQ9R3qIqHJc/) — "Adjust the
tags that we use on Held Orders." Supplied by Kurt 2026-08-26 for the Hold tab documentation.
**The Doc wins on conflict; change terms THERE first, then mirror here in the same commit.**
Sheet-side mechanics (write-once, unfulfilled-only counting, row map) live in
[`RESHIP_REPORT_RULES.md`](RESHIP_REPORT_RULES.md) **D33**; reader guidance in
[`TAB_NORTH_STARS.md`](TAB_NORTH_STARS.md) → Hold.

## The three hold tags

| Tag | Meaning |
|-----|---------|
| `_CSHOLD` | Held by a CS agent |
| `_FLOWHOLD` | Held by Flow — 🔴 MUST be accompanied by a reason tag (below) |
| `_UNRESOLVED` | Terminal-ish: currently unable to resolve (see lifecycle) |

(`_HOLD` is the RETIRED legacy tag — migration completed 2026-08-26, non-cancelled count 0,
`HOLD_LEGACY_REGRESSION` tripwire alarms if it ever reappears. See D33.)

## `_FLOWHOLD` reason classes — a `_FLOWHOLD` with none of these is a defect

1. **Duplicate Subscription First Order** — same parent SKU, `SubscriptionFirstOrder`, within
   **10 minutes**:
   - Auto-ping immediately to the CS email inbox for follow-up.
   - `_FLOWHOLD` on **1 of the 2** duplicate orders — not both. Preference: release the
     CUSTOMIZED order, hold the other.
   - `_DUP_SFO_10MINS` marker on **BOTH** orders.
2. **Duplicate Parent SKU Order** — same parent SKU, `SubscriptionRecurringOrder` (NOT
   `Gift_Order`), within **1 day**:
   - Auto-ping CS inbox; `_FLOWHOLD` on 1 of the 2 (same customized-release preference);
   - `_DUP_SRO_1DAY` marker on **BOTH** orders.
3. **PO Box Order**:
   - Auto-ping CS inbox; `_FLOWHOLD` + `_POBOX` on the order.
4. **Product error** — add-ons came in WITHOUT a box (no `AHB-*` parent line item; e.g. an
   EX-/AC- add-on order with nothing to ship it in). Kurt 2026-08-26.
   - `_FLOWHOLD` + `_PRODUCT_ERROR` on the order.
5. **Staff order sweep** — order placed with an `@elevatefoods.co` email (internal orders held
   from the normal pipeline).
   - `_FLOWHOLD` + `_ELEVATE_FOODS` on the order.

🔴 The Hold tab's `_FLOWHOLD with no reason tag` row exists because of this rule — it should be
**0**. (It read 5 on 2026-08-25; open item.)

## `_UNRESOLVED` lifecycle

- An order moves `_CSHOLD`/`_FLOWHOLD` → `_UNRESOLVED` after **2 CS pings** AND the ticket is
  closed AND the order is **≥ 21 days old**.
- `_UNRESOLVED` is NOT an active hold — the Hold tab counts it separately (D33).

## Cancellation rules (subscription-level)

- **≥ 2 `_UNRESOLVED` orders on one subscription → cancel the subscription.** Specifically:
  - Duplicate/same-parent within 10 min + no response for 21 days + 2 `_UNRESOLVED` →
    **cancel ONE subscription** (the duplicate, not both).
  - PO Box order + no response for 21 days + 2 `_UNRESOLVED` → cancel subscription.
- Duplicate same-parent within 10 min + customer APPROVES the double →
  CS tags the customer **`2XSUBAPPROVED`** (no cancel; suppresses future dup-holds for them).

## Gotchas for any consumer of these tags

- 🔴 `_DUP_SFO_10MINS` / `_DUP_SRO_1DAY` sit on **both** orders of a pair — counting them counts
  the pair twice; the `_FLOWHOLD` itself sits on only ONE of the two.
- 🔴 A fulfilled order is NOT on hold, regardless of tag (Kurt 2026-08-26) — every open-hold
  count is unfulfilled-only (D33).
- The 21-day clock + 2-ping rule means `_UNRESOLVED` accumulation is SLOW by design; a young
  `_UNRESOLVED` (<21d order age) violates the terms and is worth flagging.
- `2XSUBAPPROVED` is a CUSTOMER tag, not an order tag.
