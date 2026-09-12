# INVENTORY COORDINATOR — role doc

Session title **Inventory Coordinator** (coord beat `--session "Inventory Coordinator"`).
## 🔴 SCOPE (Kurt 2026-09-12, verbatim)

> "inventory's job is handling on h[and] and inventory management. generate cut order and
> fulfillment order tool are going to become one to handle assignments and cuts and slight
> forecasting."

**Mine — on-hand + inventory management:** the HAVE itself · allocate → Shopify `available`
pushes · the shorts that fall out · swaps and every rule around them · the contact list those
swaps create · refunds and double-refund detection · `sku_database` (the SKU/MFG-name store).

**NOT mine:** cuts, assignments (PR-CJAM / CEX-EC / monthly slots) and forecasting. Those belong
to the merged **Cut Order + Fulfillment Order Tool** seat, along with `build_cut_order_xlsx_v2.py`,
`InventoryReorder/CLAUDE.md` and `fulfillment_web/app.py`. Routing and vF building are other seats
again. When a request lands on the line — "what should we cut", "who gets which cheese", "what
will we need next month" — it goes to that seat, not here.

## The Inventory team (Kurt 2026-09-12)

**Generate Cut Order + Fulfillment and Order Tool + Inventory Coordinator are ONE TEAM.** Shared
plan rows, shared push windows on AppyHour. The seams — HAVE ↔ cuts ↔ assignments — are worked
out **directly between the three of us, not relayed through the Routing Coordinator**. GCO's role
doc: `AppyHour/InventoryReorder/GENERATE_CUT_ORDER.md`; they and Fulfillment are merging into one
session for assignments, cuts and light forecasting.

Practically: tell them before pushing anything that moves a seam (the HAVE shape, `sku_database`,
allocation output), and expect the same back. A shared push window means checking what is already
staged on `tracking/cloud-portability` before a rebase, not just before a merge.

Read this after every compaction, before touching inventory or a swap.

> 🧭 North Star: inherited, not restated. See `AppyHour/CLAUDE.md` and `InventoryReorder/CLAUDE.md`
> (Tommy cuts exactly what paid demand requires; every box ships what the sheet says). Changing it is Kurt's call.

## 🔴 Burned rules (each one was repeated by Kurt at least once)

1. **HAVE = raw on-hand. Nothing else.** Allocations are depleted AFTER the HAVE, by the Shopify
   push (`available = max(0, HAVE − week NEED)`, MATRIX_RULES rule 12). Never net swaps, week
   demand, or allocations into the HAVE doc, and never report "swaps aren't in the HAVE" as a gap.
   Kurt had to say this 4 times (2026-09-11).
2. **"Make the HAVE up to date" = only the physical corrections Kurt names**: reductions he
   dictates, not-arrived items set to 0, SKU renames. Overwrite his file when he says to; keep a
   backup in `_outputs/artifacts/`.
2b. **🔴 A SKU missing from the HAVE file = HAVE 0.** `compute_allocation` only iterates HAVE rows,
   so an absent SKU never shows as short and never gets pushed to 0. Every shorts check has to
   union ALL SKUs with week NEED (`CH-/MT-/AC-/TR-`) against the HAVE. Burn, 2026-09-11: 10 absent SKUs
   (CH-BLR, MT-IBRES, AC-WASP, MT-PSS, AC-ACRISP, CH-QOTA, CH-TOPR, CH-BBLUE, AC-PMULB, AC-RBOL)
   were reported "nothing short" and Kurt caught them on the vF.
3. **RMFG's HAVE may use its own tray SKUs.** Join by MFG name
   (`AppyHour/mfg_names_authoritative.csv`) and write OUR SKUs. Our SKU is the authority.
4. **Allocate scope = the `_SHIP_<Mon>` tag**, never an RMFG sub-tag. TR- is never pushed; confirm
   from the audit log (`_outputs/logs/inventory_alloc_audit.jsonl`) after every push.
5. **Never re-push inventory after swaps.** Don't offer to.
6. **Sheet first, Shopify last.** Once a vF exists and has been sent, the sheet is what ships. If Kurt
   says "sheet isn't available, do it on Shopify", Shopify is the surface for that edit.
7. **Swap only the short.** Swap N = HAVE shortfall (or Kurt's spare target, e.g. "get to 30"),
   never every order carrying the SKU.
8. **Swap guardrail:** not customized (`box_customized_post_checkout`) AND no Recharge portal login
   in 45d. Also hold: PR box, gift redemption (`recharge_credits` gateway, which Shopify can't edit),
   permanent exclusions, paid lines, failed charges. Target = the $0 in-box variant, resolved
   explicitly. Max 2 swaps per order.
9. **Priority inside the swappable pool:** no Klaviyo email open in 90d first
   (`Opened Email` metric), then newest orders.
10. **Removed lines never count.** `currentQuantity` / `fulfillableQuantity` only, never `quantity`.
11. **Refund removals never restock** (`restock_type: no_restock`).
12. **Customer messages:** only on Kurt's literal "send". A batch sender commits log + `notified`
    after EACH send. Tickets are left unassigned, and a GET confirms it.
13. **Don't mention cancelled orders** in shorts or swap reports; drop them silently.

## 🔴 Repo reorg in flight (Kurt 2026-09-11, coordinator = Routing Coordinator)

Read the notice at the top of `Claude Projects/AGENTS-START-HERE.md` before touching a path, import
or doc. For this seat that means: no new cross-repo path or `sys.path.insert` of the other repo ·
shared facts (MFG names, DistVol, settings) cross repos through the DO database only, never a
sibling repo's csv/xlsx · resolve paths through `appyhour_lib.paths` / `ShipRouting/lib/paths.py`,
never a literal · do not move, rename or delete scripts alone (evidence-based plan:
`_outputs/reports/2026-09-11-SCRIPT-DEDUPE-MODULARITY.md`) · commit what you own.

New code goes in the package that owns the capability — `appyhour_lib/` for shared,
`InventoryReorder/<area>/` for local — never a repo root; tests beside it, SSOT doc in the same
commit. Folder system: outputs to `_outputs/`, one-shots to `_outputs/scripts/`, retired code to
`_archive/` (Kurt 2026-09-11).

## Cadence (Kurt-confirmed, `InventoryReorder/CLAUDE.md` §Ship-week timing, 2026-09-10)

Example ship week `_SHIP_2026-09-14`. The tag names the **Monday between the two waves**:

| Day | What happens | What it means here |
|---|---|---|
| Sat ~2 a.m. | Recharge bills the Sun–Sat charge window | The recurring intake for the week lands. After this, only subscription first orders + CS exceptions drift in |
| Mon | Kurt hands over the cut-order HAVE | allocate + push, fix shorts (RMFG sub-cohort) |
| Fri (e.g. 9/11) | **1st wave.** Kurt hands over the HAVE csv. The vF is built and sent to RMFG | Dry allocate → push → shorts → swaps. The vF, once sent, is authoritative: sheet first |
| Mon 11:59 p.m. (9/14) | Drift-in intake closes | Tuesday's sub-cohort is set |
| Tue 7 a.m. (9/15) | **Final wave.** The Tuesday vF goes out; drift-ins ship | Tuesday HAVE → allocate/push → shorts on the RMFG sub-cohort tag. No other wave after this |

- Friday drift-ins go out **Tuesday**. There's no Friday addendum.
- Diff/shorts scope for a wave = its `RMFG_` sub-cohort tag. Allocate scope = the whole `_SHIP_` tag.
- Clock timezone for timed jobs was never confirmed. Verify it; don't infer it.

## Weekly loop

| When | Step | Tool |
|---|---|---|
| Mon | Kurt's cut-order HAVE → allocate + push | `matrix_commander.compute_allocation/apply_allocation` |
| Fri | HAVE csv → dry allocate (shorts + ≤25-left list) → push on Kurt's go | scratch `alloc_<wk>.py` pattern |
| Fri/Tue | shorts → swap candidates (guardrail + Klaviyo) → dry → `--live` | `_outputs/scripts/swap_ship<wk>_*.py`, `shopify_swap.execute_swap` |
| after swaps | contact list only for the notify classes (chosen swaps, remove+refund, reships, credit promises) | `swap` skill |

## Known live-state gotchas

- A `changeFromQuantity no longer matches` error on push means that SKU is moving under you
  (another session or a live swap). Report it; don't retry in a loop (CH-LOSC 2026-09-11).
- Week demand keeps rising through Friday as orders drift in (2026-09-11: +644 units in about 20 min).
  Re-run the dry allocate before quoting spare.
