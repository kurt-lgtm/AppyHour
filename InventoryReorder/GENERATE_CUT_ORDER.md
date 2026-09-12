# GENERATE CUT ORDER — role doc

Session title **Generate Cut Order** (coord beat `--session "Generate Cut Order"`). Read this after
every compaction, before touching the cut order, an assignment plan, or a forecast.

> 🧭 North Star: inherited, not restated — [CLAUDE.md](CLAUDE.md): *Tommy cuts EXACTLY what paid
> demand requires — no over-cut, no shortage, no stale inputs.* Changing it is Kurt's call.
> Constraints SSOT: [CLAUDE.md](CLAUDE.md) (cut-order constraints) + `order_checks/ORDER_CHECKS_RULES.md`
> (swap / reserve rules) + `scripts/SHORTS_PASS_RULES.md` (shorts → swap). One SSOT per surface —
> never a third doc.

## Scope (Kurt 2026-09-12, verbatim — relayed by Routing Coordinator, recorded in the reorg plan)

> "inventory's job is handling on hand and inventory management. generate cut order and fulfillment
> order tool are going to become one to handle assignments and cuts and slight forecasting."

So this role and **Fulfillment and Order Tool** (`AdminApp-Integration/FULFILLMENT_AND_ORDER_TOOL.md`)
**merge into ONE session** owning:

1. **Assignments** — the order / planner side: PR-CJAM cheese, CEX-EC, free offloads, the
   per-customer plan DB, the AdminApp planner (reorg R-16 / R-27) once it lands.
2. **Cuts** — the weekly cut order (`build_cut_order_xlsx_v2.py`, `/cut-order`), shorts → swaps,
   wheel coverage, the Drive upload Kurt fills in.
3. **Slight forecasting** — first-order projection, EOY stockout / burn-down, next-N-weeks
   consumption. Not the LTF, not TimesFM as demand-of-record.

**Not this role:** on-hand + inventory management — HAVE uploads, `set_corrected_inventory.py`
runs, received/production counts, Product Inventory batch sheets, expiry triage, reorder alerts.
That stays with **Inventory Coordinator** (`AppyHour/INVENTORY_COORDINATOR.md`). This role
CONSUMES HAVE; it never produces it. Routing / vF / ice → Routing Coordinator.

**Merge shape** is being coordinated with Fulfillment and Order Tool (their doc keeps the AdminApp
side: vendor repo boundary, local-only unified tool, `main` = production gate). Until the two docs
are folded into one, each session keeps its own doc and neither edits the other's.

## 🔴 Standing rules for this role (each has a burn)

- **HAVE is authoritative and absent = uncounted, never zero.** Never add planned Cut, receipts or
  "corrections" on top of a fresh RMFG count (8/25: CH-ASST 668 vs true 150). Thousands separators
  must parse (`"2,096"`, AC-BRJA 9/08).
- **`currentQuantity > 0` only.** Never raw `quantity` / `fulfillableQuantity` for presence
  (wk0907 board inflated ~1,789).
- **Free assignments never take projected Friday HAVE below the reserve floor** — ONE operator
  setting `reserve_floor` (default 30, Plan R-28; `check7.reserve_floor()`), never a code constant.
  Paid add-ons exempt. A box's OWN line items are blocked (#182931 got CH-LOSC while LOSC was in
  the box). Never-had (full vF archive) first, then not-in-last-4, then the fallback cheese.
- **Allocate ONCE over the whole pool and persist** (`_outputs/cache/prcjam_plan_<shipweek>.db`);
  later conversions are a LOOKUP, never a re-derivation (batch-by-batch drifted LOSC 584 / RQCAV 104
  vs planned 600 / 363). Verify per planned ORDER, never by population re-count.
- **`build_cut_order_xlsx_v2.py` is FROZEN under Plan R-19** — no move / rename / restructure until
  the AdminApp export reaches parity (one-shot parity script, deltas verbatim). Behavior-preserving
  one-hunk fixes only, "Plan: R-19" in the body. Port spec:
  `_outputs/reports/2026-09-11-CUT-ORDER-DEMAND-LOGIC-FOR-PORT.md`.
- **Bundle explosion**: Simple Bundles metafield + `BUNDLE_RECIPE_OVERRIDES`; a component is added
  only when not already a line on the same order/charge (`Added + Already = Boxes × Per`). No recipe
  → `{}` + WARN, never a guess.
- **Monthly boxes bucket by calendar MONTH, RC + SH combined.** Blank slot = silent under-count.
- **Never fabricate a SKU identity, MFG name or recipe** — `CH-FONT` ≠ `CH-FONTAL`; elicit.
- **Test-tagged orders (`PRCJAMTEST_909`) are ignored** everywhere.
- **Commits**: your commit, your push; cite the reorg plan row. Files not yours stay dirty in the tree.

## Where things live

| Thing | Path |
|-------|------|
| Cut order build (frozen) | `InventoryReorder/build_cut_order_xlsx_v2.py` |
| Demand model | `InventoryReorder/inventory_demand_report.py` (WK1 window, `_wk1_ship_tags`) |
| HAVE → settings (Inventory Coordinator runs it) | `InventoryReorder/scripts/set_corrected_inventory.py` |
| Shorts → swaps | `scripts/shorts_pass.py` + `scripts/SHORTS_PASS_RULES.md` |
| Repeat / substitute checks, reserve floor | `order_checks/check7.py`, `order_checks/swaps.py` |
| Assignment plan DB (per ship week) | `_outputs/cache/prcjam_plan_<shipweek>.db` |
| Reorg plan of record | `_outputs/reports/2026-09-11-REORG-PLAN-OF-RECORD.md` (rows R-16, R-19, R-27, R-28) |
