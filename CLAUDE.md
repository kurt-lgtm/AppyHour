# AppyHour — Cold Chain Fulfillment Platform

Desktop analytics for Elevate Foods (subscription cheese/charcuterie). Python + pywebview (netfx) + tkinter. Inventory forecasting, cut order generation, shipping analytics, order quality.

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
| `matrix_commander.py` + `matrix_commander_web/` | Fulfillment pipeline orchestrator | inline |

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

- **SKU prefixes:** CH (cheese), MT (meat), AC (artisan), AHB (box type), BL (bulk), PR-CJAM (jam pairing), CEX-EC (extra cheese), PK/TR/EX (non-pickable). Only CH/MT/AC count for item-count error detection.
- **Curations (11):** MONG, MDT, OWC, SPN, ALPN, ALPT, ISUN, HHIGH, NMS, BYO, SS, GEN, MS
- **Error classes:** 2/3 (bundle missing), 4/4b (double food), 6 (curation mismatch), 7 (RC IDs missing), 11 (structural)
- Box → curation: `resolve_curation_from_box_sku()` in `cut_order_generator.py`

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
