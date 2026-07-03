# Matrix Tool — Constraints (single source of truth)

> 🔴 **PRE-CHANGE GATE:** read this BEFORE touching `matrix_commander.py`, `matrix_commander_web/`,
> `ShipRouting/scripts/gen_rmfg_export.py`, or `AppyHourMCP` matrix tools. Change rules HERE first —
> same commit as the code. Gotchas/negatives-first: each rule below encodes a failure that shipped.

> 🧭 **NORTH STAR:** the matrix RMFG picks from matches paid demand EXACTLY — zero wrong physical boxes.
> Every check in this doc exists because an error here becomes a mis-packed box at a customer's door
> (the CEX-EC box-size proxy over-cut ~6× before the line-item rule).

**What it is:** the canonical RMFG production-matrix export pipeline —
`matrix_commander.py generate` (Shopify → Access_LIVE matrix xlsx) → `gen_rmfg_export.py` wrapper
(generate → col-L curation → autofit → QC) → `qc_audit` gate. Validation twin:
`appyhour_validate_production_matrix` MCP tool. Inputs: live Shopify orders by RMFG/_SHIP_ tag.
Output: the xlsx Tommy/RMFG picks from — errors here become wrong physical boxes.

## Rules (negatives-first)

1. **CEX-EC is a 3-level parent chain — resolve down it, never sideways** (Kurt 2026-07-02):
   `CEX-EC` (bare parent line) → `CEX-EC-{suffix}` (curation child) → applied `CH-` SKU (the cheese
   actually picked). All three legitimately COEXIST on one order — bare + suffix together is VALID,
   not a duplicate to clean (vault SKU Quirks). NEVER resolve curation from tag substrings: `cur in
   tag` made `CEXEC.3.10.NMS` match both NMS and MS → phantom expected-cheese flags (removed
   2026-07-02, `check_cexec_cheese_counts`). Curation comes ONLY from the `CEX-EC-{suffix}` level;
   the QC check walks the chain: bare with NO suffix line = unresolved → FLAG (never silently skip);
   suffix with the expected `CH-` absent from assignments = missing cheese → FLAG. The `cex_ec`
   settings map (suffix → CH-) is the level-2→3 edge for demand/QC, not an order fix
   (`feedback_cexec_resolution_rules`).
2. **CEX-EC counts off the actual CEX-EC line item, NEVER box size** — box-size proxy over-cut ~6×.
   CEX-EC = paid add-on on ANY box size. Trays (`AHB-*CUST-TRAY`) must never carry CEX-EC (no
   curation → no valid suffix); a tray+CEX-EC is an upstream Recharge-collection defect, not a
   per-order fix.
3. **Shopify REST `orders.json` has NO `tag` filter** — it silently ignores the param and returns
   ALL open unfulfilled orders (`_fetch_orders_by_tag` shipped this way; worst inside `allocate`,
   which then computed paid demand store-wide). Filter tags client-side (EXACT comma-split match,
   never substring — `RMFG_2026070` must not match `RMFG_20260706`) or use the GraphQL tag query.
   ✅ FIXED 2026-07-02 in all three instances: `matrix_commander._fetch_orders_by_tag`,
   `AppyHourMCP/tools/matrix_qc._fetch_orders_by_tag` (also forces `tags` into `fields`), and
   `InventoryReorder/inventory_reorder.py` customer-lifecycle fetch (was double-counting the whole
   store via a two-tag loop). Any NEW REST fetch must never pass a `tag` param.
4. **Always filter line items to fulfillable** — GraphQL `fulfillableQuantity` / REST
   `fulfillable_quantity > 0`; removed/refunded items otherwise get picked.
5. **QC must GATE, not report** — `qc_audit` exit 1 propagates through the wrapper. Never deliver an
   xlsx from a FAILed run. ⚠️ Known gap: the xlsx is written to Downloads BEFORE QC, so a failed run
   leaves a legit-looking file — treat any xlsx without a PASS log line as untrusted.
6. **Unattended runs use `generate` only** — `full`/`swap` paths contain `input()` prompts and will
   hang a scheduled run.
7. **All SKU prefixes are pickable** (PK/MR carry 0 DistVol) — any "non-pickable PK/TR" list in this
   codebase is stale doc-rot, not a rule.
8. **Read-only against Shopify by default** — writes (`sync-shopify`, `allocate --commit`) are
   dry-run-default and stay that way. Nothing in this pipeline touches `shipping.db`.
9. **Web UI (`matrix_commander_web`) must route through the CLI pipeline functions** — its `/api/sync`
   shipped both broken (dataclass `.get()`) AND bypassing the limiter/guard/checkpoints. Never
   re-implement sync in the web layer. ⚠️ Endpoint still broken as of 2026-07-02.
10. **Column-name → SKU resolution is layered; a name resolving must NEVER pass MFG onboarding**
    (2026-07-03): `parse_matrix` resolves `constants.NAME_TO_SKU` → `mfg_translations.csv` reverse →
    live Shopify product titles + raw SKUs (lazy, only when local layers leave gaps; offline → `??-`
    fallback as before). The Shopify layer exists because validation showed `??-` garbage for names
    known to Shopify — but a Shopify-resolved SKU is still NOT onboarded at RMFG; the
    `check_mfg_onboarding` FAIL must stay loud for it. Never treat "name resolved" as "RMFG can pick
    it". Same resolver feeds `apply_swaps_to_xlsx` column lookup — do NOT let the two drift back to
    `NAME_TO_SKU`-only (swaps silently skipped columns before). `mfg_translations.csv` is now
    git-tracked — refresh it from translator.robbinsmfginc.com via commit, never an untracked drop.
11. **One HAVE sheet feeds BOTH the cross-check and the Shopify Available push** (2026-07-03): the
    web Inventory tab's loaded inventory drives `/api/allocate` → `compute_allocation` /
    `apply_allocation` (extracted from `cmd_allocate`; web routes through them per rule 9 — never
    re-implement the AVAIL$0 math in the web layer). Gotchas: **a tag matching 0 orders is
    REFUSED** (paid=0 → push would set Available to raw HAVE and let parents over-allocate into
    paid units — the exact failure allocate exists to prevent); commit requires a fresh preview and
    invalidates it after push (re-preview after swaps); PK-/MR- structural SKUs stay uncapped;
    Available push remains manual-confirm — never auto-commit (inventorySetQuantities is
    effectively last-writer-wins even with changeFromQuantity read-then-write).

Linked from `AppyHour/CLAUDE.md`. Audit that produced this doc:
`_outputs/reports/2026-07-02-matrix-tool-audit.md`.
