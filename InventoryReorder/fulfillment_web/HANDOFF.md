# HANDOFF — 2026-05-02

## What happened (2026-05-01 → 02)

Massive swap session for `_SHIP_2026-05-04` cohort + scoping work for swap UI extension.

### Swaps executed (all via MCP `appyhour_swap_order_skus` + custom python for count-limited)

| Swap | Cohort/Filter | Mode | Result | CSV |
|---|---|---|---|---|
| PK-FCUST → PK-BITESGUIDE | _SHIP_2026-05-04, box has TR- | paid | 7/7 ✅ | swap_results_2026-05-01.csv |
| PK-FCUST → PK-TCUST | _SHIP_2026-05-04, no TR- | paid | 237/237 ✅ (after 502 retry) | same |
| MT-CAPO → MT-COPPA | _SHIP_2026-05-04 | bundle | 167/168 ✅ (1 🔒 #134486) | same |
| MT-CAPO → MT-COPPA | leftover paid | paid | included above | same |
| CH-RP+AC-RHB → CH-BIX+AC-SRHUB | tag=BIX (multi-source) | paid | 239/239 ✅ | same |
| CH-FOWC → CH-CCC | _SHIP_2026-05-04 | paid | 9/9 ✅ | same |
| MT-JAHH → MT-PSS | _SHIP_2026-05-04 | bundle | 35/35 ✅ | same |
| MT-JAHH → MT-PSS | _SHIP_2026-05-04 leftover | paid | 7/7 ✅ | same |
| CH-BRIE+CH-EBRIE → CH-RP | _SHIP_2026-05-04 | paid no-exc | 126/130 (4 🔒) | same |
| CH-EBCC → CH-OGK | _SHIP_2026-05-04 | paid no-exc | 13/13 ✅ | same |
| CH-LEON → CH-BIX | _SHIP_2026-05-04 | paid no-exc | 10/11 (1 🔒 #134861) | same |
| CH-TIP → CH-EBRIE | _SHIP_2026-05-04 | paid | 1/1 ✅ | same |
| MT-BRAS → MT-SBRES | _SHIP_2026-05-04 | paid no-exc | 5/5 ✅ | same |
| **CH-ALP → CH-WMANG** | tag=ALP, suffix -HHIGH, **70 of 121 bundle** | bundle, count-limited | 70/70 ✅ | alp_chalp_swap_70_results.json |
| **CH-ALP → CH-SHADOW** | tag=XMOM, **100 of 167** paid | paid, count-limited | 100/100 ✅ (after 502 retry) | (in script output) |
| **CH-UCONE → CH-UROSE** | _SHIP_2026-05-04, **19 of 24** | paid, count-limited | 19/19 ✅ (1 🔒 #136545 backfilled w/ #136569) | (in script output) |

**Locked orders** (manual fix needed): #134486, #134535, #134861, #136418, #136434, #136545, #136784

**Custom python flow** (for count-limited): script in main session, used `shopify_swap.py` helpers (`find_swap_targets`, `lookup_variant_gid`, `execute_swap`, `execute_bulk_swap`). Saved selection to `alp_chalp_swap_70.json`.

### Skill updates

- **forge-swap** ([SKILL.md](C:/Users/Work/.claude/skills/forge-swap/SKILL.md)) — full rewrite. Removed broken `generate_swap_list` step, opus pre/post-flight agents (over-engineered, unused). Added: rc_bundle_only behavior nuance for direct-items cohorts (PR-CJAM/BIX/XMOM), ship_tag is generic tag filter, count-limited recipe using `shopify_swap.find_swap_targets`+`execute_bulk_swap`, retry idempotency, failure classification (🔒/502/other), backfill-on-locked. API version `2024-01`. Dropped `--recharge` flag (unimplemented).

### Scoping doc saved

[.claude/plans/2026-05-02-swap-ui-extension-scope.md](.claude/plans/2026-05-02-swap-ui-extension-scope.md)

**Key finding:** swap UI **already exists** at:
- `app.py:4325-5120` — 13 swap routes
- `templates/index.html:1046-1132` — `#swapmanager-view` panel
- `static/app.js:2399-2660` — JS logic
- `shopify_swap.py` — helpers

**Critical gap:** UI **cannot swap paid items** today. Every route calls `find_swap_targets()` which hardcodes `if "_rc_bundle" not in prop_names: continue` at [shopify_swap.py:112](shopify_swap.py:112).

### 7 extensions scoped (all needed per user)

Phase A — foundation (~5hr): **E2** rc_bundle_only toggle (HIGHEST), **E1** cohort tag dropdown, **E7** box_sku_contains substring filter
Phase B — selection (~5hr): **E6** suffix/substring SKU search (`*-HHIGH`), **E3** count limiter (N of M, oldest first)
Phase C — reliability (~3hr): **E4** failure classification + retry-transients + locked-backfill
Phase D — UX (~5hr): **E5** batch queue (5+ independent swaps)

Total ~18hr.

## Shipped (2026-05-02 → 03)

| Ext | Commit | What |
|---|---|---|
| E2 | `c97f412` | rc_bundle_only toggle — paid-item swap now possible |
| E1+E7 | `e306597` | cohort tag dropdown + box_sku_contains substring filter |
| E4 | `fa6a67f` | failure classification (locked/transient/other) + retry + backfill |
| E3+E6 | `201682c` | count limiter + wildcard SKU search (`*-HHIGH`, `TR-*`) |

## Resume directive

**NEXT ACTION:** E5 batch queue (5+ independent swaps stacked, dry-run all → execute all). Plan saved scope only — needs `/forge plan E5`. ~5hr. Lowest priority — every individual session pattern already works via existing UI.

**REMAINING:** E5 only.

## Context for resume

- Run command: `python app.py --browser` from `fulfillment_web/` → http://127.0.0.1:5187 → sidebar "Swaps"
- Auth: `inventory_reorder_settings.json` (`shopify_store_url`, `shopify_access_token`)
- Stack: Flask + pywebview, vanilla JS, custom CSS dark theme
- API: Shopify Admin `2024-01` (REST + GraphQL), bare prefix `504ac4` (helper appends `.myshopify.com`)
- shop_swap.py exports: `find_swap_targets`, `lookup_variant_gid`, `execute_swap`, `execute_bulk_swap`, `_gql`

## Caveats

- `appyhour_search_products` MCP tool is broken (`'NoneType' object has no attribute 'lower'`) — validate SKUs via swap dry-run instead
- 7 locked orders need manual editing (gift redemptions per memory)
- One-time CSV `swap_results_2026-05-01.csv` accumulated all swap results from session
