# vF Archive — what we actually told RMFG to pack (constraints SSOT)

🔴 **PRE-CHANGE GATE.** Read before touching `scripts/fetch_vf_from_email.py`,
`scripts/vf_archive.py`, or anything that answers *"how many units did we assign in week W."*

> 🧭 **NORTH STAR.** For any ship week and any product, answer **"how many units did we tell RMFG to
> pack"** from a durable, re-derivable record — so assigned can be reconciled against produced
> (RMFG yield) and on-hand inventory without asking a human which file was the real one.

Companions: `VF_SHEET_RULES.md` (the sheet's own structure/QC), `MATRIX_RULES.md` (how it is built).
This doc owns only the ARCHIVE and the counting contract.

---

## Why this exists — failure-first

- **wk0727's vF was gone from every local path.** Only survivor on disk was the 93-row Tuesday leg,
  so "how many KM39 did we assign that week" was unanswerable. The mailbox had it the whole time.
- 🔴 **A local file with the right NAME is not the sent sheet.** August 2026: KM39 for wk 08-31 was
  reported as **163** from `_outputs/artifacts/…08-31-26_vF_r2.xlsx` (1,948 orders). The sheet
  actually emailed to RMFG (`TAG: RMFG_20260828 // ORDERS 2471`) says **188**. Same stem, 523 orders
  short, 25 units wrong — and nothing in the filename says which one shipped.
- 🔴 **A ship week is MORE THAN ONE SHEET.** Reporting only the Friday leg undercounted August KM39
  by 14 units across four Tuesday legs plus an 08-06 drift-in. The week is a UNION
  ([[multi-leg-shipweek-union-doctrine]]).

## The rules

1. 🔴 **THE EMAILED ATTACHMENT IS THE AUTHORITY.** The archive ingests from
   `C:\AppyHourData\vf_archive\raw\` — attachments pulled off the messages we actually sent RMFG.
   A file in `Downloads`, `_outputs/artifacts`, or a repo root is a WORKING COPY and may not be
   counted, no matter its name ([[vf-sheet-is-the-routing-authority]]).
2. 🔴 **Never overwrite, never dedupe by name.** Same name + different bytes is saved `__dupN` and
   both are ingested; the DB keeps every file keyed by content hash
   ([[never-delete-prior-output-files]]).
3. 🔴 **The Gmail query is the bare term `WeeklyProductionQuery`.** `filename:AHB_WeeklyProduction…`
   returns **ZERO** — a false absence, not an empty mailbox. A search zero is never proof of absence
   ([[gorgias-ticket-search-incomplete-recall]]).
4. 🔴 **A ship week's assigned count = the UNION of its legs**: the Friday sheet, its Tuesday sheet,
   and any drift-in re-run sent that week. `ship_week` is derived from the mail's `RMFG_` cohort tag
   where present, and from the filename date otherwise — never from mtime.
5. 🔴 **Product identity is the header string `AHB (S_REG): <MFG name>`, matched by NAME not index**
   (columns shift on every Excel round-trip). Names are not normalized, merged, or "close-enough"
   matched — an unrecognized header is stored verbatim and reported, never mapped to a guess
   ([[never-fabricate]], VF_SHEET_RULES §2).
6. **A cell counts only if numeric.** Blank / `0` / `None` are not-in-box (VF_SHEET_RULES §1).
7. **Ingest is idempotent and append-only.** Re-running never mutates a stored file's rows; a file
   already ingested (by hash) is skipped.
8. **stdout is UTF-8.** RMFG subjects carry emoji and Windows cp1252 kills a long fetch mid-run —
   that crash cost a full backfill once ([[windows-python-cp1252-stdout-crash]]).

## Not covered (know the gap)

- **Assigned ≠ packed.** The sheet is what we ASKED for. Shorts, swaps, and RMFG substitutions after
  send are not in it; reconcile those against the yield PDFs and swap ledger, not this DB.
- **Produced** lives in the RMFG breakdown PDFs
  (`_outputs/artifacts/2026-06-17-rmfg-production-invoices/`, pulled by
  `fetch_rmfg_production_imap.py`) — a separate authority, joined only at report time.
- **Opening inventory** comes from the week's HAVE count, which this DB does not hold.
  Balance = `prior_end + produced(week) − assigned(week)`, and production dated Sunday serves the
  ship week that starts the next day (8/2 → wk 08-03).
