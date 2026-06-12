# GAP FIXES — Infrastructure Hardening Plan (post-M1)

**Date:** 2026-06-11 (late) · From the system assessment + M2 queue. Owner split: Codex = mechanical
builds with clear specs; Claude = anything touching domain judgment, prod gates, or MCP/connected services.
**Contract still binding:** `.claude/plans/2026-06-11-ORCHESTRATION-claude-codex-coldchain.md`.

| # | Gap | Fix | Owner | Status |
|---|---|---|---|---|
| G0 | Weather actuals dead | daily 3am task `AppyHour Weather Actuals` | — | ✅ DONE 6/11 (registered, next run 6/12 03:00) |
| G1 | Backups single-disk | `scripts/backup_offsite.py` | Codex build + Claude fix/verify | ✅ DONE 6/12 — ran live (snapshot+zip→Drive), gws shim bug fixed, **weekly Sun 02:00 task registered** |
| G2 | Logon-coupled scheduling | `GelPackCalculator/pipeline_run.py` | Codex + Claude verify | ✅ BUILT (4 tests green, notify wired, AH_DB_OVERRIDE propagation). Daily 07:00 task NOT yet registered — register after first manual supervised run |
| G3 | Alerts go to log files | `appyhour_lib/notify.py` + wiring (wednesday postmortem escalation, pipeline summary) | Codex + Claude verify | ✅ BUILT, fallback verified. **Live alerts blocked on Kurt: webhook URL (KURT-TODO #4)** |
| G4 | Working tree = prod | `scripts/deploy_prod.py` → `C:\AppyHourProd\` pinned checkouts | Codex + Claude fix/run | ✅ DEPLOYED (AppyHour@c85d9e5, GelPack@3c6cc20, ShipRouting@97618ce; fixed ShipRouting branch). **Task repoint gated on Kurt (KURT-TODO #7)** |
| G5 | Portal invoices (the 113-acct lag) | `portal_pull.py` Playwright FedEx Billing + UPS Billing Center → scan folders | **Codex** later | ⏸ BLOCKED: Kurt fills `%APPDATA%/AppyHour/portal_creds.json` |
| G6 | Veho weekly file manual | `veho_watcher.py`: detect `Veho_GroundPlusSuite_*.xlsx` in Downloads → `veho_coverage.py` parse (GP-Zero, IN+TN) → churn sanity (>20% active-flips = abort+alert) → archive + atomic stable-file update | **Codex** | 🔨 dispatched |
| G7 | Counter cosmetics + unknown remnants | re-run counters report INSERTED not scanned; surface ignored_noise in heartbeat | **Codex** | 🔨 dispatched |
| G8 | Engine has no tests | pytest suite for choose_lane: partition/probation/temp-gate/tag-grammar/Indy-gate cases | **Claude** (domain fixtures) | next session |
| **G12** | **ADDRESS-QUALITY CLOSED LOOP** (Kurt 2026-06-12: "part of shipping — fold into the closed loop") | ONE pipeline, TWO detectors feeding it: **(a) pre-ship** = Shopify `invalid_address` tag (EasyPost) — triage proved 63% false-positive on rural; **(b) post-ship** = carrier-invoice address-correction fees (FedEx detail CSV cols: `Address Correction Gross Charge`, `Tracking ID Charge Description` + corrected `Recipient Address` block; ~$24/fee; UPS equivalent TBD). **Fixer stages:** 1 merge split number/street (A1+A2) · 2 typo/spacing fix · 3 maps-verify (WebSearch) · 4 rural-false-positive clear (untag, ship as-is; BARE unit numbers KEPT — sometimes not apts) · 5 ambiguous → digest for Kurt. **Apply:** order + customer default + Recharge subscription (match-guarded: only overwrite the OLD broken value) + REMOVE tag (today the tag is never removed — 100+ stale since 2025, cleanup included). **Wire:** pipeline_run step + D8 detector in anomaly_scan + fix-list in the Slack issues report. **Built today (manual run, proven):** triage `_outputs/reports/2026-06-12-invalid-address-triage.md` · order-fixer `scripts/incident-fixes/fix_invalid_addresses_2026-06-12.py` (37/37 ok, 6/15 cohort clean) · propagator `propagate_address_fixes_2026-06-12.py` (dry-run matched 11). | **Claude** (generalize scripts → pipeline step); **Kurt** unblocks scopes | ⏸ propagation BLOCKED: Shopify app needs `write_customers`, Recharge token needs address write. Then rerun propagator + build the recurring step. |
| G9 | M4 remainder | `!ExtraGel48oz_x2!` vocab + RMFG recipe, box-upgrade neg-margin, lock-gate | **Claude+Kurt** (RMFG coordination) | next session |
| G10 | M5 go-live | 6/15 cohort: build→validate→Bree/Pam→dry-run→apply (Fri 6/13 deadline) | **Claude+Kurt** | scheduled |
| G11 | SQLite contention | WAL | — | ✅ DONE 6/11 |

**Codex rules of engagement (unchanged):** nested GelPackCalculator repo for its code · branch `infra/gap-fixes` · never write live DB in tests (AH_DB_OVERRIDE) · never repoint a scheduled task (build scripts only — repoint = Claude+Kurt gate) · no secrets in code (env/ACL'd files) · I verify every claim vs git+fs before merge.
