# RESTORE — AppyHour New-Machine Setup Checklist

**Derived from `REBUILD-WITH-AI.md` (2026-06-11).** That file may be stale; this is the
runnable version. Work top-to-bottom. Run `python scripts/restore_check.py` at any point
to see what's still missing.

> ⚠️ **Biggest risk: the username changed.** The old machine was `C:\Users\Work\`. A LOT of
> config hardcodes `C:\Users\Work\` and `Claude Projects\`. If your new username is not `Work`,
> see **§6 Path Sweep** — nothing routing-related will work until those are fixed.

---

## 0a. ✅ PREFERRED: you have the old SSD — copy these first
If the dead machine's SSD is readable (USB-SATA adapter / enclosure), it is a **better source
than the Drive backups** — it has the creds, the full vault, and the live DB that were never
backed up. Copy these from the old disk (mounted as e.g. `E:\`) before falling back to Drive:

| From old disk (`Users\Work\...`) | To new machine | Why it matters |
|---|---|---|
| `AppData\Roaming\AppyHour\` (whole folder) | `%APPDATA%\AppyHour\` | **Creds + settings** (`gel_calc_shopify_settings.json`, `inventory_reorder_settings.json`, `portal_creds.json`, `*_api_key.txt`, `flow_api_token.txt`) — in NO backup. Skips §5 entirely. 🔴 **The live `shipping.db` is NOT here any more** (moved to `C:\AppyHourData\` on 2026-07-08) — see the row below; copying an old `shipping.db*` out of this folder onto the new box restores a corruption remnant. |
| `C:\AppyHourData\` (whole folder) | `C:\AppyHourData\` | **Live `shipping.db` + `-wal`/`-shm`, `backups\`, `heartbeats.json`, `carrier_tnt_cache*.json`.** This is the canonical data root since 2026-07-08. Skips §3. |
| `.knowledge\` (whole folder) | `~/.knowledge\` | Full vault, **no 06-11 gap** — better than the knowledge zip. Skips §4 vault. |
| `.claude\skills\` | `~/.claude\skills\` | Skills, current. |
| `.claude\hooks\` and `.claude\settings.json` | `~/.claude\` | Stop-hook, permissions, MCP prefs. |
| `AppData\Roaming\Claude\claude_desktop_config.json` | same path | MCP server registration (patch paths per §6). |
| `Claude Projects\AppyHour`, `ShipRouting`, `GelPackCalculator` | your projects root | **Run `git status` in each** — recover any uncommitted local changes the repos don't have. |
| Scheduled-task exports / `.bat` files | — | Reference for §7 (Task Scheduler entries live in the registry, not files — recreate via §7). |

After copying, run `python scripts/restore_check.py` — most rows should already be `[ OK ]`.
Then skip to §1 (runtime) and §6 (path sweep) — §3/§4/§5 are mostly satisfied by the disk copy.

## 0b. Gaps found in the 2026-06-22 restore (fold into the steps below)
Hard-won deltas — the original checklist missed these:
- **CommandCenter repo** — `InventoryReorder/fulfillment_web/app.py` (port 5187) imports `command_center` → `cc.engine` from a SEPARATE repo at `Claude Projects\CommandCenter`. Was SSD-only; now pushed to **`github.com/kurt-lgtm/CommandCenter`** (private) → clone it like the other repos (§2). `cc` state DB: `~/.cc/command_center.db`.
- **`claude-agent-sdk`** — the cut-order agents import it but it's NOT in `pyproject.toml`, so `pip install -e .[...]` misses it. Run `pip install claude-agent-sdk`. Nested `query()` uses the logged-in `claude` CLI (no `ANTHROPIC_API_KEY` needed).
- **`E:\AppyHourProd`** — standalone prod copy (its own AppyHour + ShipRouting), NOT under Claude Projects. Copy `E:\AppyHourProd` → `C:\AppyHourProd`.
- **Box-size lookup xlsx** — `box_simulation.py:20` hardcodes `C:\Users\Work\Desktop\Onboarded Items with DistVol - Updated.xlsx`; `build_lookup()` hard-crashes without it (blocks ALL routing/box runs). Recover from `E:\Users\Work\Desktop\`.
- **uv Python "dead symlinks"** — uv minor-version links can get POSIX targets (`/c/Users/...`) when uv runs under Git Bash → native uv can't follow them (`Missing expected target directory`). Fix: remove the bad links (patch dirs stay); don't auto-install uv Pythons from a Git-Bash shell.
- **Gitignored creds were never on GitHub** — `InventoryReorder/dist/{drive_oauth_token.json, inventory_reorder_settings.json}` (holds Shopify token, Recharge token, AND Gmail IMAP `smtp_user`/`smtp_password`), repo-root `shipping-perfomance-review-*.json` (Google SA). SSD-only → §5/§0a.
- **Scheduled tasks** — `schtasks /Create /XML /RU Work` (no password) PROMPTS + hangs. Use `Register-ScheduledTask -Xml ... -User Work -Force` (no prompt for InteractiveToken).
- **Sync perf (committed `restore/sync-perf-2026-06`)** — tracking fetch now skips delivered orders (+ index `delivery_status(order_number)`); FedEx IMAP download bounded `newer_than:14d`. Prevents the ~1hr "looks dead" catch-up.

## 0. Decide your paths first
Old machine used:
- Home: `C:\Users\Work\`
- Projects root: `C:\Users\Work\Claude Projects\`
- Python: `C:\Users\Work\anaconda3\python.exe`

If you can recreate the **same username `Work`**, do it — it makes every hardcoded path Just Work
and you can skip §6 almost entirely. If not, note your new paths; you'll patch them in §6.

## 1. Install the runtime
> ⚡ **One-shot:** from the repo root in an **elevated** PowerShell, run
> `powershell -ExecutionPolicy Bypass -File scripts\bootstrap_new_machine.ps1`.
> It installs everything below via winget + pip + npm. The manual list is the fallback.

- [ ] **Anaconda Python 3.10+** (gives you Python + Tcl/Tk for tkinter)
- [ ] **.NET Framework** runtime — pywebview MUST use the **netfx** backend, NOT coreclr/.NET 8 (Kori desktop)
- [ ] **Claude Code** — native installer, NO Node needed: `irm https://claude.ai/install.ps1 | iex`
- [ ] **git**, **gh CLI**, **Claude Desktop**
- [ ] **Node.js + npm** — needed ONLY for the Shopify dev-mcp server, not for Claude Code
- [ ] `pip install -e ".[dev,fulfillment,shipping,mcp]"` from the AppyHour repo root
- [ ] `npm i -g @shopify/dev-mcp` (the shopify-dev MCP server in `.mcp.json`)

## 2. Clone the repos (path-coupled — keep folder names exact)
Into your projects root (`...\Claude Projects\`):
- [ ] `github.com/kurt-lgtm/AppyHour`  (this repo)
- [ ] `github.com/kurt-lgtm/ShipRouting`  → **side-by-side** with AppyHour
- [ ] `github.com/kurt-lgtm/GelPackCalculator` → **INSIDE** `AppyHour/` (it's gitignored in this repo)

`kori/routing_v2.py` and ShipRouting hardcode `Claude Projects\AppyHour` + `Claude Projects\ShipRouting`
on `sys.path`. Keep these names or fix the sys.path blocks (§6).

## 3. Restore the database
> ⚠️ **Drive upload is BROKEN (as of 2026-06-24):** the weekly snapshot of `shipping.db`
> still runs LOCALLY (→ `%APPDATA%\AppyHour\backups\`), but the offsite push via
> `gws drive +upload` fails — `gws` is not installed / not on PATH — so new weeklies
> (back to ~2026-06-14) were **never uploaded**. The Drive ids below are the last that
> actually landed before the upload broke; anything newer lives only on this machine's
> `%APPDATA%\AppyHour\backups\` (recover from the old SSD per §0a). Fix the uploader
> first (reinstall/auth `gws`, or swap in rclone / the Drive API) — see §5a.
> Use the newest available, NOT the stale `pre-cutover` snapshot the old recovery doc named.
- [ ] **First check `C:\AppyHourData\backups\` (local) / the old SSD** for a snapshot newer than 06-21 — the local weeklies kept running even though the Drive upload didn't. 🔴 Snapshots moved with the DB on 2026-07-08; the legacy `%APPDATA%\AppyHour\backups\` folder stops at 2026-07-10 and (verified 2026-08-31) exists **only in the MSIX sandbox view**, so a real-context restore that looks there finds nothing.
- [ ] Download **`shipping.weekly-2026-06-21.db`** — id `1wcYREgMgGw339GYmDmzTBxhQsWDzhE1c` (130 MB; last one that reached Drive)
- [ ] Copy to **`C:\AppyHourData\shipping.db`** — 🔴 NEVER to `%APPDATA%\AppyHour\shipping.db`. That legacy MSIX-virtualized path is hard-refused by `appyhour_lib.paths.assert_canonical_db()` with no override (since 2026-08-31), so every WRITER dies on contact with a DB restored there — `db.connect()` and `shipping_invoice_db.init_db()` raise `NonCanonicalDBPath`. 🔴 It is NOT unreadable, and that asymmetry is the trap during a restore: `connect_ro()` is deliberately unguarded, so readers, reports and the shipping-data skill will happily open a DB restored to the legacy path and return plausible numbers while every ingest fails. A half-restored system that reads fine and writes nowhere is harder to diagnose than one that fails outright — restore to the canonical path and verify a WRITE, not a read. It is the second name behind the 2026-07-22 nine-day split-brain and the 6/27, 7/01, 7/03 WAL corruptions. Create `C:\AppyHourData\` if missing, and do not carry a `-wal`/`-shm` pair across.
- [ ] Catch up the gap since the snapshot: re-run importers (carrier emails re-downloadable from carrier portals + RMFG emails)
- [ ] Fallback if the weekly is corrupt: `shipping.weekly-2026-06-19.db` (id `1sTPOsEvR4FKo3_bcSYyOtHI5qVWVkGjZ`) or the original `shipping.pre-cutover-2026-06-11.db` (id `14Wjf8EOPnSqh_kwBPQSK9403AIAnq-4l`)

## 4. Restore vault + skills
- [ ] **`coldchain-logic-backup-2026-06-21.zip`** — id `1BHVQuZPs93tqKMKI_C_26Hm1ihmUtsE9` (latest; docs/handoffs/contracts/this file)
- [ ] ⚠️ **`coldchain-knowledge-backup-2026-06-11.zip`** — id `1zwKaRnmzchrKEsLNlgaU3A4s7OPydiUH` — extract to `~/.knowledge/` + `~/.claude/skills/`.
      **This is the ONLY knowledge backup on Drive and it's 11 days stale** — the weekly job backs up logic+DB but NOT the vault/skills. Expect to lose vault notes written since 06-11; reconstruct from the logic zip + git history. (See §5a — fix the backup job.)

## 5. Recreate creds + settings (NOT in any backup — re-enter by hand)
Create `%APPDATA%\AppyHour\` if missing, then:
- [ ] `gel_calc_shopify_settings.json` — Shopify creds, OpenWeatherMap key, per-state transit config, zip overrides
- [ ] `inventory_reorder_settings.json` — Shopify store URL + access token (used by `appyhour_lib/credentials.py`)
- [ ] `portal_creds.json` — FedEx/UPS billing portal logins
- [ ] For `cut_order_server/`: copy `.env.example` → `.env`, fill real values (FLASK_SECRET_KEY, RECHARGE_TOKEN, GOOGLE_SVC_ACCOUNT_JSON, DO Spaces keys, DATABASE_URL)
- [ ] Gmail/IMAP app passwords, Google OAuth service account (8 scopes), Gorgias API key, GitHub auth (`gh auth login`)

Alternatively set env vars instead of the JSON: `SHOPIFY_STORE_URL`, `SHOPIFY_ACCESS_TOKEN`,
`SHOPIFY_API_VERSION`, `OPENWEATHER_API_KEY` (see `appyhour_lib/credentials.py`).

## 5a. Backup gap — PARTIALLY FIXED 2026-06-22, but OFFSITE UPLOAD BROKE 2026-06-24
`scripts/backup_offsite.py` now also bundles `~/.knowledge/` + `~/.claude/skills/` into
`coldchain-knowledge-backup-<date>.zip` alongside the DB/logic snapshot — so the *content*
of the weekly is covered.
**⚠️ But the Drive UPLOAD is broken.** `backup_offsite.py` snapshots to
`%APPDATA%\AppyHour\backups\` fine, then tries to push offsite with `gws drive +upload` —
and `gws` is **not installed / not on PATH**, so the upload silently fails. The "weekly
offsite" has therefore been **LOCAL-ONLY** since ~2026-06-14: no DB *or* knowledge zip has
reached Drive since then. The newest knowledge zip on Drive is still **2026-06-11**.
- **Fix the uploader:** reinstall/auth the `gws` CLI, or replace the uploader with `rclone`
  / the Google Drive API, then run one backup and confirm the new zip/db actually appears on Drive.
- Until then, treat Drive as stale: restore from the dead disk's `~/.knowledge` + the local
  `%APPDATA%\AppyHour\backups\` if recoverable.
- Note: the **code** is still safely offsite via the three GitHub repos (AppyHour, ShipRouting,
  CommandCenter) — only the DB + vault/skills zips are stuck local.

## 6. Path sweep (ONLY if username/paths differ from `C:\Users\Work\Claude Projects`)
Files that hardcode the old path — patch each to your new path:
- [ ] `.mcp.json` — `command` + `args` python/server paths
- [ ] `AppyHourMCP/claude_desktop_config_snippet.json` and your live `%APPDATA%\Claude\claude_desktop_config.json`
- [ ] `AppyHourMCP/install.bat` (note: uses `C:\Users\Work\AppyHour`, no "Claude Projects" — old drift)
- [ ] `AppyHourMCP/register_wednesday_task.bat` → `BAT=` path
- [ ] `ShippingReports/*.bat` (`weather_sync_cron.bat`, `safety_factor_sweep.bat`, `melt_efficiency_calibrator.bat`, `postmortem_runner.bat`)
- [ ] `kori/routing_v2.py` + ShipRouting `lib/` sys.path blocks
- [ ] Any `command_center.py` / `postmortem_runner.py` absolute paths

`python scripts/restore_check.py --grep-paths` lists every file still containing `C:\Users\Work`.

## 7. Re-register scheduled tasks (Windows Task Scheduler)
> 💾 **Authoritative record is on the old SSD:** Windows stores every registered task as XML in
> `C:\Windows\System32\Tasks\` (look for ones named `AppyHour*` / `appyhour*`). Copy those out and
> re-import on the new box with `schtasks /Create /XML "task.xml" /TN "<name>"` — that captures the
> EXACT trigger/args, including any task not listed below. The list here is reconstructed from the
> repo and may be incomplete.

| Task name | Schedule | Target | Registration script |
|---|---|---|---|
| `appyhour_sync_on_logon` | At logon | `GelPackCalculator/sync_logon.py` (orchestrates sync_all_carriers + backfill_sync + auto_import) | none in repo — recreate from SSD XML |
| `AppyHour Wednesday Ops Run` | Weekly Wed 14:00 | `AppyHourMCP/wednesday_ops_run.bat` (incl. routing post-mortem) | ✅ `AppyHourMCP/register_wednesday_task.bat` |
| `AppyHour Weekly Offsite Backup` | Weekly Sun 02:00 | `scripts/backup_offsite.py` (⚠️ snapshots locally but Drive upload via `gws` is BROKEN — see §5a) | ✅ `scripts/register_backup_task.bat` |
| `AppyHour Weather Actuals` | Daily 03:00 | `ShippingReports/weather_sync_cron.bat` | none — `schtasks /Create` one-liner in `HANDOFF.md` |
| Postmortem runner (name TBC) | Mondays 09:00 | `ShippingReports/postmortem_runner.py` | none — schedule was a "suggestion"; confirm against SSD XML whether it was ever registered |

- [ ] Recover `C:\Windows\System32\Tasks\AppyHour*` XML from the SSD → re-import (authoritative)
- [ ] For the two with `register_*.bat`: just run the .bat (after the §6 path sweep)

## 8. Verify
- [ ] `pytest` (from repo root) — core logic green
- [ ] `python scripts/validate_refactor_db.py --copy <restored.db>` vs expectations
- [ ] `python GelPackCalculator/auto_import.py` — expect clean totals
- [ ] Launch Kori: `GelPackCalculator/kori/run_webview.bat`
- [ ] `python ShipRouting/build.py` on the current `_SHIP_` cohort
- [ ] In Claude Desktop, confirm the `appyhour` MCP server connects

---

### Non-negotiable domain rules (sanity check after restore)
- Cohort = `_SHIP_<Monday>` TAG, never scan date. On-time = delivered≤2 ÷ FULL cohort.
- Canonical DB columns: `zip_code` (never `zip`), `weather_history.zip_prefix` = 5-digit.
- Veho lanes = Indianapolis + Nashville ONLY. OnTrac ≡ LaserShip.
- Carrier cost order: Veho $6 → OnTrac $8 → UPS $11 → FedEx HD $15 → FedEx 2Day $25 (last resort).
