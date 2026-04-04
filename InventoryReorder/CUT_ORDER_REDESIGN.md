# Cut Order XLSX v2 — Redesign Spec

**Status:** Draft
**Current version:** `build_cut_order_xlsx.py` (single sheet, flat layout)
**Target:** New version built alongside current — `build_cut_order_xlsx_v2.py`

## Problems with Current Layout

1. **Flat SKU list** — no visual grouping by urgency, hard to find shortages
2. **Assignment tables crammed to the right** — user must scroll horizontally to see PR-CJAM/CEX-EC
3. **MONTHLY slot tables below everything** — easy to miss, disconnected from demand
4. **No summary dashboard** — user has to scan 100 rows to assess overall health
5. **"Good?" column is text** — "OK" vs "NEED 58" requires reading, not scanning
6. **No demand source breakdown** — can't see RC vs Shopify vs PR-CJAM vs CEX-EC contribution
7. **Week 1 and Week 2 visually merged** — spacer column H is easy to miss

## Design Principles (from UI/UX Pro Max)

- **Data density with hierarchy** — group by urgency, not just alphabetically
- **Color-not-only + color guidance** — red/amber/green fill AND text indicators
- **Scanability** — shortages surface to the top, OK items fade to background
- **Progressive disclosure** — summary first, detail on demand
- **Number formatting** — tabular/monospaced figures, right-aligned numbers
- **Whitespace balance** — clear section separators, not just row gaps

## Proposed Layout: Multi-Tab Workbook

### Tab 1: "Dashboard" (summary view)

```
ROW 1:  Cut Order — Week of 2026-04-11          Generated: 2026-04-04 17:00
ROW 2:  [blank]
ROW 3:  HEADER: Category | SKUs Short | SKUs Tight | SKUs OK | Total Demand W1 | Total Demand W2
ROW 4:  CHEESE     |    8       |     3      |   12    |  4,200         |  4,500
ROW 5:  MEAT       |    3       |     1      |    9    |  3,100         |  3,200
ROW 6:  ACCOMP     |    2       |     4      |   18    |  4,800         |  5,100
ROW 7:  [blank]
ROW 8:  TOTALS     |   13       |     8      |   39    | 12,100         | 12,800
ROW 9:  [blank]
ROW 10: TOP SHORTAGES (sorted by deficit)
ROW 11: HEADER: SKU | Name | Avail | Demand W1 | Deficit W1 | Demand W2 | Deficit W2
ROW 12: CH-TOPR     | Topre...   |    0  | 456       | -456       | 537       | -993
ROW 13: CH-FOWC     | Fowc...    |    0  | 331       | -331       | 243       | -574
ROW 14: ... (top 15 shortages)
```

- Red fill on deficit cells
- Amber fill on "tight" (avail covers W1 but not W2)
- Hyperlinks from SKU → detail row on Tab 2

### Tab 2: "Cut Order" (main working sheet)

Same data as current but with improvements:

```
SECTION: SHORTAGES (red header bar)
  SKU | Name | Avail | RC W1 | SH W1 | +CJAM | +CEXEC | =Demand W1 | After W1 | Cut W1 | Good? | ...W2...
  [sorted by deficit descending]

SECTION: TIGHT (amber header bar)  
  [same columns, sorted by runway ascending]

SECTION: OK (muted header bar)
  [same columns, sorted alphabetically within CH/MT/AC groups]
```

Key changes:
- **Demand broken out**: RC direct, Shopify, +CJAM (from SUMIF), +CEXEC (from SUMIF) shown separately
- **Grouped by urgency**: SHORTAGE → TIGHT → OK, not just alphabetical
- **Category sub-headers** within each urgency group (CHEESE / MEAT / ACCOMPANIMENTS)
- **Conditional formatting**: 
  - After W1 < 0 → red fill + white text
  - After W1 < demand W1 * 0.25 → amber fill
  - After W1 >= 0 → subtle green fill
  - Cut W1 input cells → blue fill (same as current)

### Tab 3: "Assignments" (PR-CJAM + CEX-EC)

Dedicated tab for assignment editing — no more horizontal scrolling.

```
PR-CJAM ASSIGNMENTS
HEADER: Curation | Cheese SKU (editable) | Jam SKU (editable) | W1 Count | W2 Count
MONG     | CH-BLR          | AC-RBOL         | 287      | 312
MDT      | CH-TTBRIE       | AC-SDF          | 290      | 380
...

[blank section]

CEX-EC ASSIGNMENTS  
HEADER: Curation | Cheese SKU (editable) | W1 Count (large boxes) | W2 Count
MONG     | CH-WWDI         | 8       | 12
...

[blank section]

GLOBAL EXTRAS
HEADER: Slot | Resolved SKU (editable) | W1 Count | W2 Count
CEX-EM   | MT-...          | 117     | ...
EX-EC    | CH-...          | 24      | ...
```

### Tab 4: "Monthly Boxes" (slot tables)

Informational — shows MONTHLY box counts by week and month.

```
AHB-MED (2026-04) — 350 W1 / 0 W2
  Slot        | Assigned SKU | W1 Count | W2 Count
  Cheese 1    | [editable]   | 350      | 0
  Cheese 2    | [editable]   | 350      | 0
  ...

AHB-MED (2026-05) — 0 W1 / 12 W2
  ...
```

### Tab 5: "Audit" (data verification)

```
DATA SOURCES
  Recharge charges: 4,444 queued (W1: 2,148 / W2: 2,296)
  Shopify orders: 89 W1 / 69 W2
  First-order projection: 166 in 3d, 55/day

DEMAND RECONCILIATION (per SKU)
  SKU | RC Direct | SH Addon | CJAM Resolved | CEXEC Resolved | Total
  [all 100 SKUs with full breakdown]

MISSING/NEW SKUs
  SKUs on Shopify orders not in inventory: [list]
  SKUs in inventory with 0 demand: [list]
```

## Visual Design Tokens (Excel)

### Colors
| Token | Hex | Use |
|-------|-----|-----|
| Header BG | #1E293B | Slate-800, section headers |
| Header FG | #FFFFFF | White text on dark headers |
| Shortage BG | #FEE2E2 | Red-100, deficit rows |
| Shortage Text | #991B1B | Red-800, deficit numbers |
| Tight BG | #FEF3C7 | Amber-100, tight rows |
| Tight Text | #92400E | Amber-800, tight numbers |
| OK BG | #F0FDF4 | Green-50, healthy rows |
| OK Text | #166534 | Green-800, positive numbers |
| Input BG | #EEF2FF | Indigo-50, editable cells |
| Input Text | #3730A3 | Indigo-800, user inputs |
| Surface | #F8FAFC | Slate-50, default background |
| Muted | #94A3B8 | Slate-400, secondary text |

### Fonts
| Element | Font | Size | Weight |
|---------|------|------|--------|
| SKU codes | Space Mono | 10pt | Regular |
| Names | DM Sans | 10pt | Regular |
| Numbers | Rajdhani | 12pt | Regular |
| Bold numbers | Rajdhani | 12pt | Bold |
| Headers | Space Mono | 11pt | Bold |
| Section titles | Space Mono | 10pt | Bold |

### Column Widths (Tab 2)
| Column | Width | Content |
|--------|-------|---------|
| A: SKU | 14 | Monospace |
| B: Name | 30 | Truncated |
| C: Avail | 8 | Right-aligned |
| D: RC W1 | 8 | Right-aligned |
| E: SH W1 | 8 | Right-aligned |
| F: +CJAM | 8 | SUMIF formula |
| G: +CEXEC | 8 | SUMIF formula |
| H: =Demand | 10 | Bold, sum of D:G |
| I: After W1 | 10 | Bold, conditional |
| J: Cut W1 | 9 | Editable, blue |
| K: Good? | 9 | OK/NEED |
| L: spacer | 2 | |
| M-T: W2 repeat | same | |

## Implementation Notes

- Build as `build_cut_order_xlsx_v2.py` — current version stays untouched
- Reuse same data fetching (`inventory_demand_report.py` imports)
- SUMIF references cross-tab (Assignments tab → Cut Order tab formulas)
- Sort shortages by `After W1` ascending (most negative first)
- Freeze panes: Row 1 header + Column A:B on Cut Order tab
- Named ranges for assignment cells (easier SUMIF references)

## Future: Auto-Discovery

Add a "New SKUs" section that queries Shopify for any CH-/MT-/AC- SKU 
that appeared on orders in the last 90 days but isn't in the current 
inventory settings. Flag them for the operator to add.
