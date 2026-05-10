# scripts/

One-off + recurring utility scripts. Not part of the apps or MCP servers — these are tools the operator runs ad-hoc. Categorized by purpose 2026-05-09 (was 35 loose `.py` in repo root).

See `README.md` (sibling) for the rules on what stays in repo root vs lives here.

## Layout

```
scripts/
├── swaps/           # SKU swap workflows (RMFG, Recharge bundle, Shopify edit)
├── audits/          # Data audits (mispick, distvol drift, RMFG truth, fulfillment QA)
├── incident-fixes/  # One-shot fixes for past incidents (FL routing, box-type backfill, etc.)
├── utilities/       # Misc helpers (CSV ops, lookups, ad-hoc queries)
└── archive/         # Retired scripts kept for reference
```

## Task Routing

| Task | Look in | Notes |
|------|---------|-------|
| Run a swap | `swaps/` | Only `_rc_bundle` items swappable; never touch paid extras |
| Audit data quality | `audits/` | Mispick: read ticket BODY, not tags (45% triage gap in Apr audit) |
| Re-apply a past fix | `incident-fixes/` | Read script header for context; many are dated/single-use |
| Generic CSV / lookup | `utilities/` | If script is generic enough, consider promoting to `appyhour_lib/` |

## Rules

- **No business logic here.** If a script grows into ongoing logic, move it to the appropriate app or `appyhour_lib/`.
- **Header docstring required** — what it does, when last run, who/why. Future-you will thank present-you.
- **Archive, don't delete.** Move retired scripts to `archive/` with a one-line comment in `README.md`.
- **Path audit on rename.** If you rename or move a script, grep for it in: vault `~/.knowledge/`, AAAK MEMORY.md, scheduled-tasks, hooks (`~/.claude/hooks/`), other scripts.

## Stays in repo root (NOT here)

Per `README.md`: anything imported by app code, the matrix_commander / matrix_commander_web entry points, conftest.py, build/test config. See sibling README for full list.
