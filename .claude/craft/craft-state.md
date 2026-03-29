# Craft State — Swap Manager

## Feature
Swap Manager page integrated into AppyHour fulfillment_web Flask SPA

## Mode
FULL

## Current Phase
Phase 9: Code Review (in progress, background agent)

## Plan Path
.claude/plans/2026-03-29-swap-manager.md

## Impact Brief
- 4 files created/modified
- Extends existing shopify_swap.py + 4 swap routes
- New: 7 multi-swap routes, JS view, HTML panel, sku_mappings.json

## Key Decisions
- Flask HTTP architecture (not pywebview bridge)
- Routes added directly in app.py swap section (no separate module)
- Reuse shopify_swap.py functions (find_swap_targets, execute_bulk_swap, lookup_variant_gid)
- Same _swap_progress / _swap_cancel background thread pattern
- sku_mappings.json as single source for NAME_TO_SKU

## Completed
- Phase 1.1: Quick Discover → FULL mode
- Phase 2: Deep Discover — app.py 8284 lines, app.js 5309 lines, Flask+SPA
- Phase 3: Plan v2 (post-critic) — extend existing, don't rebuild
- Phase 3b: Plan Critic — found existing shopify_swap.py + 4 routes
- Phase 4: Created sku_mappings.json (67 name→SKU, 10 variant GIDs)
- Phase 5: Added 7 new routes to app.py (~350 lines)
- Phase 6: Built UI — HTML panel + toolbar button + ~300 lines JS
- Phase 7-8: Verified — JSON valid, Python syntax OK, JS balanced, 116 routes load

## Resume Directive
Code review running in background. After review, fix any CRITICAL/HIGH issues.
Then Phase 10: Simplify + deliver.
