# Swap UI Extension — Scoping (2026-05-02)

## Premise check

Swap UI already exists. Not a new build — extension.

**Existing scaffold:**
- `app.py` lines 4325–5120 — 13 swap routes (preview, execute, multi-preview, multi-execute, matrix-upload, recharge-sync, history, export-csv, ship-tags, tag-skus, progress, cancel)
- `templates/index.html` lines 1046–1132 — `#swapmanager-view` SPA panel
- `static/app.js` lines 2399–2660 — swap JS (previewSwaps, executeSwaps, syncRechargeSwaps, handleMatrixFile, pollSwapProgress, cancelSwap, addSwapPair, setSwapMode)
- `static/styles.css` lines 900–917 — status-badge CSS variants
- `shopify_swap.py` — helpers (`find_swap_targets`, `lookup_variant_gid`, `execute_swap`, `execute_bulk_swap`)

**Stack:** Flask + pywebview hybrid, port 5187. Vanilla JS, no framework. Custom CSS, dark theme. Auth via `inventory_reorder_settings.json`.

**Run:** `python app.py --browser` → `http://127.0.0.1:5187` → sidebar "Swaps".

## Gaps vs this session's 5 patterns

| Pattern (session) | Existing UI? | Gap |
|---|---|---|
| 1. Single bulk swap (PK-FCUST→PK-BITESGUIDE w/ TR- filter) | partial | No box_sku_contains UI; no rc_bundle_only toggle |
| 2. Multi-source merge (CH-BRIE+CH-EBRIE→CH-RP) | ✅ multi-pair builder handles it | none |
| 3. Direct-items cohort (BIX/XMOM/ALP, paid-only) | ❌ | (a) ship-tags dropdown only shows `_SHIP_*`, no cohort tags. (b) `find_swap_targets` is bundle-only hardcoded — UI has no path to swap paid items |
| 4. Count-limited (70 of 122) | ❌ | No N-of-M selector. Would need: enumerate matches, sort, take first N, custom orderEdit loop |
| 5. Batch parallel (5 independent swaps queued) | partial | multi-pair handles same-cohort same-target pairs only. No "queue of N independent swap operations, dry-run all, execute all" UX |
| Retry transients (502) | unknown | Need to check `/api/swap_execute` error handling |
| Locked auto-backfill | ❌ | Locked orders silently fail. No "pull next eligible from pool" backfill |
| Failure classification (🔒 vs 502 vs other) | ❌ | Errors bucketed as `failed: N` — no breakdown |

## Recommended extensions (TIER 1)

**E1. Cohort tag selector** — extend `GET /api/swap/ship-tags` to optionally include non-_SHIP_ tags (BIX, XMOM, ALP, RMFG_*). Add a "Tag type" filter (Ship Date / Cohort / All) above dropdown.
- Files: `app.py:4459`, `app.js:2415`, `index.html:1050`
- Effort: 1-2 hr

**E2. rc_bundle_only toggle (HIGHEST PRIORITY)** — paid-item swap is currently impossible via UI. Must fix.
- Add `bundle_only: bool = True` param to `find_swap_targets` (default preserves current safe behavior)
- When `bundle_only=False`: skip the `_rc_bundle` check at line 112; still respect `fulfillable_quantity > 0`
- UI: checkbox "Include paid items (no exceptions)" — unchecked default. When checked: red warning text "Will modify customer-paid line items — confirm carefully"
- Confirmation modal before execute when paid mode + count >0
- Files: `shopify_swap.py:64`, `app.py:4330+4375+4511+4582`, `app.js:2517+2575`, `index.html:1064`
- Effort: 2-3 hr

**E3. Count limiter + sort** — input "Limit to first N orders (oldest first)" below preview. Slider 1→count, default = full count. Slices the targets list before bulk execute.
- Files: `app.js:2517` (slice after preview), `app.py:4575` (accept `limit` param)
- Effort: 1 hr (pure UI, helper already supports sliced list)

**E4. Failure classification + backfill** — change `execute_bulk_swap` errors return shape to `{locked: [...], transient: [...], other: [...]}` (parse `"cannot be edited"` → locked; `502/503` → transient). UI shows three buckets. "Retry transients" button. "Backfill locked" toggle (opt-in: when set + count was N, backfill from unselected pool to hit N).
- Files: `shopify_swap.py:221`, `app.py:4372+4575`, `app.js:2580+2620`, new CSS variants
- Effort: 3-4 hr

**E5. Batch queue (5+ swap operations)** — new "Queue" section above existing manual/matrix toggle. User stacks N swap configs (each = ship_tag + pairs + filters + limit). "Dry-run all" → table of per-row counts. "Execute all" → sequential or parallel.
- Files: `app.js` new module, `index.html` new panel, `app.py` new `/api/swap/batch-*` routes
- Effort: 4-6 hr

## E6 (PROMOTED to TIER 1) — Suffix / substring SKU search

This session ran "ALP orders with `-HHIGH` suffix SKU" — `find_swap_targets` requires exact `old_sku`. Add wildcard:
- OLD_SKU input accepts `*-HHIGH` (suffix) or `TR-*` (prefix) or `*BIX*` (substring)
- Helper expands wildcard via `productVariants(query: "sku:*-HHIGH")` → list of concrete SKUs
- UI shows resolved SKU list + count for confirmation before preview
- Files: new `find_skus_matching()` in `shopify_swap.py`, `app.py:4504`, `app.js:2517`
- Effort: 2-3 hr

## E7 (PROMOTED to TIER 1) — `box_sku_contains` substring filter

This session ran "PK-FCUST swap, only orders with TR- in box SKU" — UI has no equivalent.
- Optional input "Only orders containing box SKU substring(s)" (comma-sep)
- Wired through to MCP tool's `box_sku_contains` param OR add equivalent to `find_swap_targets`
- Files: `shopify_swap.py:64` (add `box_sku_contains: list[str] = None`), `app.py:4504`, `app.js:2517`
- Effort: 2 hr

## TIER 2 (nice-to-have)

- **Live preview hover** — hover an order# in preview table → show all line items
- **Saved presets** — name + persist a swap config for reuse next week (cohort+pairs+filters)

## TIER 3 (defer)

- Real-time updates via WebSocket (current 1s polling works fine)
- Multi-user concurrency lock (single-operator tool)
- Integration with `/forge-swap` skill (CLI parity)

## Order of attack (revised — all session patterns required)

**Phase A — Foundation (~5hr):** unlocks every cohort + filter combo this session ran
1. **E2** rc_bundle_only toggle — paid items reachable
2. **E1** cohort tag dropdown — BIX/XMOM/ALP selectable
3. **E7** box_sku_contains substring filter — TR- pattern reachable

**Phase B — Selection & resolution (~5hr):** count + suffix
4. **E6** suffix/substring SKU search (`*-HHIGH`)
5. **E3** count limiter (N of M, oldest first)

**Phase C — Reliability (~3hr):** failures
6. **E4** failure classification (🔒/502/other) + retry-transients button + locked-backfill toggle

**Phase D — UX (~5hr):** queue
7. **E5** batch queue (5+ independent swaps, dry-run all → execute all)

Total: ~18hr. Ship Phase A first — biggest unblock. Phases B–D incremental on top.

## Out of scope / non-goals

- New web framework (vanilla JS works fine for this volume)
- Auth changes (single-tenant settings.json is correct)
- Mobile responsive (desktop-only ops tool)
- Replacing Matrix upload (already works)
