# Fulfillment Web App — Development Guide

## Overview
Flask + pywebview gamified weekly cheese fulfillment planner. Shares settings JSON with main `inventory_reorder.py` tkinter app.

## Run
```bash
python app.py --browser    # opens in default browser
python app.py              # opens in pywebview native window (fallback to browser)
```
Port: **5187**

## Architecture
- `app.py` — Flask backend (all API endpoints + file parsing + calculation engine)
- `templates/index.html` — Single page app HTML
- `static/app.js` — All client-side JS (state, rendering, mascot, calendar)
- `static/styles.css` — FUI dark theme (charcoal + cyan)

## Key Behaviors
- **Auto-run on load**: Page load triggers full pipeline: detect RMFG folder → load data → calculate → build calendar
- **Two views**: Dashboard (NET table + assignments + shelf life) and Calendar (4-week action grid)
- **Shares settings**: Reads/writes `inventory_reorder_settings.json` from parent dir or dist/

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/data` | GET | Return all settings data for UI |
| `/api/calculate` | POST | Legacy calculate (settings-based demand) |
| `/api/calculate_rmfg` | POST | Multi-window calculate (RMFG folder data) |
| `/api/load_rmfg` | POST | Load RMFG folder (auto-detect files) |
| `/api/run_all` | POST | Full pipeline: load + calculate + subs |
| `/api/action_calendar` | POST | Generate 4-week action calendar |
| `/api/assignments` | GET | Get PR-CJAM + CEX-EC assignment rows |
| `/api/assign` | POST | Set a cheese assignment |
| `/api/candidates/<cur>/<slot>` | GET | Get candidate cheeses for assignment |
| `/api/auto_assign` | POST | Auto-assign all curations |
| `/api/suggest_fixes` | GET | Shortage fix suggestions |
| `/api/substitutions` | GET | Surplus→shortage substitution suggestions |
| `/api/wed_po` | GET | Generate Wednesday PO lines |
| `/api/variety_check` | GET | Check ±2 curation cheese overlaps |
| `/api/split` | POST | Set CEX-EC split ratios |
| `/api/import_csv` | POST | Import demand CSV (file upload) |
| `/api/export_csv` | GET | Export NET report as CSV download |
| `/api/rmfg_folders` | GET | List available RMFG_* folders |

## RMFG Folder Structure
Files auto-detected in `RMFG_*` folders:
- `*Template Check*.csv` — Primary inventory (SKU, qty)
- `*Product Inventory*.csv` — Fallback inventory from fulfillment center
- `*order-dashboard*.csv` — Shopify orders (Saturday demand)
- `charges_queued*.csv` — Recharge queued charges
- `*MARCH CHARGES*.csv` — Next Saturday demand (date-range filtered)

## Demand Windows
1. **Saturday**: order-dashboard + charges_queued (all pickable SKUs)
2. **Tuesday**: first orders × 3 (from order-dashboard `Subscription First Order` tag)
3. **Next Saturday**: MARCH CHARGES filtered by date range

## Action Calendar Logic
- 4 weeks generated from today
- Running inventory depletes each week (Sat demand + Tue demand)
- Shortages trigger PO lines (Wed) or MFG orders (if wheels available)
- Open POs with ETAs generate Crossdock tasks on arrival date
- Fulfillment tasks on Tue/Sat with unit counts and shortage warnings

## Constraint System
- PR-CJAM cheese must be unique across all curations
- ±2 window check: no cheese overlap within 2 adjacent curations in CURATION_ORDER
- CEX-EC can be split across multiple cheeses via `cexec_splits`

## Hardcoded Defaults
- `RMFG_20260307` folder has PO421 additions, incoming inventory, corrections, and finalized assignments baked into the load logic
- Future RMFG folders won't have these — they'll use settings as-is

## CSS Theme Variables
```
--bg: #0a0a0d    --accent: #00d4ff    --green: #00e676
--bg2: #131316   --red: #ff3b5c       --orange: #ff8800
--surface: #161619   --yellow: #d4960a    --blue: #60a5fa
```
