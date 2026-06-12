# AppyHour Scripts

Ad-hoc and one-shot Python scripts. Organized by purpose. Reorganized 2026-05-08.

## Subdirs

| Dir | Purpose | When to add here |
|-----|---------|------------------|
| `swaps/` | SKU swap generation + verification | New swap script for a specific cohort |
| `audits/` | One-shot data audits + comparisons | Checking item counts, comparing exports, verifying box CSVs |
| `incident-fixes/` | Reactive scripts for specific past incidents | Fixing data anomalies (paid IPAC, alpha repair, ops gaps) |
| `utilities/` | General-purpose ad-hoc tools | Drive lookups, onboarding docs, food-safety sync tests |
| `archive/` | Truly dead — kept only as historical reference | Don't add here directly; demote from above subdirs after 90+ days unused |

## Stays in `AppyHour/` root (load-bearing — DO NOT MOVE)

- `matrix_commander.py` — wrapped by `matrix_commander_web/`
- `build_ops_summary.py` — referenced by audit cycles
- `audit_distvol_drift.py` — active distvol audit
- `box_simulation.py` — active simulation
- `conftest.py` — pytest config (must be at root)
- `agent_sdk_example.py` — example/reference

## Conventions

- Scripts here are **one-shot or ad-hoc**. If a script becomes load-bearing (used by hooks, MCP, scheduled tasks), promote it to a proper module under the appropriate app folder (`InventoryReorder/`, `ShippingReports/`, etc).
- No `.bak` files — git is the backup.
- No `tmp_*.py` files — delete after use, or move to `archive/` if useful for reference.
- New script → land in the right subdir from the start, don't drop in root.
