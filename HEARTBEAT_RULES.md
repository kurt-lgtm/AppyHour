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

13. **The weekly freshness sweep is a SECOND reader of `EXPECTED`, never a second copy of it.**
   `Claude Projects/_outputs/scripts/freshness_sweep.py` (beat-or-fail check, 2026-08-29) imports
   `automation_health.EXPECTED` and FLAGs any beat older than its declared limit — the WEAK form of
   exit-0-without-beat detection (the beats' owning Claude-internal scheduled tasks leave no
   queryable last-run record, so "ran without beating" and "never ran" are indistinguishable; both
   are red). Rule 4 still holds: expectations change in `automation_health.py` ONLY — the sweep
   imports, it never re-declares. The two checkers fail independently and watch each other
   (`automation-health` and `freshness-sweep` are both rows in the table).

14. **Two scheduled writers of `shipping.db` must not overlap — and a write collision must never
   take down the stages that had nothing to do with it.** 🔴 2026-08-31: `daily_shipping_sync`
   (`appyhour_daily_tue/wed/thu/fri`, 12:00 since 2026-05-14) died **three runs running** with
   `sqlite3.OperationalError: database is locked` from `store_delivery_status`. Two things had to
   land together: `appyhour_sync_daily_noon` was created 2026-08-25 on a **12:05** trigger — newly
   overlapping the 12:00 daily — and commit `811914b` added the replica-pull stage, expanding the PP
   work list 124 → 2,436 and pushing the first 200-order checkpoint from ~12:01:35 out to ~12:05,
   straight into that window. NEGATIVES:
   - **A new schtask that writes `shipping.db` gets its start time checked against every existing
     writer task's RUN DURATION, not against their start times.** 12:05 "looks clear" of a 12:00
     task and is not; the 12:00 daily now runs ~180 min.
   - **Never open `shipping.db` for writing with raw `sqlite3.connect` in a scheduled task.**
     `busy_timeout` alone only makes you *wait* before losing; the advisory single-writer lock in
     `appyhour_lib/db.py` is what serializes writer *processes* (`appyhour_lib/CLAUDE.md`).
   - **Never hold `db.connect()` across a long stage either.** A 3-hour lock hold starves every
     other writer and gets BROKEN anyway at `AH_WRITE_LOCK_MAX_AGE` (1800s) — a lock nobody can
     respect is worse than none. Take it **per checkpoint** and release. That long-hold hazard, not
     an exemption, is why `daily_shipping_sync`/`sync_logon` sat in
     `scripts/db_write_gate.BYPASSING_WRITERS`; that list stays populated after a migration
     (see its header) because a per-checkpoint holder is unlocked most of its run.
   - **A lock loss must be DEFERRAL, never loss and never death.** Rows already paid for with an API
     call are HELD and retried at the next checkpoint (the `PPThrottled` shape); the named
     `PP DB-LOCK` line is logged; the run continues. Every stage is now wrapped so a stage's
     exception fails **that stage only** — the 08-25/27/28 collisions also killed the Gorgias and
     reclassify stages, which never touched the contended write. That blast radius was the real
     damage.
   - **Never let "we could not write" report as a dead feed.** `written == 0` from lock starvation
     and `written == 0` from a dead ParcelPanel feed demand opposite actions; they are counted and
     reported separately (`PP DB-LOCK STARVED` vs `PP FAIL`), same reason rule-8-style throttling is
     diagnosed before the dead-feed guard.
   - **A scheduled task whose action is a bare `python.exe <script>` discards stderr, so a crash
     leaves no evidence.** The absence of it is the only reason this took a full reconstruction.
     `daily_shipping_sync.main()` now writes any escaping traceback into its own
     `%APPDATA%/AppyHour/sync_logs/daily_*.log` (in-process: survives a deploy, needs no elevation).
     Wrapping the task action in `cmd.exe /c ... >> log 2>&1` is still worth doing and requires an
     elevated `schtasks /Change` — Kurt's terminal, not an agent's.
   - **🔴 A timeout that only "moves on" is not a timeout — it is a second writer nobody is
     tracking. RESOLVED 2026-08-31 by cooperative cancellation.** `sync_logon._run_stage` stamped
     `fail:Timeout` at 600s and continued, but Python cannot kill a thread: the abandoned stage
     kept writing (measured ~11,900 upserts through ~12:22) while holding the advisory lock its
     owner would never release. The per-checkpoint lock made that collision *survivable*; it did
     not remove it. What now holds, all of it negatives-first:
     - **A stage that can be abandoned must be cancellable, and a stage that cannot be cancelled
       cleanly must NOT get a flag.** One primitive — `appyhour_lib/cancel.py` (`CancelToken`,
       `StageCancelled`, `checkpoint()`) — passed down from `_run_stage`, never per-loop ad-hoc
       booleans. `run_post_ingest_backup` is deliberately NOT cancellable mid-flight (one
       `sqlite3.backup` call; the only interior "boundary" is a torn snapshot file) — it takes no
       write lock, so its only cancel point is refusing to START.
     - **NEVER check a cancel token inside a transaction, and never while a write connection is
       open.** Cancelling mid-transaction abandons a partial write; cancelling with the connection
       open swaps a silent orphan for a loud one — the lock is still held. Every checkpoint sits
       AFTER `commit()` and AFTER `close()`. In `backfill_sync` the boundaries are the month-chunk
       loop, the `while url:` pagination loop (nothing written yet), and a per-`PP_FLUSH_EVERY`
       (200) batch flush; in `auto_import` it is BETWEEN FILES, never inside `ingest_file`'s
       per-invoice loop.
     - **Never replace a silent abandonment with a quieter one.** `_run_stage` now signals →
       joins with a bounded `STAGE_GRACE_S` (120s) → and if the thread is STILL alive raises
       `ZombieStageError`, a named CRITICAL alarm that ABORTS the run (exit 3). Process exit is
       the only thing that actually stops a daemon thread, and continuing would run the remaining
       stages beside an untracked writer. 🔴 Do not "fix" a zombie by raising a timeout; the grace
       window is not a second ceiling to tune — needing more of it means the checkpoints are too
       far apart.
     - **"The stage stopped" is not the acceptance test; "the stage left no lock" is.** After a
       clean cancel `_run_stage` runs a lock-release proof (`appyhour_lib.db.write_lock_holder`)
       and alarms CRITICAL if this process still holds `<db>.writelock`. Measured on a scratch DB:
       old shape → second writer REFUSED after 10.16s; new shape → cancelled at chunk 51 on an
       exact 5,100-row boundary, lock free, second writer **OK after 0.03s**.
       Tests: `tests/test_stage_cancel.py` (8, scratch `tmp_path` only — never the live DB).
     - **`run_fulfillments` held ONE `db.connect()` across its whole stage** (the long-hold this
       rule already forbade, still live in the file that broke). It now passes an `open_conn`
       factory down, so the lock is taken per committed batch and released across the HTTP calls.
       NEGATIVE: do not reintroduce a single `conn` "for efficiency" — the time is in the HTTP.
     - **A cancel is NOT a failure.** Stages stamp `cancelled:Timeout` and re-raise; `_run_stage`
       stamps `fail:Timeout:<n>s:cancelled-clean`. Never fold `StageCancelled` into
       `sync_all_carriers`' `failures` list — that would fire rule 8's partial-run alarm for legs
       that were never attempted.
     - **🔴 STILL OPEN (smaller, named):** `auto_import` is cancellable but frees no lock, because
       `shipping_invoice_db.init_db` opens shipping.db with a RAW `sqlite3.connect` and holds it
       for the whole scan — one of the 25 lock-bypassing writers measured in
       `scripts/db_write_gate.py`. Migrating `init_db` to `appyhour_lib.db.connect` is a separate
       change (Kori, both MCP servers and ~30 callers share it) and must not be done as a side
       effect of a cancellation fix.

15. **The three `shipping.db` corruptions were caused by TWO NAMES for one file, not by concurrent
   writers. Concurrency was never the bug — and every fix aimed at concurrency missed.** 🔴 Measured
   2026-08-31 on scratch DBs (`walrace.py`, 4 writer processes, raw `sqlite3.connect`, per-transaction
   reconnect, WAL + `synchronous=NORMAL`, ~20s, INSERT/DELETE + `wal_checkpoint(TRUNCATE)` every txn):

   | writers reach the file by | `busy_timeout` | runs corrupted |
   |---|---|---|
   | ONE path | 0 (none at all) | **0 / 5** |
   | ONE path | 10000 | **0 / 5** |
   | TWO paths (NTFS hardlink, same bytes) | 10000 | **5 / 5** |

   The two-name runs fail with `database disk image is malformed` — the verbatim string from
   `notify_fallback.log` on 2026-07-01 and from the 6/27 handoff. SQLite keeps its WAL locks in the
   `-shm` file, and sqlite creates `-wal`/`-shm` beside **whichever NAME was opened**: two names ⇒ two
   `-shm` ⇒ the writers never see each other's locks at all, and each checkpoints its own WAL into the
   one shared main image. NEGATIVES:
   - **Do not "prove" concurrency corrupts a WAL DB by reasoning about it — it does not.** Four
     processes with NO `busy_timeout`, all calling `wal_checkpoint(TRUNCATE)`, ran ~2,900 transactions
     clean. That is SQLite working as designed. `busy_timeout` buys clean *waits*, not integrity, and
     its absence was never the corruption mechanism (2026-06-27 fix, `bff150f`).
   - **The advisory lock is per-NAME, not per-IMAGE — it cannot bind a second name.** `db.connect()`
     locks `str(target) + ".writelock"` (`appyhour_lib/db.py`). Measured: two `connect()` calls on two
     names for one file BOTH acquired a lock; the same-name control was correctly refused. That is why
     Phase 1 (`7d5e1a5`) did not stop 2026-07-03 — the `writelock.stale-2026-07-03-1030` corpse shows
     `sync_logon.py` holding it since 04:02 while the image went malformed at 10:19.
   - **All three corruptions (6/27, 7/01, 7/03) happened while the DB lived in MSIX-virtualized
     `%APPDATA%\AppyHour`; there has been none since it moved to `C:\AppyHourData` on 7/08** — 8 weeks,
     with the same ~30 writers and 24 of them still on raw `sqlite3.connect`. The move, believed at the
     time to be about a *missing-file* false alarm, is what actually fixed the corruption. Packaged
     (Claude/MCP) processes got a copy-on-write shadow of that path while scheduled tasks and Kori got
     the real Roaming file: one image, two names, exactly the row above.
   - **A second name is a LATENT corruption machine, so a writer that resolves a non-canonical path is
     a 🔴 bug even when it "works".** Still live at time of writing: `GelPackCalculator/backfill_sync.py`
     (`init_db(".")` — relative CWD), `validate_fix1_rescore.py:21` and `validate_thermal_fixes.py:17`
     (hardcoded `%APPDATA%\AppyHour\shipping.db`), `backfill_box_type.py` + `import_other_data.py`
     (`_get_app_dir()`), `ShippingReports/reports/box_size_report.py:340` (`%APPDATA%` with no canonical
     branch), and `shipping_invoice_db._db_path()`, whose no-arg fallback is
     `GelPackCalculator/shipping.db`. Each is one invocation away from reproducing the table above.
   - **Never verify single-image-ness from inside the MSIX container.** `fsutil file queryfileid` on the
     Roaming and LocalCache paths returns the SAME id from a packaged process — the VFS makes both names
     hit one file for the caller that asked. That is the identical illusion as the retracted 7/01
     `samefile=True` finding (REBUILD §5.1). The directory *listings* differ, which is the tell.
   - **`database is locked` (rule 14) and `database disk image is malformed` (this rule) are different
     failures with opposite fixes.** Locking work — the advisory lock, per-checkpoint acquisition,
     cooperative cancellation — buys availability and is worth doing; it does not and cannot address
     corruption. Do not let a green lock story stand in for path canonicalization.
   - **🔴 Canonicalizing onto `db_path()` would NOT have prevented 7/03, and saying otherwise is the
     trap.** On 7/03 the canonical path WAS `%APPDATA%\AppyHour\shipping.db` — the virtualized one. MSIX
     splits packaged from unpackaged writers at the SAME name, so pointing every writer at one path
     string still yields two images. Only moving OFF the VFS removes it, which is what 7/08 did. The
     guard below prevents a REGRESSION back onto a virtualized or relative name; it was never the
     missing 7/03 fix. Do not re-derive "take the lock properly" from the four docs that record only
     two incidents (`appyhour_lib/CLAUDE.md:33`, `REBUILD-WITH-AI.md:272`,
     `ShippingReports/RESHIP_REPORT_RULES.md:218`) — that remedy was already deployed when 7/03 hit.
   - **The guard already exists in exactly one writer; promote it, do not reinvent it.**
     `sync_logon._resolve_db_guarded()` (`GelPackCalculator/sync_logon.py:165-188`) resolves at CALL
     time and raises + notifies CRITICAL when the path is not under `DATA_ROOT`. That belongs in
     `appyhour_lib.db.connect()` and `shipping_invoice_db.init_db()` so all ~30 writers inherit it.
     Note it also fixes the module-import resolution class: 20 writers still do `DB = db_path()` at
     import, which is what produced the 9-day 7/22 split-brain.
   - **RCA fix #2's stagger is HALF shipped and is not load-bearing anyway.**
     `appyhour_sync_on_logon` carries `delay=PT2M`, not the ~5 min proposed. Under the table above a
     stagger narrows the overlap WINDOW without touching the mechanism, and the MCP servers are
     long-running (hours, "usually 1-3 live"), so no logon delay avoids them. Treat it as noise
     reduction, never as the corruption fix — Kurt's "I don't want any collisions" is satisfied by
     path canonicalization, not by scheduling.

## Wired beats (update when adding/removing)

| name | writer | max age |
|------|--------|---------|
| `offsite-backup` | `scripts/backup_offsite.py` end of successful `run()` | 8 days |
| `forecast-a-monitor` | `_outputs/scripts/forecast_a_monitor.py` | 8 days |
| `loop-scorecard` | `ShipRouting/scripts/loop_scorecard.py` | 8 days |
| `corrections-mining` | `_outputs/scripts/corrections_digest.py` | 8 days |
| `automation-health` | `scripts/automation_health.py` self-beat | 2 days |
| `freshness-sweep` | `_outputs/scripts/freshness_sweep.py` (weekly data-freshness monitor — Mon 12:33 Claude scheduled task; beats on run, flags or not) | 8 days |
| `pytest-shiprouting` | `_outputs/scripts/pytest_cadence.py` (weekday ShipRouting fast-tier suite via `~/.claude/hooks/catch-up-missed-tasks.sh` — stamp-guarded; Slack only on red, beat every run) | 4 days |

Checker also probes (no beat needed): `sync_heartbeat.json` age (>48h), `schtasks` AppyHour* Last
Result ≠ 0, shipping.db `PRAGMA quick_check` (read-only immutable), **dev↔prod tree parity on
DB-relevant `*.py` (rule 9)**, **cloud-replica freshness — `shopify_orders`/`weather_history` data
age + `C:\AppyHourData\replica_pull_stamp.json` ingest stamp (rule 11)**.
