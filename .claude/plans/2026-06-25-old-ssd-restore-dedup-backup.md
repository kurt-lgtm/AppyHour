# Old SSD (E:) — Restore Audit, Dedup/Cleanup, Backup Architecture
**Date:** 2026-06-25 · **Drive:** E: external (Realtek USB), 238 GB / **213 GB used, 25 GB free** · **Status:** PLAN ONLY — no deletions/moves until approved.

> ⚠️ **Env quirk:** my tooling's filesystem view of `AppData`/`Downloads` can differ from the real machine (proven this session: portal_creds was visible to me but not Kurt's terminal). Every "is it already on C:?" check below is marked **[VERIFY native]** — Kurt confirms from a normal PowerShell before we act on it.

---

## PART 1 — RESTORE AUDIT (what E: still has that C: may lack)

Top-level of `E:\Users\Work` sized. Already restored (skip): the 3 repos, `%APPDATA%\AppyHour` data, `~/.knowledge`, `~/.claude` env, memory, scheduled tasks, `go\` (665M, incl nlm.exe), `bin\` (101K), `_outputs\` (2.5M).

| Item | Size | Verdict | Why |
|---|---|---|---|
| **`Repos\gorgias-reporting`** | 81M | **MUST [VERIFY native]** | A standalone project NOT under Claude Projects. C: has a `Repos\` dir but contents unconfirmed. If it has uncommitted/unpushed work → recover like we did `recover/ssd-main`. **Check: does it have a git remote? unpushed commits?** |
| **`Codex Projects\`** (auth.json, sessions, memories, skills, config.toml, state sqlite) | 104M | **NICE [VERIFY native]** | Codex CLI home — auth token, session history, learned memories/skills. C: has the dir; confirm it's populated vs a fresh shell. Auth + memories worth restoring; `cache/`, `tmp/`, `*.sqlite-wal` are disposable. |
| **`Documents\`** business files | 1.7G | **NICE (selective)** | `AHB_WeeklyProductionQuery_*.xlsx`, FedEx invoice reviews, `Box_Simulation_*`, `TAG_VALIDATION.docx`, `Documents\Codex\2026-04-25`. Restore the AHB/invoice/box-sim spreadsheets; skip `My Music/Pictures/Videos` junctions + DYMO/Office template cruft. |
| **`Desktop\`** | 1.4M | **NICE** | DistVol xlsx already restored. Also: `API shipstation.txt`, `mechanic ship date task 2.19.txt`, `Box_Simulation_*` outputs, app `.lnk` shortcuts. Tiny — pull the 2 `.txt` notes + any box-sim not already on C:. |
| **Edge + Chrome** `User Data\Default` | (in AppData) | **NICE (bookmarks/history only)** | Favorites/bookmarks + history are recoverable. **Passwords are NOT** (App-Bound Encryption — confirmed dead end this session). Plan: export bookmarks via the profile's `Bookmarks` JSON, skip the rest. |
| `Favorites\`, `Contacts\`, `Pictures\`, `Music\`, `Videos\` | — | **SKIP unless asked** | Personal profile data; Kurt's call, not AppyHour-critical. |
| `anaconda3\`, `AppData\Local\...\ms-playwright`, app caches | large | **SKIP** | Toolchains — reinstalled clean on the new box. Never restore; they're cleanup targets (Part 2). |
| `OneDrive\` | — | **SKIP** | Cloud-synced separately; local copy redundant. |

**Action for Kurt (native PowerShell, read-only) to close the audit:**
```powershell
git -C "E:\Users\Work\Repos\gorgias-reporting" log --oneline -5; git -C "E:\Users\Work\Repos\gorgias-reporting" remote -v; git -C "E:\Users\Work\Repos\gorgias-reporting" status -s
Get-ChildItem "C:\Users\Work\Repos","C:\Users\Work\Codex Projects" -Force | Select Name
```
Paste output → I finalize MUST/NICE and (with approval) pull the gaps the same non-destructive way we recovered the git branches + worktree files.

---

## PART 2 — DEDUP / CLEANUP PLAN (lean the old drive)

**Principle:** the old drive becomes a *cold archive of sources only*, not a profile clone. Delete anything (a) already safely on C:/GitHub, or (b) a derived/regenerable artifact. **Verify-before-delete, keep-one-of-each, nothing executed until approved.**

**Delete classes (est. recoverable: ~majority of the 213 GB):**
1. **Toolchains/binaries** — `E:\...\anaconda3`, `AppData\Local\...\ms-playwright`, browser caches → reinstalled on C:, never needed from E:.
2. **Derived build artifacts inside projects** — `node_modules/`, `.venv`/`venv/`, `__pycache__/`, `dist/`, `.codegraph/`, `.codebase-context/`, `*.egg-info/` across `Claude Projects` + `Codex Projects` (regenerable from source).
3. **Caches/transient** — `Codex Projects\{cache,tmp}`, `*.sqlite-wal/-shm`, `AppData\*\Cache`, `Downloads\` triage (1.1G / 1071 files — keep only invoices/exports not already ingested; rest deletable).
4. **Duplicate/backup spreadsheets** — `gel_calc_shopify_settings.*.bak*` (many), `Onboarded Items...2026-04-29.bak.xlsx`, dated `Box_Simulation_*` duplicates → keep newest + the canonical, drop the rest (canonical already on C:).
5. **Already-restored copies** — anything under E: byte-identical to its C: counterpart (the 3 repos, vault, .claude, %APPDATA% data).

**Safe-deletion method (when approved):**
- Build a **deletion manifest** first: `path, size, class, reason, c:_counterpart_verified(Y/N)`. Nothing deleted that isn't on the manifest with `verified=Y`.
- Stage to a `E:\_TO_DELETE\` holding folder (move, not delete) → let it sit one cycle → then purge. Reversible until final purge.
- Dedup detection: hash-compare (`Get-FileHash`) C: vs E: for the "already-restored" class before removing.
- **Never** touch `OneDrive\` or personal `Pictures/Music/Videos` without explicit per-folder OK.

---

## PART 3 — BACKUP ARCHITECTURE (forward-looking, not old-PC-shaped)

**Reject the old model** (whole-profile sprawl that made this restore painful). New model = **back up SOURCES by tier, not a machine image.** Builds on existing `scripts/backup_offsite.py` (scrypt+Fernet creds encryption, `AH_BACKUP_PASSPHRASE`, `drive_oauth_token.json` OAuth) and the `backup-coverage-gaps` memory.

**Tiers & destinations:**
| Tier | What | Where | Cadence |
|---|---|---|---|
| **A — Code** | AppyHour, ShipRouting, CommandCenter, GelPackCalculator, gorgias-reporting | **GitHub remotes (source of truth)** — every repo has a remote + is pushed | on commit (hook/CI) |
| **B — Secrets** | `portal_creds.json`, drive/SA tokens, `gel_calc_shopify_settings.json`, `inventory_reorder_settings.json`, `.env`s | **encrypted bundle → Drive** (`backup_offsite.py`, scrypt+Fernet) | weekly + on change |
| **C — Live data** | `shipping.db`, `~/.knowledge` vault, `~/.claude` memory + magic-claude-mem store | **Drive, versioned** (keep last N) | daily |
| **D — Reference/business** | DistVol xlsx, production queries, box-sim inputs, key Documents | **Drive folder** | weekly |
| **E — Config inventory** | a `RESTORE-MANIFEST.md` listing every path, its tier, and restore command | in the AppyHour repo | on change |

**Principles:**
- **Source over derived:** never back up `node_modules`/venvs/caches/toolchains (the old drive's bloat). Restore = clone + reinstall + pull data tiers.
- **Encryption mandatory for Tier B**; passphrase lives only in the User env var, never on Drive (existing pattern).
- **Restore is checklist-driven:** the `appyhour-machine-restore` skill + `RESTORE-MANIFEST.md` make the next migration a runbook, not an archaeology dig.
- **Quarterly restore-test:** dry-run restore into a temp dir, verify `restore_check.py` passes — catches backup rot before it matters.
- **Retention:** Tier C daily × 14, weekly × 8; Tier B/D weekly × 8.

**Net:** GitHub holds code, Drive holds (encrypted) secrets + data + reference, a manifest ties it together. No more 213 GB profile to sift.

---

## SWARM FINDINGS + RECOVERY (2026-06-25, 3 parallel read-agents)

**Major correction:** E: is NOT just stale dups. ShipRouting + AppyHour are **divergent branches** — E: holds real, single-copy work C: never had. The "C is canonical, delete E" premise was unsafe; caught by reading the files.

**RECOVERED to C: (63 files, copy-if-absent, non-destructive) — DONE:**
- **ShipRouting (37):** `lib/zone_floor.py` (fixes a dangling import in C's `full_cohort_dryrun.py`), `lib/fedex_tnt.py`, `lib/invariants.py` (the feasible-hub-fence/ice-floor line), 19 tests, 8 apply/upload scripts, `ROUTING_RULES.md`, `EXPRESS_RULESET.md`, `HANDOFF.md`, `INVESTIGATE.md`, 3 plans. ⚠️ This is a *second work-line* — C is on the MILP/carrier-TNT epic; reconcile before merging to main.
- **AppyHour (26):** 🔒 `.env` (16 keys — C had NONE), Gmail OAuth creds, `gel_calc_settings.json`; entire `scripts/automations/` wrong-address tree + non-reproducible `_learning/decisions.jsonl`; `reconcile_lost_in_transit.py` + `pp_backfill_aged_out.py`; `zip_overrides_*.json`; `ShippingReports/config.yaml` + `output/zip_overrides.json`; `mfg_translations.csv`; `TOOL_REGISTRY.md`; incident-fix scripts; `.spec` build files; `test_notify.py`. All 4 secrets verified **gitignored** (AppyHour is public).
- Not found: `FulfillmentPlanner.spec` (name/path off — recheck).

**Refined E: deletion classes (post-recovery):**
- **SAFE DELETE (identical dup / junk):** odysseus, Cowork, gorgias-reporting, Codex Projects (100% identical to C); ShipRouting `*.bak`; AppyHour `.ruff_cache` + ~118MB dated CSV/XLSX dumps + retired DBs (`shipments.db.RETIRED`, 20MB) + generated JSON dumps (64MB); `_outputs` cache/logs/`coldchain-knowledge-backup` (dups live vault)/vendored mermaid (~27MB); toolchains (anaconda3, ms-playwright).
- **ARCHIVE → backup, THEN delete:** `_outputs` reports/research/postmortems/diagrams + RMFG invoice PDFs/xlsx (~20MB); ShipRouting 3 plans; AppyHour postmortems/plans/coldchain zips.

## 🔒 PROTECTED — NEVER DELETE (added 2026-06-25)
- **`E:\Users\Sherry`** — a SEPARATE Windows user account (not `Work`), **6.1 GB** of real data: Desktop/Documents/Downloads/Favorites/Music, her own `NTUSER.DAT` registry hive, and browser history (Edge 688 KB + Chrome 196 KB). Entirely outside the `E:\Users\Work` cleanup scope and explicitly off-limits. No deletion/move/scan touches `E:\Users\Sherry`, `E:\Users\Public`, or any non-`Work` profile. (ACL-bound to her SID — reads need elevation anyway; do not.)

## WIRING VERIFICATION + FIXES (2026-06-25, 3-agent swarm + applied)
Swarm verified the 63 recovered files. **AppyHour: clean after fixes. ShipRouting: divergent-line API drift (decision needed).** Hardcoded-path audit: CLEAN (no old-machine/E: paths; outputs correctly route to _outputs).

**FIXED + verified:**
- ShipRouting `lib/zone_floor.py` — dangling `lib.origin.is_ground_service` → `lib.canon.is_ground(normalize_service(...))` (semantics identical); imports clean now.
- AppyHour `GelPackCalculator/parcel_panel.py` — restored `pp_fetch_concurrent` (surgical merge from E: + `threading`/`concurrent.futures` imports; C:/E: had diverged so no wholesale copy). `pp_backfill_aged_out.py` import resolves now.
- `.env` — added `OPENWEATHER_API_KEY` alias (code reads that, not `OWM_API_KEY`).
- `mfg_translations.csv` — copied to repo root (matrix_commander expects it there, not `data/inputs/`).
- `GelPackCalculator.spec` — icon `kori.ico` → `kori/kori.ico`.
- `.gitignore` — added `*.egg-info/`, `*.bak`, `*.committed-bak` (so egg-info + `shopify_swap.py.committed-bak` don't get committed to the public repo).

**OPEN DECISION — ShipRouting divergent feasible-hub-fence/ice-floor line:** `lib/invariants.py`, `scripts/full_cohort_dryrun.py`, and 8 tests still reference 8 symbols the canon branch refactored away (`sizing_temp`, `resolve_ice_target`, `origin_hub_from_tag`, `_tiered_delay_rate`, `validate_zip_overrides`, `Z5_MIN_N`, `_HUB_TEMP`, `_LIVE_HUB_TEMP_CACHE`). Live engine path is UNAFFECTED (no production module imports them). Choose: **PORT** the line to the current canon lib API, or **ARCHIVE** it to `_archive/`. Do not leave non-importable modules in `lib/`.

## EXECUTION GATING
This pass is analysis only. Next steps, each separately approved:
1. Kurt runs the Part-1 verify commands → I finalize + pull true gaps (non-destructive).
2. I generate the Part-2 **deletion manifest** (read-only) → Kurt reviews → staged-move → purge.
3. I implement Part-3 (extend `backup_offsite.py` tiers + write `RESTORE-MANIFEST.md` + schedule).
