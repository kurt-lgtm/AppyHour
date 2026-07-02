# Automation Heartbeats / Dead-Man-Switch — Constraints (single source of truth)

> 🔴 **PRE-CHANGE GATE:** read this before touching `appyhour_lib/heartbeat.py`,
> `scripts/automation_health.py`, or any task wrapper's `beat()` call. Change rules HERE first, same commit.

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
8. **Don't wire `beat()` into files another agent has mid-flight** — coordinate first (2026-07-02:
   daily_shipping_sync deferred while the writelock migration owns those files; checker covers it via
   `sync_heartbeat.json` age instead).

## Wired beats (update when adding/removing)

| name | writer | max age |
|------|--------|---------|
| `offsite-backup` | `scripts/backup_offsite.py` end of successful `run()` | 8 days |
| `forecast-a-monitor` | `_outputs/scripts/forecast_a_monitor.py` | 8 days |
| `loop-scorecard` | `ShipRouting/scripts/loop_scorecard.py` | 8 days |
| `automation-health` | `scripts/automation_health.py` self-beat | 2 days |

Checker also probes (no beat needed): `sync_heartbeat.json` age (>48h), `schtasks` AppyHour* Last
Result ≠ 0, shipping.db `PRAGMA quick_check` (read-only immutable).
