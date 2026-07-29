# AppyHour — Cold Chain Fulfillment Platform

Desktop analytics for Elevate Foods (subscription cheese/charcuterie). Python + pywebview (netfx) + tkinter. Inventory forecasting, cut order generation, shipping analytics, order quality.

> 🧭 **NORTH STAR** (read before ANY change — every change must serve this): run the cold-chain
> subscription operation end-to-end with **minimal manual intervention** — every box arrives **≤2 days,
> cold, at the lowest *expected total* cost** (ROUTING_RULES §0; Kurt-signed 2026-07-29): label PLUS the
> measured price of failure risk (reships at measured conversion, CS handling, customer loss when the
> data shows it). **Late risk is priced, not forbidden** — a cheaper lane may carry more late risk when
> the math wins, bounded by two floors never for sale: the **2-day promise** and **cold arrival** (warm
> risk is a safety constraint, not a line item). Ground by default, air only when proven necessary; every
> tradeoff constant is a measured, inspectable lever (LEVER_RULES.md). Every routine decision automated
> with **loud failures, never silent ones** (HEARTBEAT_RULES); fast, efficient, secure, autonomous
> (Kurt 2026-07-02). A change that satisfies every constraint but adds manual steps, silent failure
> modes, or unpriced cost moves AWAY from this — flag it.

## 🛠 Tool dispatch — READ FIRST

**Before calling or writing ANY tool/script, consult [`TOOL_REGISTRY.md`](TOOL_REGISTRY.md)** — the canonical *capability → tool* dispatch table (40 MCP tools, ops scripts, ShipRouting, skills, routines). Find the capability, **call the listed canonical tool — never reinvent, hallucinate, or rebuild one** (it lists the ~48 dup/one-shot scripts to avoid). This is the primary small-model operating reference: each row is flagged **✅** small-model-safe (just call it) · **🔒** needs judgment/confirmation · **✍️** writes/mutates (dry-run + verify first). If a capability isn't there: codegraph → registry → `appyhour_lib` → grep, BEFORE creating anything.

## Map

| Area | Purpose | CLAUDE.md |
|------|---------|-----------|
| `GelPackCalculator/` | Thermal analysis, gel-pack sizing, Shopify forecast (tkinter) | `GelPackCalculator/CLAUDE.md` |
| `InventoryReorder/` | Demand forecasting, cut order, fulfillment web (tkinter + Flask) | `InventoryReorder/CLAUDE.md` |
| `ShippingReports/` | Shipping analytics + cost analysis (canonical `shipments.db`) | `ShippingReports/CLAUDE.md` |
| `AppyHourMCP/` | Main MCP server — tools for shipping/inventory/gelcalc/orders | `AppyHourMCP/CLAUDE.md` |
| `AppyHourShippingMCP/` | Shipping-only MCP server (subset) | `AppyHourShippingMCP/CLAUDE.md` |
| `appyhour_lib/` | Shared library (weather, credentials) — **not** the AppyHour repo | `appyhour_lib/CLAUDE.md` |
| `scripts/` | Loose utilities (swaps/audits/incident-fixes/utilities) | `scripts/README.md` |
| `matrix_commander.py` + `matrix_commander_web/` | Fulfillment pipeline orchestrator | **`MATRIX_RULES.md`** (constraints SSOT — read before ANY change) |

Original 386-line CLAUDE.md preserved as `_CLAUDE-original-2026-05-10.md` (ledger).

## Task Routing

| Task | Read | Skip | Skills/MCP |
|------|------|------|-----------|
| Cut order generation | `InventoryReorder/CLAUDE.md`, `~/.knowledge/ops/Cut Order*` | `GelPackCalculator/`, `ShippingReports/` | appyhour MCP (inventory tools) |
| Gel pack / thermal | `GelPackCalculator/CLAUDE.md`, `appyhour_lib/thermal.py` | `InventoryReorder/`, `ShippingReports/` | appyhour MCP (gelcalc) |
| Shipping analytics | `ShippingReports/CLAUDE.md`, `~/.knowledge/ops/Crossdock*` | `GelPackCalculator/`, `InventoryReorder/` | appyhour-shipping MCP |
| Order edit / sync | `AppyHourMCP/tools/order_edit.py`, `AppyHourMCP/tools/shopify.py` | rest of MCP/ tools | appyhour MCP (shopify, order_edit) |
| Swap / RMFG | `scripts/swaps/`, `~/.knowledge/ops/Swap Filtering*` | thermal/gel | appyhour MCP (matrix_qc) |
| Add MCP tool | `AppyHourMCP/CLAUDE.md`, `AppyHourMCP/server.py` | desktop apps | — |
| Bug / incident fix | `scripts/incident-fixes/`, MISTAKES.md | apps unless in scope | — |
| Domain demand forecast (per-SKU/cohort, retention+curation aware) | `appyhour_forecast_demand` MCP, `/cut-order`, LTF sheet | TimesFM | — (domain-aware; TimesFM has NO swap/curation/churn knowledge) |
| Generic TS forecast (new/un-modeled series, quantile bands, trading vol) | `forecast_ts.py` (TimesFM 2.5, pinned) | demand tools above | — (use ONLY when no domain forecaster owns the series; cross-check, not source-of-record) |
| **Write ANY new script** (auth/recharge/swap/sheets/imap/weather/box) | **`TOOL_REGISTRY.md` FIRST** | — | call the canonical (get_shopify_auth, recharge_client, /swap…), never reimplement |

## Box Sizing — DistVol (canonical fact)

**DistVol** = AppyHour's per-SKU volumetric unit. `box_simulation.py` sums each order's DistVol → assigns a box size (`SMALL` ≤ 2.99, `LARGE` ≤ 6.7; rate `105.3` cu-in per 1.00 DistVol).
- **Source of truth** = the `DistVol` column in `C:\Users\Work\Desktop\Onboarded Items with DistVol - Updated.xlsx` (hardcoded `box_simulation.py:20`). Single-copy on Desktop — in the backup rescue set.
- Fallbacks when a SKU isn't in the xlsx: `PREFIX_DEFAULTS` (AC .12 / CH .20 / MT .07 / PK 0 / TR 1.0) + `MANUAL_OVERRIDES` in `box_simulation.py`.
- Tools: `box_simulation.py [SHIP_TAG]` (compute), `audit_distvol_drift.py` (re-derive lookup vs CSV), `_outputs/box_distvol.db` + `_outputs/cache/sku_distvol_map.json` (cached map). See `TOOL_REGISTRY.md`.

## Run

```bash
PY=/c/Users/Work/anaconda3/python.exe
$PY GelPackCalculator/gel_pack_shopify.py
$PY InventoryReorder/inventory_reorder.py
pip install -e ".[dev]" && pytest
```

## Critical Constraints

- **Live data only** — no staging; tests against real Shopify/Recharge
- **pywebview = netfx** (.NET Framework), NOT coreclr/.NET 8. Use `waitForBridge()` polling, not `pywebviewready`. `evaluate_js` won't work from API threads.
- **Recharge cursor pagination MANDATORY** — page-based silently loops. `timeout=30`. v2021-11 nests `variant_id` as dict.
- **Shopify GraphQL order edit** — `beginEdit` → `addVariant`/`setQuantity` → `commitEdit`. Filter qty=0. Use `fulfillableQuantity`. `_rc_bundle` = removable.
- **Shared settings JSON** at `%APPDATA%/AppyHour/` — schema changes must be backward-compatible across 3 apps.
- **PR-CJAM-GEN** = only generic; curation-specific variants made by Shopify post-charge.
- **CH-MAFT** never assigned (ASSIGNMENT_EXCLUDE).

## Domain Quick-Ref

- **SKU prefixes:** CH (cheese), MT (meat), AC (artisan), AHB (box type), BL (bulk), PR-CJAM (jam pairing), CEX-EC (extra cheese), TR (trays), PK (inserts), MR (journal). **All prefixes are pickable** — PK & MR carry 0 DistVol; TR has DistVol (~1.0); CH/AC/MT/TR have per-SKU DistVol from the xlsx. Only CH/MT/AC count for item-count error detection.
- **Curations (11):** MONG, MDT, OWC, SPN, ALPN, ALPT, ISUN, HHIGH, NMS, BYO, SS, GEN, MS
- **Error classes:** 2/3 (bundle missing), 4/4b (double food), 6 (curation mismatch), 7 (RC IDs missing), 11 (structural)
- Box → curation: `resolve_curation_from_box_sku()` in `InventoryReorder/fulfillment_web/app.py` (line ~1325)

## Style

DM Sans (table data), Space Mono (UI chrome 11-13px w600), Rajdhani (numbers 12px w400). Dark theme, ttk "clam". Immutable updates. Daemon threads for API; UI updates via `root.after(0, cb)` or polling.

## Toolchain

`pyproject.toml` (PEP 517). `ruff` (120 cols, E/W/F/I/UP/B/S/SIM). `pyright` basic. `pytest --cov` target 80%+. PyInstaller `--onefile --windowed`.

## Layered Architecture

1. **Pure logic** — `appyhour_lib/` (no API/UI deps, stdlib only)
2. **Domain** — GelPackCalculator, InventoryReorder, ShippingReports
3. **MCP integration** — `AppyHourMCP/tools/` (Pydantic-validated, FastMCP `register(mcp)`)
4. **UI/CLI adapters** — tkinter desktop, pywebview/Flask web

Full architecture detail in `_CLAUDE-original-2026-05-10.md` if needed.
