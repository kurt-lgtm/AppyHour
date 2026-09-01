# RESOLVE_DUPES_RULES.md — Matrixify import dupe resolution (SSOT)

> 🔴 PRE-CHANGE GATE — single source of truth for `scripts/resolve_import_dupes.py`.
> Change rules HERE first, same commit as any code change. Process doc:
> `~/.claude/skills/matrixify-import-dupe-check/SKILL.md` (3-phase workflow).

## 🧭 North Star

Turn a raw Matrixify "add line item" export into an import that lands clean on the
first pass: every duplicate add detected before import, every CH-/MT- dupe replaced
with a SKU the customer has **never received**, zero fabricated identities, zero
edits to live Shopify — output is a corrected CSV, nothing else.

## 🔴 Negatives first (the failures this tool exists to prevent)

1. **Never pick a replacement the customer already has.** A pick must clear ALL of:
   the order's current box (live, `currentQuantity>0`), the customer's FULL order
   history (by email), the SKUs this import already adds to that order, and picks
   already made for the same order (distinct substitutes per order). A live-only
   check re-creates the dupe one import later.
2. **Dietary restrictions (NNRS/CORS/NCRS) gate curation replacements.** If the
   order or customer carries a restriction tag, the tool does NOT auto-pick — it
   flags `NEEDS-DIETARY-REVIEW`. Encoding a guessed exclusion list = fabrication.
3. **Never fabricate SKU identity.** Candidate pool = $0 in-box SKUs already present
   in this import sheet (product id + handle taken verbatim from the sheet). No
   candidate clears → `MISSING`, never an invented SKU/product-id. Verify any
   manually-added extra via `appyhour_get_product` before use (CH-LOU/CH-FAG shared
   an id once).
4. **Dry-run is the default; `--apply` only writes a CSV.** The tool NEVER edits
   Shopify (READ-ONLY GraphQL). Output is a Matrixify file the operator imports.
5. **Never overwrite the input export.** Corrected file versions alongside in
   `_outputs/artifacts/` as `<base>_RESOLVED-<TAG>.csv`; refuses to clobber an
   existing output (versions `-2`, `-3`, ...).
6. **In-sheet dedupe on output** — swapping every matching row of an in-sheet dupe
   creates a NEW dupe of the replacement; each order+sku collapses to one row.
7. **AC- dupes are dropped, not replaced** (Kurt standing call) — surfaced in the
   decision log so the operator can override.
8. **Presence check = `currentQuantity>0`** (fallback `quantity`). Never raw
   `quantity` (refunded items → false dupes) and never `fulfillableQuantity`
   (fulfilled items → missed dupes). See skill gotchas.
9. Windows: CSVs utf-8-sig; no non-ASCII to stdout (cp1252).

## Contract

`python scripts/resolve_import_dupes.py --export <csv> --ship-tag <TAG>
 [--warnings <txt>] [--apply]`

- Detect (Phase A): wraps `scripts/utilities/check_import_dupes.py` bulk read +
  dupe classes (live dupe + in-sheet dupe); optional `--warnings` reuses a prior
  WARNINGS.txt instead of re-detecting.
- Split: DUPES vs CLEAN order sets (in-memory).
- Pick (Phase B): history-aware per rules 1–3 above.
- Emit: decision log `<base>_RESOLVE-DECISIONS-<TAG>.txt` (order, dupe sku,
  replacement, why) always; corrected CSV only with `--apply`.
- Reports orders MISSING from the tag — never silently skipped.

Replaces the dated one-shots `scripts/utilities/resolve_dupes_2026_*.py`,
`resolve_0710_1.py`, etc. Do not copy those again — extend this tool.
