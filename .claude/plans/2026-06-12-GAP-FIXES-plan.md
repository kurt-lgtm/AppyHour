# GAP FIXES — Infrastructure Hardening Plan (post-M1)

**Date:** 2026-06-11 (late) · From the system assessment + M2 queue. Owner split: Codex = mechanical
builds with clear specs; Claude = anything touching domain judgment, prod gates, or MCP/connected services.
**Contract still binding:** `.claude/plans/2026-06-11-ORCHESTRATION-claude-codex-coldchain.md`.

| # | Gap | Fix | Owner | Status |
|---|---|---|---|---|
| G0 | Weather actuals dead | daily 3am task `AppyHour Weather Actuals` | — | ✅ DONE 6/11 (registered, next run 6/12 03:00) |
| G1 | Backups single-disk | `scripts/backup_offsite.py`: sqlite-API snapshot → zip docs → `gws drive +upload`; weekly Sun 02:00 task | **Codex** | 🔨 dispatched |
| G2 | Logon-coupled scheduling | `GelPackCalculator/pipeline_run.py`: one orchestrated entry (downloaders → auto_import → backfill → weather) with per-step status JSON; daily 07:00 time-trigger; sync_logon kept as fallback | **Codex** | 🔨 dispatched |
| G3 | Alerts go to log files | Slack webhook notifier: tiny `appyhour_lib/notify.py` (env `AH_SLACK_WEBHOOK`, fail-silent) + call from pipeline_run summary, postmortem escalation, heartbeat failures | **Codex** (lib+wiring) / **Kurt** (create webhook, set env) | 🔨 dispatched |
| G4 | Working tree = prod (logon task executed mid-build code 6/11) | `C:\AppyHourProd\` pinned checkout pattern: `scripts/deploy_prod.py` does git pull --ff-only at fixed tag/branch into prod dir; scheduled tasks repointed there | **Codex** builds; **Claude+Kurt** approve repoint | 🔨 dispatched (build only — NO task repoint without approval) |
| G5 | Portal invoices (the 113-acct lag) | `portal_pull.py` Playwright FedEx Billing + UPS Billing Center → scan folders | **Codex** later | ⏸ BLOCKED: Kurt fills `%APPDATA%/AppyHour/portal_creds.json` |
| G6 | Veho weekly file manual | `veho_watcher.py`: detect `Veho_GroundPlusSuite_*.xlsx` in Downloads → `veho_coverage.py` parse (GP-Zero, IN+TN) → churn sanity (>20% active-flips = abort+alert) → archive + atomic stable-file update | **Codex** | 🔨 dispatched |
| G7 | Counter cosmetics + unknown remnants | re-run counters report INSERTED not scanned; surface ignored_noise in heartbeat | **Codex** | 🔨 dispatched |
| G8 | Engine has no tests | pytest suite for choose_lane: partition/probation/temp-gate/tag-grammar/Indy-gate cases | **Claude** (domain fixtures) | next session |
| G9 | M4 remainder | `!ExtraGel48oz_x2!` vocab + RMFG recipe, box-upgrade neg-margin, lock-gate | **Claude+Kurt** (RMFG coordination) | next session |
| G10 | M5 go-live | 6/15 cohort: build→validate→Bree/Pam→dry-run→apply (Fri 6/13 deadline) | **Claude+Kurt** | scheduled |
| G11 | SQLite contention | WAL | — | ✅ DONE 6/11 |

**Codex rules of engagement (unchanged):** nested GelPackCalculator repo for its code · branch `infra/gap-fixes` · never write live DB in tests (AH_DB_OVERRIDE) · never repoint a scheduled task (build scripts only — repoint = Claude+Kurt gate) · no secrets in code (env/ACL'd files) · I verify every claim vs git+fs before merge.
