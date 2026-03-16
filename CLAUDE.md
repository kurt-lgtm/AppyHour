# AppyHour — Cold Chain Fulfillment Platform

## Overview

Desktop analytics platform for Elevate Foods (elevatefoods.co), a subscription cheese/charcuterie box company. Built with Python + pywebview (netfx/.NET Framework backend) and tkinter. Manages shipping analytics, inventory forecasting, cut order generation, and order quality/error detection.

## Project Structure

```
AppyHour/
├── GelPackCalculator/       # Thermal analysis, gel pack sizing, Shopify integration (tkinter)
│   └── gel_pack_shopify.py  # Main app (~3200 lines, single-file tkinter)
├── InventoryReorder/        # Inventory forecasting, cut order generation (tkinter)
│   ├── inventory_reorder.py # Main app (tkinter, cohort-based forecasting)
│   ├── cut_order_generator.py  # Weekly cut order from Recharge+Shopify demand
│   ├── fulfillment_web/     # pywebview fulfillment dashboard
│   └── Errors/              # Error detection & fix scripts (24 scripts)
├── ShippingReports/         # Shipping analytics & cost analysis
│   └── ingest.py            # Data ingestion pipeline
├── AppyHourMCP/             # MCP server for Claude Code integration
│   ├── server.py
│   └── tools/               # shipping.py, inventory.py, gelcalc.py
└── pyproject.toml           # Project config (pytest, ruff, pyright)
```

## Run

```bash
# Python path on this machine (not in PATH for bash)
/c/Users/Work/anaconda3/python.exe

# GelPackCalculator
cd GelPackCalculator && python gel_pack_shopify.py

# InventoryReorder
cd InventoryReorder && python inventory_reorder.py

# Tests
pip install -e ".[dev]"
pytest
```

## Key Domain Concepts

### SKU Taxonomy
- **CH-** — Cheese (CH-MCPC, CH-BLR, CH-WWDI, CH-EBRIE, etc.)
- **MT-** — Meat (MT-LONZ, MT-TUSC)
- **AC-** — Artisan crafted items (AC-DTCH, AC-PRPE, AC-TCRISP)
- **AHB-** — Subscription box types (AHB-MED, AHB-LGE, AHB-MCUST-*, AHB-LCUST-*)
- **BL-** — Bulk/base items
- **PR-CJAM-** — Bonus cheese+jam pairings (1 per box)
- **CEX-EC-** — Extra cheese assignments (~40% of boxes, large only)
- **PK-/TR-/EX-** — Non-pickable items

Only CH/MT/AC count toward item count for error detection.

### Curations (Box Recipes)
11 standard: MONG, MDT, OWC, SPN, ALPN, ALPT, ISUN, HHIGH, NMS, BYO, SS, GEN, MS

### Box SKU → Curation Resolution
`AHB-MCUST-MONG` → MONG, `AHB-LGE` → MONTHLY, etc. See `resolve_curation_from_box_sku()` in `cut_order_generator.py`.

### Error Order Classes
- **Class 2/3** — Bundle selection missing/incomplete (Recharge charges)
- **Class 4/4b** — Double food item or duplicate curation write
- **Class 6** — Curation mismatch (food items don't match box curation)
- **Class 7** — Recharge charge missing RC IDs
- **Class 11** — Structural errors (excluded SKU, 3+ copies, missing box/category, bare CEX-EC)
- CH-MAFT is never assigned (ASSIGNMENT_EXCLUDE)

## API Integration Notes

### Recharge (Subscription Management)
- **Cursor pagination is mandatory** — page-based silently loops forever
- Always use `timeout=30`
- Rate limiting: respect 429 responses with retry-after
- `bundle_selections` PUT needs real collection_id (can't be empty)
- Skip/unskip must pass `purchase_item_ids`
- v2021-11: `variant_id` is nested dict, not string

### Shopify (Order Management)
- GraphQL order edit API: use `beginEdit` → `addVariant`/`setQuantity` → `commitEdit`
- Filter qty=0 (removed) line items from CalculatedOrder before counting
- Use `fulfillableQuantity` when available
- `_rc_bundle` property = Recharge curation (removable); no props = paid/extras (keep)

### pywebview (Desktop UI)
- Uses **netfx** (.NET Framework), NOT coreclr/.NET 8
- Bridge availability: use `waitForBridge()` polling, NOT `pywebviewready` events
- `evaluate_js` does NOT work from Python API threads — use polling instead

## Design Conventions

- Three fonts: **DM Sans** (table data), **Space Mono** (UI chrome, 11-13px weight 600), **Rajdhani** (numbers/display, 12px weight 400)
- Dark theme with ttk "clam" theme
- Immutable data patterns preferred
- Threading: API calls on daemon threads, UI updates via `root.after(0, callback)` or polling

## Testing

```bash
pytest                    # run all tests
pytest --cov              # with coverage
ruff check .              # lint
pyright                   # type check
```

Target: 80%+ coverage. TDD workflow (RED → GREEN → REFACTOR).
