# SHORTS_PASS_RULES.md — constraints SSOT for `scripts/shorts_pass.py`

> 🔴 PRE-CHANGE GATE: single source of truth for the weekly shorts→swap orchestrator.
> Read this BEFORE touching `shorts_pass.py`. Change rules HERE first, same commit as code.

🧭 **NORTH STAR:** resolve the week's shorts with zero phantom results and zero guardrail
violations, in one command.

## 🔴 GOTCHAS / NEGATIVES FIRST (each rule = a real burn)

1. **NEVER hand-roll order edits.** wk0810 hand-rolled loops around the low-level
   `execute_swap` primitive produced **34 phantom "successes"** (see
   `_archive/handrolled-shape-refs/wk0810-swaps/README.md`). ALL execution delegates to
   `AppyHourMCP/tools/order_edit.py` module functions — `_lookup_variant_gids` (cached,
   $0-preferring, batched) + `_swap_order_skus` (balance invariant removed==added or ABORT,
   pre-commit line-item snapshot verify, `swap_audit.jsonl` append, PAID-ITEM GUARD).
   Do NOT reimplement GraphQL beginEdit/addVariant/commitEdit anywhere else.
2. **Success is NEVER call-count.** `execute_swap`-style paths return `success:False`
   without raising; `_swap_order_skus` can raise or partially apply. The ONLY final
   evidence = re-count `fulfillable_quantity` of old/new SKUs on the tag AFTER execution
   and diff vs plan (memory: swap-success-count-is-not-evidence). shorts_pass exits
   nonzero on any plan-vs-actual mismatch.
3. **Count with `fulfillable_quantity`, never `quantity`.** Removed/zeroed lines still
   report original `quantity` — the wk0810 scripts also got this wrong. Both planning
   (via `find_swap_targets`) and verification count fulfillable only.
4. **Login-OR-customize guardrail (workspace CLAUDE.md).** Never swap a subscriber who
   LOGGED IN (Recharge `/events?verb=login` — `recharge-login-events` skill) OR
   CUSTOMIZED (Recharge bundle_selections on the subscription — a Shopify tag alone
   misses portal customizations). Either signal → EXCLUDED from the plan, with reason.
   A clean login scan alone does NOT clear a customer — both halves must be clean.
   **Login lookback = 30 days (`--login-window-days`), Kurt-confirmed 2026-08-09.** The guardrail
   itself states no window, so one had to be chosen: ~30 days ≈ one monthly charge cycle, i.e.
   "did they engage with THIS box." 🔴 Do not shorten it casually — a customer who curated three
   weeks ago would then read as never-logged-in and get silently swapped, which is the exact burn
   this guardrail exists to prevent. Lengthening it toward "ever" makes shortages unresolvable
   (every portal user becomes permanently unswappable). PARKED improvement: anchor to the
   subscription's last charge date instead of a flat day count — that is the question we actually
   mean; needs Kurt's go before implementing.
5. **Paid items are never swapped.** Only $0 in-box lines (`_rc_bundle` property) are
   eligible; paid signal = actual-paid > 0 **OR** catalog price > 0 (the catalog half
   catches Recharge-collected money pushed to Shopify at $0 — #163709 MT-CCSP).
   Enforced twice: plan filters `_rc_bundle`-only; `_swap_order_skus` runs its own
   PAID-ITEM GUARD (`rc_bundle_only=True`, `allow_paid` never passed).
6. **Swap target must resolve to the $0 in-box variant** — `_lookup_variant_gids`
   prefers the cheapest variant; a target resolving only to a priced variant is a
   plan-time hard failure, not a warning.
7. **Recharge calls follow the `recharge-api` skill:** `X-Recharge-Version: 2021-11`
   header, cursor pagination (page-based silently loops), `timeout=30`, 429 backoff.
8. **Dry-run is the DEFAULT.** Writes require `--apply`. Live-writes discipline
   (`~/.claude/rules/live-writes.md`): restate + count + SHIP_TAG echo before Kurt says go.
9. **Every run appends a JSONL result log** under `_outputs/logs/`
   (`shorts_pass_<TAG>_<ts>.jsonl`) — plan, exclusions, per-order results, verify diff.
   Never overwrite a prior run's log. (`_swap_order_skus` separately appends the
   canonical `_outputs/logs/swap_audit.jsonl`.)
10. **GraphQL `sku:` search index-lag** produced phantom "failures" on wk0810 — variant
    lookups happen ONCE up front via the batched cached path; per-order re-lookups are
    forbidden.
11. **🔴 A pairs.csv `old_sku` on ZERO cohort orders is a TYPO — REFUSE, never a `0 planned` row.**
    ([[feedback-join-zeroes-silently]]) Live 2026-08-08 smoke test on the sibling tool:
    `tag_where.py _SHIP_2026-08-10 --has PR-CJAM` reported "0 of 2321 orders match", exit 0,
    because the real SKU is `PR-CJAM-GEN` and matching is EXACT. In this tool the failure hides
    even better: a bad `old_sku` renders as an innocuous `elig 0 / plan 0 / excl 0` line in the
    preview table, so a week's shorts silently go unswapped and the verify phase passes (0
    expected, 0 actual). Before the Recharge exclusion pass, `cohort_skus()` unions every
    fulfillable SKU on the tag (fulfillable-only, rule 3) and any `old_sku` missing from it makes
    the whole run exit 2 with prefix-overlap near-miss candidates per bad pair — a bad pairs.csv
    is rejected as a file, not silently half-executed. `--allow-absent-sku` is the deliberate
    escape hatch. Same guard/flag in `tag_where.py`, `remove_line_items.py`, `refund_batch.py`.

12. **🔴 A bundle_selection EXISTING is not customization — measure `updated_at > created_at`.**
    Recharge writes a bundle_selection for every bundle subscription by default, so
    `if bs.get("bundle_selections"): return True` flags essentially every subscriber.
    **Live 2026-08-09 on `_SHIP_2026-08-10`:** CH-CONI planned **1 swap out of 67 eligible** — 44
    excluded as "customized", 22 as "logged_in". Re-checking 10 of the 44 via the per-order chain
    found `updated_at − created_at = 0s` on **all 10** — every one a false positive. 🔴 The failure
    was SILENT, not loud: rule 2's verify compares plan vs actual and both were 1, so the run exits
    0 and a week's shorts quietly go unresolved. This is the "suspiciously SMALL count" that the
    rule-11 zero-guard cannot catch. Fix: `_bundle_edit_delta_s() > 5s` (a system write is 0s).
    Post-fix: plan 2/2, and controls against an independent per-order chain give 5/5 true positives,
    0/5 false positives. Memory: [[recharge-bundle-selection-scoping]].
    **PARKED:** scope to THIS order (order → `/charges?external_order_id=` → box line
    `purchase_item_id` → `/bundle_selections?purchase_item_ids=`) rather than the customer's active
    subscriptions. Customer scope over-protects, which is the safe direction, but it is not the
    question we mean; needs the caller to pass order id, not just email. Kurt's go first.

13. **🔴 Wrap stdout to UTF-8 before ANYTHING prints — including `--help`.**
    Live 2026-08-09: `shorts_pass.py --help` died with
    `UnicodeEncodeError: 'charmap' codec can't encode character '→'` under Windows cp1252,
    exit 1, before doing any work. Five of the six new tools (`shorts_pass`, `outreach`,
    `resolve_import_dupes`, `tag_where`, `refund_batch`) shipped with non-ASCII output and **no**
    wrap; only `remove_line_items` had one. It passes interactively when the terminal is UTF-8 and
    fails in a pipe or a scheduled run — and for an `--apply` tool the crash lands BETWEEN
    mutations, leaving the batch half-applied (exactly the wk0810 refund burn). All six now wrap at
    import. ⚠️ Side effect: importing these modules as a library replaces `sys.stdout` — do not
    pre-wrap in a caller or the outer wrapper is orphaned ("I/O operation on closed file").

## What it is

`shorts_pass.py SHIP_TAG --pairs pairs.csv [--apply] [--limit N]` — one command for the
weekly shorts→swap pass. `pairs.csv` rows: `old_sku,new_sku,count`.

Phases: **plan** (find_swap_targets eligibility + fulfillable filter → login/customize
exclusion → cap at count) → **preview table** (per pair: eligible / planned / excluded+why)
→ **execute** (`--apply` only; order_edit module path, ThreadPool 8) → **verify**
(re-fetch tag, re-count fulfillable old/new, diff vs plan, nonzero exit on mismatch).

Inputs: Shopify auth via `appyhour_lib.credentials.get_shopify_auth()`; Recharge token via
`cut_order_server.app.creds.get_recharge_token()`. Outputs: stdout preview + JSONL log.
Eligibility reuses `InventoryReorder/fulfillment_web/shopify_swap.py::find_swap_targets`.

Non-goals: single ad-hoc swaps (use `/swap`), paid-item swaps, Recharge-side charge edits,
tray cohorts (TR- never in Tuesday cohorts).
