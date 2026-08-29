# Automation Heartbeats / Dead-Man-Switch — Constraints (single source of truth)

> 🔴 **PRE-CHANGE GATE:** read this before touching `appyhour_lib/heartbeat.py`,
> `scripts/automation_health.py`, or any task wrapper's `beat()` call. Change rules HERE first, same commit.

> 🧭 **NORTH STAR:** no automation failure stays silent past one checker cycle — silence IS the
> failure signal.

**What it is:** the inversion the Slack-on-completion hooks structurally can't do — **a task that never
runs sends no Slack**, so silence looked like success (7 of 14 schtasks failed silently for a week,
2026-07-02 audit; ingest heartbeat sat 5 days stale). Fragile tasks write a heartbeat on success;
one daily checker alarms on ABSENCE. Plan origin: `.claude/plans/2026-07-02-absorbed-tools-to-production.md`
TASK 4.1 (healthchecks dead-man-switch pattern, local variant).

## Rules (negatives-first)

1. **Silence is the failure signal — never make the checker success-only.** The checker alarms on
   missing/stale beats AND its own inability to read the ledger. A checker that only reports
   what it found re-creates the original blind spot.
2. **`beat()` must NEVER fail the host task.** It is fire-and-forget (try/except swallow, atomic
   temp+replace write). A backup that succeeded but couldn't record a heartbeat must still exit 0 —
   the checker will alarm the missing beat, which is the correct signal, not the task failing.
3. **The ledger is `%APPDATA%/AppyHour/heartbeats.json` — NOT shipping.db.** Nothing in this system
   touches shipping.db read-write, ever (MSIX+WAL corruption). The checker's DB health probe opens
   `mode=ro&immutable=1` only.
4. **Expectations live in the checker, not the ledger.** A task that stops being scheduled must be
   removed from `EXPECTED` in the same change — a stale expectation = permanent false alarm, which
   trains alarm-deafness (the failure mode that killed the old monitoring).
5. **Anomaly-first Slack** (per `feedback-appyhour-tasks-slack-summary`): silent when green; one
   consolidated message when red, via canonical `appyhour_lib.notify.notify()` — never a new webhook,
   never MCP from a scheduled run (`scheduled-tasks-use-cli-not-mcp`).
6. **External watcher for the backup only** (healthchecks.io, T4.1a): env `HEALTHCHECKS_BACKUP_URL`
   → success-only GET ping at the end of `backup_offsite.py`. If unset, silently skipped (Kurt owns
   the account). The LOCAL checker still covers it — external is belt-and-suspenders for machine-dead.
7. **Checker self-beat:** `automation_health.py` writes its own beat last. If the checker itself dies,
   the NEXT run (or a human reading the ledger) sees it. Accepting the honest ceiling: a fully dead
   machine alerts nothing locally — that's what rule 6's external ping is for.
8. **A partial run must NEVER stamp `ok`.** A multi-leg task (e.g. `sync_all_carriers` = FedEx +
   OnTrac + Veho) finishes every leg, then **raises** with the collected failures so its caller
   stamps `fail:`. 🔴 2026-07-27: it swallowed all three legs' `FileNotFoundError` and returned
   normally — `carriers: ok` on a run where zero invoices were pulled. Per-leg resilience (one
   carrier's hiccup can't block the others) is NOT permission to report success.
9. **Prod-tree parity is a monitored invariant — an undeployed fix is not a fix.** Scheduled tasks
   run from `C:\AppyHourProd\AppyHour`, a separate copy of the dev tree; `check_prod_parity()`
   alarms when a DB-relevant dev file is newer than its prod counterpart. 🔴 Four split-brain
   incidents in a row (07-13, 07-22, 07-24, 07-27) were "already fixed" in dev while prod ran the
   old file — 07-27's root cause was prod holding the 07-08 file-keyed `paths.db_path()` under a
   deployed 07-22 guard, so the guard called a stale resolver and wrote legacy silently. **Deploy a
   guard and its resolver together, or neither.** NEGATIVE: the check reports dev-NEWER only and
   never suggests a sweep — some prod files are legitimately newer (local hotfixes), and blanket
   dev→prod copying clobbers them. The deploy step is `scripts/deploy_prod.py` (2026-08-29:
   dry-run default, same tracked set as the check, REFUSES while any file is newer in prod —
   `--apply` is Kurt's call; the old git-pull deploy in that file is dead, origin/main is
   hundreds of commits behind dev).
10. **Don't wire `beat()` into files another agent has mid-flight** — coordinate first (2026-07-02:
   daily_shipping_sync deferred while the writelock migration owns those files; checker covers it via
   `sync_heartbeat.json` age instead).
11. **A cloud-replica table gets a DAILY data-age probe here, never only the weekly sweep's whole-table
   gate.** 🔴 2026-08-26: local `shopify_orders` (replica of the cloud MySQL primary) sat **9 days
   stale** behind the sweep's 14d gate after the single weekly Monday pull died ONCE on a transient
   DO MySQL 2003 connect timeout (8/24) — no retry until the next Monday, 1,513 of
   `_SHIP_2026-08-24`'s orders missing locally, carrier-mix Pending denominator at 40.2%. The cloud
   primary was current the whole time; only the local leg was dead. `check_replica_freshness()` now
   probes two INDEPENDENT signals daily: table DATA age (`shopify_orders` >4d, `weather_history`
   >9d) and the ingest STAMP `C:\AppyHourData\replica_pull_stamp.json` (>4d; written by
   `daily_shipping_sync.run_cloud_replica_pull`, which also retries 3× in-run). NEGATIVE: never
   collapse the two — a pull that runs but moves nothing passes the stamp and trips the data age; a
   dead pull behind a fresh-looking table trips the stamp. The stamp is ingest METADATA, distinct
   from the order-placed `created_at` DATA column (an ingest timestamp is not an event date). The
   stamp lives beside the canonical DB, NOT `%APPDATA%` — MSIX virtualization can mask a
   real-profile write there from this checker's sandboxed run. A MISSING stamp is a loud finding by
   design (deploy nag until the prod copy carries the pull stage).

12. **A finding that repeats 3 consecutive runs must become DISPATCHED WORK, not a re-sent alert.**
   🔴 2026-08-29 (harness-efficiency-review, "The one systemic finding"): this checker re-reported
   identical findings daily for a MONTH — ingest heartbeat staleness re-alarmed as it aged 7d→28d,
   prod-tree drift at 9→12→20 undeployed files — alerts fired, nobody owned the fix. Detection
   without dispatch is "naming an owner is not dispatching," violated by the machines.
   `dispatch_findings()` now maps each finding to a stable per-entity key (`finding_key()` —
   variable parts like ages/counts must never reach the key) and feeds
   `Claude Projects\_coordination\finding_dispatch.py`: on the 3rd consecutive appearance it files
   a durable `handoffs.jsonl` row to **"Kurt triage"** via `coord.py send` (SSOT:
   `_config/COORDINATION_RECORDS_RULES.md`), surfaced by the SessionStart inbox hook. NEGATIVES:
   dedupe is against handoffs.jsonl state (open OR acked blocks a re-file; only `resolve` frees
   it — and a persisting finding re-files after a resolve, because a resolve that didn't clear the
   finding is not a fix); a finding absent for one run resets its streak (`finalize()` runs on
   green too); the dispatcher is ADDITIVE and ISOLATED — findings, Slack, and exit codes are
   unchanged, and a broken dispatcher prints loudly but never fails the checker (rule-2 family).
   The crash path (exit 2) skips dispatch entirely — a partial run must not reset streaks it
   never got to check.

## Wired beats (update when adding/removing)

| name | writer | max age |
|------|--------|---------|
| `offsite-backup` | `scripts/backup_offsite.py` end of successful `run()` | 8 days |
| `forecast-a-monitor` | `_outputs/scripts/forecast_a_monitor.py` | 8 days |
| `loop-scorecard` | `ShipRouting/scripts/loop_scorecard.py` | 8 days |
| `corrections-mining` | `_outputs/scripts/corrections_digest.py` | 8 days |
| `automation-health` | `scripts/automation_health.py` self-beat | 2 days |
| `freshness-sweep` | `_outputs/scripts/freshness_sweep.py` (weekly data-freshness monitor — Mon 12:33 Claude scheduled task; beats on run, flags or not) | 8 days |

Checker also probes (no beat needed): `sync_heartbeat.json` age (>48h), `schtasks` AppyHour* Last
Result ≠ 0, shipping.db `PRAGMA quick_check` (read-only immutable), **dev↔prod tree parity on
DB-relevant `*.py` (rule 9)**, **cloud-replica freshness — `shopify_orders`/`weather_history` data
age + `C:\AppyHourData\replica_pull_stamp.json` ingest stamp (rule 11)**.
