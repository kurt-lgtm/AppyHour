# Matrix Tool — Constraints (single source of truth)

> 🔴 **PRE-CHANGE GATE:** read this BEFORE touching `matrix_commander.py`, `matrix_commander_web/`,
> `ShipRouting/scripts/gen_rmfg_export.py`, or `AppyHourMCP` matrix tools. Change rules HERE first —
> same commit as the code. Gotchas/negatives-first: each rule below encodes a failure that shipped.

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

Linked from `AppyHour/CLAUDE.md`. Audit that produced this doc:
`_outputs/reports/2026-07-02-matrix-tool-audit.md`.
