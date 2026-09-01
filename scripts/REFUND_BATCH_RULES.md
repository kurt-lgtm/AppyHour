# REFUND_BATCH_RULES.md — constraints SSOT for `scripts/refund_batch.py`

> 🔴 **PRE-CHANGE GATE:** single source of truth for batch refund execution — change rules HERE first,
> same commit as any code change. Read this BEFORE touching `refund_batch.py`.

🧭 **NORTH STAR:** refund exactly what each customer actually paid for the affected item — once, never
twice, never at list price — with every dollar that moves logged to a file before and after it moves.

## 🔴 Constraints (negatives-first — each one is a prior burn or standing rule)

1. **Refund = what the customer ACTUALLY PAID** — discounted line price + that line's tax share —
   **NEVER list/catalog price** (standing money rule; a refund was once staged at LIST price).
   Default `--amount-mode actual-paid` computes this per order from the SKU line. A fixed `--amount`
   is allowed but is still logged against actual-paid, and the tool **WARNS on every order where
   amount > actual paid** (over-refund) — review the dry-run before `--commit`.
2. **NEVER invoice a customer as refund remediation** — standing, rejected. This tool only moves
   money TO customers; nothing here may create charges/invoices.
3. **Idempotent by refund note** (template's proven mechanism): before issuing, the order's existing
   refunds are checked for the note keyword; matching orders are SKIPPED. Crash-safe: re-running the
   same command never double-refunds. Corollary: the `--note` must be distinctive per campaign —
   reusing an old note makes every order look already-refunded.
4. **Dry-run is the DEFAULT.** Money moves ONLY with `--commit`. Dry-run shows the full per-order
   amount table + total. Live-write gate applies: restate count + note + total, wait for go.
5. **Per-run artifacts, versioned, never overwritten:** every run writes
   `_outputs/logs/refund_batch_<UTCstamp>.log` and, on commit,
   `_outputs/reports/refund_batch_<UTCstamp>_moved.xlsx` (exactly what moved: order, amount,
   refund id). Timestamped names — never clobber a prior run's file.
6. **🔴 After ANY `--commit`, run the double-refund check:**
   `python InventoryReorder/Errors/detect_double_refunds_v2.py` — catches doubles the note-match
   can't (different notes, manual refunds).
7. **Order selection — two paths only:**
   - explicit xlsx/csv of order numbers (`--orders file`), template semantics (header row skipped,
     `#` stripped, non-digits dropped); or
   - `--ship-tag TAG --sku SKU`: cohort filter where SKU presence is counted via
     **`active_line_items()` (AppyHourMCP/utils.py) ONLY** — raw `line_items` phantom-counts
     removed/refunded lines (MT-FS-BRAS burn). Never hand-roll line-item reads.
8. **No fabricated amounts.** If the SKU line can't be found on an order in actual-paid mode, that
   order is flagged `MISSING` and skipped — never inferred from another order or catalog price.
9. **🔴 A `--sku` on ZERO cohort orders is a WRONG SKU, not an empty refund set — REFUSE.**
   ([[feedback-join-zeroes-silently]]) Live 2026-08-08 smoke test on the sibling tool:
   `tag_where.py _SHIP_2026-08-10 --has PR-CJAM` reported "0 of 2321 orders match", exit 0 — the
   real SKU is `PR-CJAM-GEN` and matching is EXACT. Money-adjacent, so this one matters more: a
   refund campaign that quietly refunds NOBODY is indistinguishable from one that ran clean, and
   the operator ticks it off. With `--ship-tag`, the cohort pass now also returns the union of all
   live SKUs on the tag; if `--sku` is absent from it the tool prints prefix-overlap near-miss
   candidates and exits 2 BEFORE any planning or `--commit`. `--allow-absent-sku` is the
   deliberate escape hatch. (The `--orders` path is unaffected — it already reports NOT FOUND per
   order number.) Same guard/flag in `tag_where.py`, `remove_line_items.py`, `shorts_pass.py`.

10. **🔴 `--ship-tag` MUST fetch with the SERVER-SIDE `tag` param via `shopify_paginate` — never a
    hand-rolled walk over `status=any` with no tag.** Live 2026-08-09, same cohort, minutes apart:
    `tag_where.py _SHIP_2026-08-10 --has CEX-EC` → **644 match**; `refund_batch --ship-tag
    _SHIP_2026-08-10 --sku CEX-EC` → apparently **1**. The old `fetch_by_tag_sku` sent NO `tag`
    param, so it paged the entire order history (~680 pages, 25+ min) filtering client-side.
    Instrumented replay proved the predicate was right — it reached 644 only at page 24 of 680 —
    so the tool agreed with `tag_where` **only if allowed to run to completion**. Any timeout,
    Ctrl-C, or rate-limit abort silently under-selects → **under-refunds, with no error**. It also
    split the `Link` header on `,` instead of the canonical `<([^>]+)>;\s*rel="next"` regex, which
    survives only because Shopify URL-encodes the `fields` commas as `%2C` — one un-encoded comma
    away from a crash. Use `shopify_paginate(..., resource="orders-live")` (money-adjacent read:
    never served from the 10m orders cache). Selection is now the pure, offline-tested
    `select_cohort()`; population deliberately stays `status=any` (refunds happen post-fulfilment)
    where `tag_where` uses open+unfulfilled — a **superset by design**, not a mismatch.
11. **🔴 A skip is not a non-match — reconcile every order that entered.** The 644 CEX-EC lines were
    all `price 0.00` **in-box components** ([[paid-signal-depends-on-the-question]]): selection hit
    all 644, and actual-paid correctly owed $0 on every one. But the tool printed 644 look-alike
    `MISSING` lines and a bare `0 orders` footer — the console tail showed one order (`#165495`) and
    the run read as *"the filter found nothing"* (a matching bug) instead of *"nobody paid for this
    SKU"* (the truth). Every run now prints a `SELECTION:` line (matched → planned, split by
    zero-paid vs SKU-line-missing), the same counters go into the log header, and an all-skipped
    run says so LOUDLY. Never diagnose a refund count from tail output alone — read the
    `SELECTION:` line.
12. **UTF-8 stdout is mandatory** — the 2026-08-09 fix crashed on its own em-dash under cp1252
    *after* selection completed, wasting a 3-minute live read. `sys.stdout.reconfigure` at import.

## Interface

```
refund_batch.py (--orders file.xlsx|file.csv | --ship-tag TAG --sku SKU)
                --note "REASON — refund issued" [--sku SKU] [--amount N]
                [--amount-mode actual-paid] [--commit] [--skip 118062,...]
```

Fallback reference implementation (mechanics lifted from):
`InventoryReorder/Errors/_template_bulk_refund.py`.
