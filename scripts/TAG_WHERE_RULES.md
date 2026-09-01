# TAG_WHERE_RULES.md — Constraints SSOT for `scripts/tag_where.py`

> 🔴 PRE-CHANGE GATE: single source of truth for bulk predicate tagging. Change rules HERE first,
> same commit as any code change. Read BEFORE touching `tag_where.py`.

## 🧭 NORTH STAR

Bulk tag exactly the orders matching a stated predicate — previewed first, applied per-order via the
canonical read-modify-write path, never mangling any order's existing tag set.

## 🔴 GOTCHAS / NEGATIVES FIRST (each one has burned us)

1. **Shopify tags are a SET — never overwrite the whole tag string.** ([[shopify-tags-are-a-set-ice-lossy]])
   Every write is read-modify-write of the FULL current set: fetch current tags, remove only the
   specific `--remove` tags, append only missing `--add` tags, PUT the result. NEVER dedupe,
   re-order, trim, normalize, or "clean up" other tags in passing — a tag you don't understand is
   an operator's tag, not noise. Quantity is inexpressible in Shopify tags; the SHEET is ice
   authority — a Shopify-vs-sheet ice gap is EXPECTED, never reconcile it here.
2. **Tray/extra ice tags are ADD-ONLY.** ([[tray-ice-tags-never-remove]]) Any `--remove` target
   matching the ice pattern (case-insensitive contains `gel` or `ice`, e.g. `!ExtraGel24oz!`,
   `!ExtraGel48oz!`) is REFUSED unless `--force-ice-remove` is passed, which prints a loud warning
   before proceeding. Default path: hard error, exit 2, no writes.
3. **Never format-validate operator tags.** ([[multi-leg-shipweek-union-doctrine]]) Tag strings are
   OPAQUE. No regex sanity checks, no "that doesn't look like a _SHIP tag" rejections, no casing
   fixes. Matching is exact string equality, or prefix when the user passes `--prefix`. Nothing else.
4. **Line-item predicates use `active_line_items` ONLY.** (`AppyHourMCP/utils.py`) Raw
   `order["line_items"]` phantom-counts refunded/removed items — Shopify keeps them at original
   quantity. `--has`/`--lacks` evaluate net-quantity>0 items; the fetch MUST request
   `line_items,refunds` fields or the subtraction silently no-ops.
5. **Dry-run is the DEFAULT; `--apply` is the only write switch.** Preview prints every matched
   order with before/after tag sets. No `--apply` → zero writes, exit 0. There is no
   "yes to all" prompt — the preview run IS the confirmation artifact (live-writes.md restate gate:
   show count + tag identity, wait for Kurt's go, re-run with `--apply`).
6. **Never reimplement the write.** The per-order update is `tools.shopify.apply_tag_update` —
   the same function behind the `appyhour_update_order_tags` MCP tool (read-modify-write + cache
   bust). A second REST/GraphQL implementation is how tag writes drift.
7. **Delta log is append-only JSONL** at `Claude Projects/_outputs/logs/tag_where_<SHIP_TAG>_<date>.jsonl`
   (one record per applied order: order name/id, before, after, added, removed, ts). Never
   overwrite a prior log ([[never-delete-prior-output-files]]) — a rerun appends.
8. **Zero matches is a claim, not a result.** ([[feedback-join-zeroes-silently]]) On 0 matches the
   tool prints the cohort size fetched so a dead tag/typo'd SHIP_TAG is visible, not silent.
9. **🔴 A `--has`/`--lacks` SKU on ZERO cohort orders is a WRONG SKU, never an empty result.**
   ([[feedback-join-zeroes-silently]]) Live 2026-08-08 smoke test: `tag_where.py _SHIP_2026-08-10
   --has PR-CJAM` printed "0 of 2321 orders match", exit 0 — the real SKU is `PR-CJAM-GEN` and
   matching is EXACT. For `--lacks` it is far worse: a SKU that exists nowhere means every order
   trivially lacks it, so the predicate matches the WHOLE cohort and would mass-tag 2321 orders.
   The join-zero guard unions all cohort SKUs after fetch and REFUSES (exit 2, zero writes) if any
   predicate SKU appears zero times, printing prefix-overlap near-miss candidates.
   `--allow-absent-sku` is the deliberate escape hatch — never the default, never auto-added.
   Same guard, same flag name, in `remove_line_items.py`, `refund_batch.py`, `shorts_pass.py`.

## Contract

```
tag_where.py SHIP_TAG [--has SKU]... [--lacks SKU]... [--tagged T]... [--not-tagged T]...
             (--add T... | --remove T...) [--prefix] [--apply] [--force-ice-remove]
```

- Predicates AND together; repeated flags of the same kind also AND (`--has A --has B` = both).
- `--tagged`/`--not-tagged` match against the order's own tag set (exact, or prefix with `--prefix`).
- Fetch: all open unfulfilled orders on SHIP_TAG via `shopify_paginate` (cached `orders` tier, 10m).
- Apply phase: ThreadPoolExecutor(8) over `apply_tag_update`; per-order failures collected and
  printed, never swallowed; nonzero exit if any write failed.
- Idempotent: an order already in target state still passes predicates but produces a no-op write
  (add of present tag / remove of absent tag changes nothing).

## Non-goals

Not a tag search tool (use `appyhour_fetch_orders`), not a routing/ice applier (`apply.py` owns
routing tags, the sheet owns ice), not for vF sheets (`scripts/vf_tags.py` + VF_SHEET_RULES.md).
