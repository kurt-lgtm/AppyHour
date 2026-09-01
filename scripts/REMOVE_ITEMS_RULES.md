# REMOVE_ITEMS_RULES.md — constraints SSOT for `scripts/remove_line_items.py`

> 🔴 PRE-CHANGE GATE: single source of truth for cohort line-item removal. Change rules HERE first,
> same commit as any code change. Read BEFORE touching `remove_line_items.py`.

## 🧭 North star

One canonical, guarded way to remove a SKU's line item(s) from every matching order on a
`_SHIP_` cohort — replacing the pile of dated one-shots (`remove_paid_addons_ship_20260810.py`,
`remove_mrjrnl_ship_20260810.py`, `remove_pk_from_tr_orders.py`, …) — with dry-run default,
paid-item refusal, append-only delta log, and a verify phase that proves the removal actually
landed. Removal is the LAST resort of the shortage flow; swaps (`shorts_pass.py`) come first.

## 🔴 Negatives first — what this tool must NEVER do

1. **Never write new GraphQL edit code.** Execution delegates to the EXISTING order-edit
   machinery in `AppyHourMCP/tools/order_edit.py`: `orderEditBegin` → `orderEditSetQuantity(0,
   restock:true)` → pre-commit unexpected-change verification → `orderEditCommit`, and its
   `_audit()` (swap_audit.jsonl) + `_paid_skus_on_order()` paid detector. A parallel edit path
   is how #157930 shipped short — the guards live in ONE place.
2. **Never remove a PAID line silently.** Removing a line the customer paid for is
   money-adjacent (wk0720 CH-IPRW violation: paid line removed with no refund). Default =
   REFUSE with the per-order actual-paid amounts printed. `--allow-paid` (Kurt's explicit OK)
   permits it and STILL prints actual-paid per order — because that amount is the refund owed.
   Refund execution is NOT this tool's job: hand the printed list to `scripts/refund_batch.py`
   (or the `_template_bulk_refund.py` flow) in the SAME work session. Paid detection uses
   `_paid_skus_on_order` — actual-paid > 0 OR catalog price > 0 (the Recharge-ONETIME $0-line
   trap, Kurt 2026-07-21 #163709).
3. **Never target off raw `line_items`.** Shopify keeps refunded/edited-out lines at original
   quantity — raw reads phantom-count removed food (MT-FS-BRAS burn). Targeting goes through
   `utils.active_line_items()` (orders fetched WITH `refunds`) plus `fulfillable_quantity > 0`.
4. **Never write without `--apply`.** Dry-run is the default and prints the full preview table.
   Every committed removal appends a delta row to
   `_outputs/logs/remove_line_items.jsonl` (append-only — never truncate/rewrite) AND to the
   shared `swap_audit.jsonl` via `order_edit._audit`.
5. **Never claim success from call counts.** Verify phase re-fetches per-order via REST
   (GraphQL aggregate is eventually consistent right after an edit) and confirms the SKU's
   fulfillable count matches plan (0, or plan residual). Zero-is-a-claim: before trusting the
   zeros, the verifier fetches a CONTROL order (a non-target on the tag) and proves the read
   path returns nonzero fulfillable lines — a fetch that zeros the control is INCONCLUSIVE,
   nonzero exit. Nonzero exit on any mismatch.
6. **Never touch fulfilled or cancelled orders.** Fetch scope = open + unfulfilled;
   cancelled orders are skipped explicitly.
7. **Never guess SKU identity.** CH-FONT ≠ CH-FONTAL. The preview table shows the SKU and
   per-order lines; Kurt confirms before `--apply`.
8. **🔴 Never report "nothing to do" for a SKU that is on ZERO cohort orders — REFUSE instead.**
   ([[feedback-join-zeroes-silently]]) Live 2026-08-08 smoke test on the sibling tool:
   `tag_where.py _SHIP_2026-08-10 --has PR-CJAM` returned "0 of 2321 orders match", exit 0,
   because the real SKU is `PR-CJAM-GEN`. Matching is EXACT, so a near-miss SKU reads as an empty
   cohort instead of a typo, and the operator concludes the removal was unnecessary. Before
   planning, `cohort_skus()` unions every live SKU on the tag (same `active_line_items` path as
   targeting, rule 3); if `--sku` is absent from that union the tool prints prefix-overlap
   near-miss candidates and exits 2 — no "nothing to do", no writes. `--allow-absent-sku` is the
   deliberate escape hatch for a genuinely-expected absence. Reinforces rule 7 (CH-FONT ≠
   CH-FONTAL): the guard is what makes a wrong SKU LOUD rather than a quiet zero.

## Contract

```
remove_line_items.py SHIP_TAG --sku SKU [--only-if-tagged TAG] [--allow-paid] [--apply]
```

- Inputs: cohort `_SHIP_` tag (verbatim, never format-validated — multi-leg doctrine);
  one SKU per run; optional extra tag filter (order must carry BOTH).
- Fetch: `AppyHourMCP/utils.py::shopify_paginate` (cached tier ok for planning; verify phase
  reads live per-order REST). Apply: ThreadPool(8), matching the MCP swap path.
- Output: preview table (order, email, qty, paid $, tags) → on `--apply`, per-order result +
  verify report; exit 0 only when every planned removal verified.

## Cross-references

- Swap-first doctrine + execution guards: `scripts/SHORTS_PASS_RULES.md`, `tools/order_edit.py`
- Refund pairing: `scripts/refund_batch.py` (paid removals owe a refund — run it, same session)
- Line-item reading rules: `shopify-line-items` skill; `utils.active_line_items`
- Registry row: `AppyHour/TOOL_REGISTRY.md` (order tools, 🔒✍️)
