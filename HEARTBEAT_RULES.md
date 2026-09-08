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
3. **The ledger is `C:\AppyHourData\heartbeats.json` — NOT `%APPDATA%`, NOT shipping.db.** Nothing
   in this system touches shipping.db read-write, ever (MSIX+WAL corruption). The checker's DB
   health probe opens `mode=ro&immutable=1` only.
   🔴 **Why it moved off `%APPDATA%` (2026-08-31):** MSIX virtualizes that directory, so
   agent/routine writes landed in the sandbox overlay while real-context (schtask) writes landed in
   the real profile — **two physical ledgers with disjoint histories**. Measured that morning: the
   overlay held 8 keys with `offsite-backup` frozen at 08-22 (→ a "9.2d stale" finding), while the
   real profile held that ONE key correct at 08-30. The backup had run fine; the ledger was split.
   Both `automation_health.py` and (since `e528823`) `freshness_sweep.py` read this ledger, so
   rule 13's mutual check bought nothing here — **they went blind together, at the same instant,
   for the same reason.** `C:\AppyHourData` is outside the virtualization scope, the same reason
   the canonical shipping.db and `replica_pull_stamp.json` live there; verified by writing through
   `\\localhost\C$\AppyHourData\...` and reading the byte-identical file back through `C:\`.
   NEGATIVES: (a) **never seed a moved ledger by COPYING one side** — the two hold disjoint
   histories, so copying the side with more keys carries its stale value forward and keeps
   false-alarming; the merge is **newest-wins per key**. (b) Any reader that hand-rolls
   the deprecated `Path(os.environ["APPDATA"]) / "AppyHour" / "heartbeats.json"` instead of calling
   `read_ledger()` is a second, silently-diverging path — `read_ledger()` is the only sanctioned
   access.
   (c) `read_ledger()` merges the deprecated `%APPDATA%` file newest-wins for ONE deprecation
   window and logs LOUDLY on stderr whenever it contributes a key; a silent fallback would be the
   split ledger wearing a fix's name. Remove the fallback once no unmigrated writer/reader remains.
3b. **`sync_heartbeat.json` moved for the SAME reason, one file later (2026-09-01) — canonical
   `C:\AppyHourData\sync_heartbeat.json`, accessed ONLY via `appyhour_lib.sync_heartbeat`.**
   This is the ingest-leg heartbeat (`carriers`, `fulfillments`, `auto_import`, `shopify_orders`,
   `post_ingest_backup`), and it was the LAST file left on the virtualized path. `sync_logon.py`
   stamps it from the `appyhour_sync_on_logon` schtask (real context); `automation_health` reads it
   packaged (agent context). Measured 2026-09-01: the overlay was frozen at **08-25** and
   `check_sync_heartbeat` reported **"ingest sync heartbeat stale: 6.8d"** while the real-profile
   file had been written **13:22 that same day** with every leg current. 🔴 The false alarm landed
   on the one signal whose entire job is to say the ingest died — the monitor was not lagging, it
   was reading a different file. Writers: `GelPackCalculator/sync_logon.py`,
   `GelPackCalculator/pipeline_run.py`. Reader: `automation_health.check_sync_heartbeat`.
   NEGATIVES, all measured that day:
   (a) **Never seed by copying a side.** Same trap as the ledger, but sharper here: the overlay
   carried `fulfillments_status: "ok"` from 08-25 while the real profile carried
   `fail:Timeout:600s:cancelled-clean` from that morning. Copying the overlay would have buried a
   live failure under a stale success — a monitoring path repaired into lying. Merge newest-wins
   per key (`sync_heartbeat.merge`).
   (b) **A `_status` key has NO timestamp of its own — do not merge it independently.** Its
   recency is `max(<name>, <name>_last_attempt)` (`sync_heartbeat.stamp_time`), because `_stamp`
   advances the bare key only on success. Comparing statuses by the success timestamp alone loses
   exactly the failure in (a).
   (c) **`_last_attempt` is NOT a freshness signal** and must stay excluded from the staleness
   gate. A leg failing every run stamps a fresh attempt every run; counting it would hold the gate
   green forever — the silent-degrade class this checker exists to catch.
   (d) **`retired:` is a PASS, not a failure.** `shopify_orders_status: "retired:cloud-owned"` is
   terminal and will never change back; grading it red posts an unfixable finding every run, which
   is the alarm-deafness rule 4 bans. It was invisible while the checker read the frozen overlay.
   (e) Timestamps here are **naive local**, unlike `heartbeats.json` (aware UTC). Do not
   "harmonise" them: both consumers compare against a naive `datetime.now()`, so a mixed
   comparison raises and an offset shift silently moves the 48h gate.
   (f) `%APPDATA%\AppyHour\sync_logs\` deliberately did NOT move — it is write-only, with no
   cross-context reader to diverge from. A split matters when two contexts READ one name.
4. **Expectations live in the checker, not the ledger.** A task that stops being scheduled must be
   removed from `EXPECTED` in the same change — a stale expectation = permanent false alarm, which
   trains alarm-deafness (the failure mode that killed the old monitoring).
   🔴 **A max-age must clear the owning schedule's longest legal gap, or it IS the stale
   expectation this rule bans (2026-08-31).** `automation-health` sat at 2d while its routine runs
   `15 12 * * 1-5` — weekdays only, so the Fri→Mon gap is 72h and every Monday graded a healthy
   Friday run stale. Structural false alarms are worse than none: this one reached the rule-12
   dispatcher and was on course to hand Kurt noise. Check the cron before setting a limit; prefer a
   flat hours limit over weekday-aware logic (a limit anyone can verify with one subtraction beats
   one that needs a holiday calendar the checker does not have).
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
9b. **Prod drift is graded by whether prod EXECUTES the file — a stale file nobody runs is a
   COUNT, never a finding** (2026-09-06). 🔴 The burn is the alarm itself: rule 9 emitted one
   blanket `prod tree STALE on N DB-relevant file(s)` line that fired *every single day* (21,
   then 15, then 36) and named files nobody could act on. The ownership sweep of 2026-09-03
   proved why: the MCP server runs from the DEV tree (`.mcp.json`) and every Claude scheduled
   routine runs dev paths too, so prod's only consumers are the **schtask actions under
   `C:\AppyHourProd`**. A recurring alarm nobody can act on is worse than no alarm — it is the
   rule-4 failure (an expectation nobody can satisfy) applied to the daily health post, and it
   trains everyone to skim past the day a REAL undeployed fix appears. So `check_prod_parity`
   splits its output:
   - 🔴 **CRITICAL, `prod-drift-executed-<file>`** — the stale file is reachable from a prod
     entry point (the entry script itself, or a module it imports, depth ≤ 2 =
     `PARITY_REACH_MAX_DEPTH`). The finding **names the entry point**, because that is the only
     form anyone can act on: "`helper.py` is stale in prod AND `sync_logon` imports it."
     Keyed **per file** — one fix does not clear another file's drift.
   - 🔵 **INFO, body-only** — everything else: `N other DB-relevant file(s) differ; prod does not
     execute them`. No finding, no `finding_key`, no dispatch, no page.
   NEGATIVES: (a) the count is **not** deleted — dropping it hides the day an unreachable file
   becomes reachable (someone adds an import), and it is the context that makes the critical list
   read as "3 of 36". (b) Entry points are **enumerated, never hardcoded** — the same
   `_prod_entry_targets()` rule 19 uses (schtask actions under the prod tree, `.bat` wrappers
   parsed); a hand-kept roster silently goes stale the first time Kurt adds a task. (c) The reach
   walk is **static AST and never executes a target** (every one is a live ingest/backup/Gorgias
   action) and resolves imports **by directory** (own dir + prod root), never by bare basename —
   eight `utils.py` exist in the tree and a name match would invent reachability. (d) If the walk
   or the schtask enumeration **fails, that is a CRITICAL** (`prod-drift-reach-unknown`) listing
   every drifted file. "Reachability unknown" must never silently mean "nothing is executed" —
   that turns a broken check into a green board. (e) Rule 9's deploy discipline is unchanged:
   `--apply` is still Kurt's call, and a CRITICAL here names the file, it does not deploy it.
9c. **`automation_health.py --no-notify` is the ONLY sanctioned way to run this checker
   read-only** (2026-09-06). The checks are all read-only (`shipping.db` opens
   `mode=ro&immutable=1`, the AST walks never execute a target), but `main()` itself has three
   side effects on a red run: the `#kurt-ops` post, the heartbeat ledger write, and the dispatch
   streak advance that files a handoff at 3. `--no-notify` suppresses exactly those three;
   findings, printed report and exit codes are byte-identical (still non-zero on findings).
   🔴 NEGATIVE: do NOT hand-write another scratchpad harness that calls the `check_*` functions
   one at a time. Two were written (`ah_readonly_run.py`, `prod_reach.py`) and each is a
   hardcoded list of checks that silently goes stale the moment a check is added — a harness
   missing a check reports a green the real run would not.
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
       stamps `partial:Timeout:<n>s:…` when rows were committed and `fail:Timeout:<n>s:cancelled-clean`
       when none were (rule 18). Never fold `StageCancelled` into
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
     a 🔴 bug even when it "works".** ✅ **CLOSED 2026-08-31** — the guard below is now enforced and all
     seven stragglers are resolved: `backfill_sync.py` (`init_db(".")` → `init_db()`),
     `validate_fix1_rescore.py` + `validate_thermal_fixes.py` (hardcoded `%APPDATA%` → `db_path()`, and
     opened `mode=ro`; both had been printing "shipping.db not found" since the 7/08 move, i.e. they
     validated nothing for 8 weeks), `backfill_box_type.py` (`_get_app_dir()` → `init_db()`),
     `ShippingReports/reports/box_size_report.py` (`%APPDATA%` with no canonical branch → `db_path()`,
     `mode=ro`), `shipping_invoice_db._db_path()` (no-arg fallback `GelPackCalculator/shipping.db` →
     `paths.db_path()`), and `import_other_data.py` → **archived** to
     `GelPackCalculator/archive/` (one-time-and-applied: its `other data/` input folder no longer
     exists, and its target state is in the DB — 6 `invoices` rows with `source='other_data'` and
     ~12,742 `shipments` carrying those workbook names). Also canonicalized: Kori's `_db_dir()`
     `%APPDATA%` fallback, the component most likely to still hit it after the DO ingest migration.
     🔴 Retirement selection was EVIDENCE-based, not name-based: a reference count is not evidence for
     a manually-run CLI (it is never imported, so zero refs is expected). The axis is
     one-time-and-applied vs repeatable-diagnostic — the two `validate_*` scripts LOOK like spent
     one-shots and are not; they re-check the newest Kori snapshot against the shipped fix invariants
     and pass today against real data.
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
   - **The guard already exists in exactly one writer; promote it, do not reinvent it.** ✅ **DONE
     2026-08-31.** `sync_logon._resolve_db_guarded()` was lifted verbatim into
     **`appyhour_lib.paths.assert_canonical_db()`** and is now called from
     **`appyhour_lib.db.connect()`** and **`shipping_invoice_db.init_db()`**, so every writer inherits
     it. `sync_logon._resolve_db_guarded` remains as the CALL SITE (that is what makes resolution
     happen at call time) and delegates — do not re-inline the check there.
     - **It is a HARD REFUSE, not a warning** (Kurt's call). `NonCanonicalDBPath` subclasses
       `RuntimeError` so the pre-existing `except RuntimeError` callers still work. It breaks any
       one-shot run from the wrong directory — that is the point — and the message names the offending
       path, the canonical path, and how to fix the invocation, so the break is self-servicing.
     - **🔴 A refusal RAISES; it does not Slack.** Same day, the promoted guard kept
       `sync_logon`'s `notify(level="critical")` as a library default and **one test run posted 7
       CRITICALs to #kurt-ops in 90 seconds**. The page was correct *in sync_logon* — that task runs
       off a logon trigger with stdout teed to a file, so a refusal nobody sees is a silent stall —
       and wrong as a default: every pytest run, CI pass and developer typo pages Kurt, and an alarm
       that fires on typos gets muted, which is worse than no alarm. `assert_canonical_db(...,
       notify=...)` defaults to `None` = page only when **`AH_UNATTENDED=1`**; `sync_logon` passes
       `notify=True` explicitly. Pages are deduped one-per-offending-path-per-process so a retry
       loop cannot storm. NEGATIVE: do NOT infer unattended from `sys.stdin.isatty()` — sync_logon
       tees stdout to a log, so an isatty probe gets it backwards in both directions.
     - **🔴 A temp-dir scratch DB must work with NO env var**, or the next person disables the guard
       to get their tests green. `%TEMP%` is an unconditional allow, so pytest's `tmp_path` needs
       nothing. The three fixtures that broke (`GelPackCalculator/tests/_tmp_db_{heal,acct,dims}`)
       were writing a **repo-local** `shipping.db` — the exact second name this rule bans — and were
       moved to `tmp_path`, not handed an `APPYHOUR_DB_PATH`. Wiring tests through an env var would
       make a passing suite depend on ambient state; that is not the fix.
     - **Escape hatches are deliberately narrow, and the legacy Roaming path has NONE.** Allowed:
       a file whose name is not `shipping.db`; a path under `%TEMP%` (tests, scratch copies); an
       `APPYHOUR_DB_PATH`/`AH_DB_OVERRIDE` naming exactly that file, which prints a warning so it is
       never silent; a machine with no `C:\AppyHourData` at all (pre-migration). `%APPDATA%\AppyHour\
       shipping.db` is refused unconditionally — it is the specific virtualized second name behind the
       7/22 split-brain.
     - **`connect_ro` is deliberately NOT guarded.** A `mode=ro` connection cannot take a write lock or
       trigger a checkpoint, so it cannot join the race, and read-only work on a scratch copy is the
       sanctioned way to investigate this DB at all. Guarding it would block the safe path.
     - **Module-import resolution: partially closed.** Removed: `sync_logon.py:39`'s `DB = db_path()`
       (the line its own docstring named as the 7/22 root cause; `DB` was used only by a status
       `print`), plus `sync_shopify_orders`, `import_feedback_csv`, `pp_backfill_aged_out` and
       `kori/db_snapshots` (now `_default_db_path()`). 🔴 **Deliberately left at import, with reasons:**
       `pipeline_run.py:32` resolves once ON PURPOSE — it exports the value as `APPYHOUR_DB_PATH` to
       its subprocesses, so one resolution per pipeline is the invariant, not the bug;
       `gmail_fedex_sync.py:34` and `import_missing_fedex.py:35` define `DB_PATH` and never read it
       (writes are centralized in `auto_import`) — dead constants, no writer behind them;
       `fl_audit_v2.py` / `fl_force2day_audit.py` are read-only audits. In every remaining case the
       guard converts a stale import-time value from a silent legacy write into a loud refusal, which
       is the property that mattered.
   - **🔴 `shipping_invoice_db.init_db` still opens a RAW `sqlite3.connect` and takes no advisory
     write lock — that migration was SCOPED AND DECLINED on 2026-08-31, not forgotten.** Measured
     scope (not the ~30 assumed): **one** `sqlite3.connect` in the whole module (`init_db`); **~24 call
     sites across 8 files** — `kori/gel_pack_webview.py` (16), `backfill_box_type` (2), `auto_import`,
     `backfill_pp`, `backfill_sync`, `import_missing_fedex`, `sync_carrier_invoices`, plus the archived
     `import_other_data`. **Neither MCP server imports it** (grepped: `AppyHourMCP`, `AppyHourShippingMCP`
     have zero references) — that premise was wrong, and the MCP raw-connect exposure is
     `AppyHourMCP/tools/cache.py`, a different file. Why declined: most of those call sites are READS
     (`load_all_shipments`, `query_*`, `stats_by_box_type`, and `box_size_report`) that reach the DB
     through `init_db` because it is the only entry point. Routing them through `db.connect()` hands
     every read path an exclusive write lock and a 90 s `DBWriterBusy` — Kori's UI would start failing
     whenever a long writer is mid-run. That trades integrity it does not need for availability it
     does. **The prerequisite is to split read from write first** (add an `open_ro()` and move the
     ~15 query paths onto it); only then migrate the remaining writers. Do not do the one-line
     `init_db` swap on its own. It buys availability, never integrity — see the `database is locked`
     vs `malformed` bullet above.
   - **RCA fix #2's stagger is HALF shipped and is not load-bearing anyway.**
     `appyhour_sync_on_logon` carries `delay=PT2M`, not the ~5 min proposed. Under the table above a
     stagger narrows the overlap WINDOW without touching the mechanism, and the MCP servers are
     long-running (hours, "usually 1-3 live"), so no logon delay avoids them. Treat it as noise
     reduction, never as the corruption fix — Kurt's "I don't want any collisions" is satisfied by
     path canonicalization, not by scheduling.

16. **A routine may go EXCEPTION-ONLY only AFTER its beat is wired and verified.** 🔴 2026-08-31:
   **silence must be EARNED.** Making a routine silent-when-clean while nothing watches it makes it
   silent AND unwatched — strictly worse than the weekly all-clear it replaces, because the
   all-clear was at least a human-readable liveness signal. Kurt's standing preference is
   exception-only; rule 1 says silence is the failure signal; the two only compose when something
   else is watching. Order, non-negotiable: (1) land `beat()` in the code path that does the work,
   (2) register the name in `EXPECTED` with a max age clearing the schedule's longest legal gap
   (rule 4 — **weekly routines get ~10 days, never 7**: a catch-up run after a slept-through slot
   legally lands >7d after the last one), (3) verify it lands in the canonical ledger via
   `read_ledger()`, (4) only then flip the Slack step. Flipping first and wiring later is the
   failure mode this rule names; there is no window in which it is acceptable. NEGATIVES:
   - **A beat placed where the work did not happen is worse than no beat.** `shipping-cost-sheet`
     beats INSIDE the `--push` branch after `push()` returns a URL, not at the end of `main()` — a
     compute-only run is not the routine (rule 8's shape).
   - **Two routines sharing one Python entry point need TWO keys.** `weekly-reship-report` and
     `weekly-shipping-vendor-matrix` both run `ingest/slack_reship/sync.main()`; one key would let
     either routine's death hide behind the other's success. `--push` is what tells them apart.
   - **A routine whose beat could NOT be landed stays LOUD.** As of 2026-08-31 that is
     `friday-forecast-refresh` and the three `prewarm-carrier-tnt-*` routines: every one of their
     beat targets is a `ShipRouting/scripts/*.py` file, off-limits to the session that wired the
     rest. They keep posting on success until someone with that repo lands `beat()` in
     `friday_forecast_refresh.py`, `build_prewarm_universe.py` and `prewarm_carrier_tnt.py`. Do NOT
     flip them to exception-only before then. (Update 2026-09-02: `friday-forecast-refresh` was
     DELETED by Kurt — the Friday ice re-size is manual from the DO app — so it no longer needs a
     beat. 2026-09-03: the live-write business routines `truffle-watch-christine-farley`,
     `wrong-address-handler-daily`, `sku-lifecycle-scan-weekly` and `carrier-sla-monitor-weekly`
     are now wired — see the table below; `ops-issues-weekly-update` and
     `evo-transfer-monday-reminder` remain LOUD for the reasons recorded there.)

17. **An ingest leg whose cost tracks the SIZE OF THE DATASET rather than the SIZE OF THE CHANGE
   will eventually outgrow any ceiling — fix the window, never the ceiling.** 🔴 2026-08-01 →
   2026-09-02: `sync_logon`'s `fulfillments` stage hit its 600s watchdog on **every** run for a
   month and had become a daily page (`fail:Timeout:600s:cancelled-clean` — which is the rule-14
   cancellation working exactly as designed; the alarm was correct, the workload was not).
   `backfill_sync.sync_fulfillments` re-fetched a **fixed 30-day window every run** — the whole
   active subscriber base — to gain ~2,500 changed rows. Measured in isolation against a scratch
   DB: **696.3s / 12,573 rows on 2026-08-31**, and **142.4s / 12,748 rows on 2026-09-02** — the
   same code and the same window, 4.9× apart, because the cost is Shopify's page latency times a
   page count that only grows. 🔴 **Neither number is "the" cost — on the slow day leg 1 alone blew
   the ceiling, on the fast day it fit and leg 2 blew it.** RESOLVED by an `updated_at` watermark
   (`appyhour_lib/sync_watermark.py`, `WATERMARK_OVERLAP_HOURS`): same-day, same method, leg 1 went
   **142.4s/12,748 rows → 21.3s/1,730 rows** at the 12h throttle interval and **10.4s/825 rows**
   back-to-back, with the cold-start fallback measured at **136.5s/12,734 rows** (i.e. unchanged).
   NEGATIVES:
   - **🔴 Do NOT raise `STAGE_TIMEOUT_S`.** At these volumes no plausible ceiling helps, the
     ceiling is what stops an abandoned stage becoming an untracked writer (rule 14), and a leg
     whose runtime is a function of the subscriber base outgrows the next ceiling too.
   - **🔴 A watermark advanced past unfetched rows loses them SILENTLY AND FOREVER — that failure
     is strictly worse than the timeout it replaces.** A timeout re-fetches the window next run
     and loses nothing; nothing errors when a mark over-advances, no count looks wrong, and the
     gap surfaces months later as an order with no tracking. So the mark advances **only** over a
     chunk that BOTH paged cleanly AND committed, and only after the write connection CLOSED (the
     rule-14 boundary, for a sharper reason: a checkpoint mid-transaction abandons work the next
     run redoes; a watermark mid-transaction tells the next run there is nothing to redo).
   - **🔴 The pagination loop treats a non-200 and an exhausted retry as END OF PAGES** (it sets
     `url = None` and moves on). That was harmless while the whole window was re-fetched every
     run and is the exact hole a watermark would paper over, so `sync_fulfillments` carries a
     `fetch_ok` flag per chunk. And because a watermark is a LOW-water mark, the first incomplete
     chunk stops advancement for every LATER chunk too — jumping a hole is indistinguishable from
     never having had one.
   - **Overlap deliberately, and subtract it on READ.** Shopify `updated_at` is not strictly
     monotonic across a paginated read (an order touched mid-pagination can be re-sorted past the
     pages already walked) and the index lags the write. `WATERMARK_OVERLAP_HOURS = 2` is ~10× the
     longest single-chunk paging interval measured, costs ~7 re-fetched orders (~3.5 orders/hour),
     and `store_fulfillments` upserts on `tracking_number` so a duplicate is a no-op. Baking the
     margin into the STORED value instead would compound it every run and walk the mark backwards.
   - **Cold start is the FIXED WINDOW, never an empty one.** Missing, corrupt, unparseable, or a
     mark from the future (clock skew, a restored file) all degrade to `since` / a clamped
     non-empty window. An empty window looks exactly like a healthy quiet day while the gap it
     leaves is permanent. `sync_watermark.clear()` is the sanctioned repair — there is no
     "rewind N hours" API, because a hole of unknown depth is not repaired by a guess.
   - **The watermark file lives at `C:\AppyHourData\sync_watermarks.json`, for the third time and
     the same reason** (`heartbeats.json` 08-31, `sync_heartbeat.json` 09-01). A `%APPDATA%` fork
     costs a heartbeat a false alarm; it costs a watermark **missing data** — one context banks
     progress the other cannot see, so the next run derives its window from a value that does not
     describe what was fetched. Access ONLY via `appyhour_lib.sync_watermark`.
   - **Timestamps here are aware-UTC (`...Z`), unlike `sync_heartbeat.json`'s naive local.** They
     are a value copied out of Shopify's domain and handed straight back as `updated_at_min`, not
     a clock reading. Harmonising them would shift every window by the UTC offset, and a window
     shifted FORWARD skips rows.
   - **A watermark is NOT a heartbeat and must not be graded as one.** It records how far a feed
     got, not when it last ran; `automation_health` keeps grading `sync_heartbeat.json`. A mark
     that stops advancing because the feed is genuinely quiet is healthy.
   - **🔴 This fixes leg 1 only — THE STAGE STILL TIMES OUT, and reporting otherwise on the
     strength of leg 1 would be the win-claimed-from-the-improvement trap.** Leg 2
     (`sync_parcel_panel`) is bounded by ParcelPanel's ~27.3 orders/min, not by a window; measured
     read-only on 2026-09-02 its work list is **2,607 of 12,287 orders in the 30-day window ≈ 95
     min**, which no 600s ceiling contains. What the watermark buys leg 2 is BUDGET: it now gets
     ~579s of the 600s instead of ~458s, i.e. ~263 orders banked per run instead of ~208 (+26%),
     and its 200-row flush (rule 14) means every one of those is committed. The backlog drains
     monotonically (the `_skip_delivered` predicate never re-selects a delivered order), so the
     pair fits only once it is worked down — expect the timeout stamp (now
     `partial:Timeout:600s:…` when rows were banked, rule 18) to keep appearing until then, and
     do NOT read its disappearance as the only success signal.

18. **A cancel that BANKED progress is `partial:`, not `fail:` — and `partial:` NEVER advances
   last-success. A cancel that banked NOTHING is still `fail:`.** 🔴 2026-09-03: the rule-17
   watermark fixed leg 1, and the stage kept stamping `fail:Timeout:600s:cancelled-clean` with an
   `error` Slack every run anyway — "third day running". Measured, not inferred: leg 2
   (`sync_parcel_panel`) re-polls every fulfillment in the last 30 days that is not yet delivered;
   on Wednesday 09:20 that list was ~1,300 (the Monday cohort still in transit), and at ParcelPanel's
   ~100/min a 600s ceiling covers ~1,000 — so Tue/Wed cancel and the other days fit. Every one of
   those cancels landed on a committed `PP_FLUSH_EVERY=200` boundary (`_flush` → `commit` →
   `checkpoint`), so the progress WAS banked and the remainder IS re-selected next run. The stamp
   said `fail:` because `_run_stage` could not tell a stall from a bounded run that did its share.
   The heartbeat now carries THREE stamp classes, and every reader must know all three:
   - **`ok`** — the stage finished. The ONLY class that advances the bare `<name>` key.
   - **`partial:Timeout:<N>s:<rows> rows committed; remainder re-selected next run`** — cancelled at
     the ceiling AFTER at least one commit. Written to `<name>_status` + `<name>_last_attempt`. Log
     line only; **no Slack**. Silence is EARNED here (rule 16), not assumed: the checker below
     watches for a partial that never becomes an `ok`.
   - **`fail:Timeout:<N>s:cancelled-clean`** — cancelled at the ceiling with ZERO commits. Same
     `error` Slack as before. Zero rows in 600s is a real stall (dead feed, lock starvation, a hung
     socket), not a big backlog, and it must page.
   NEGATIVES:
   - **🔴 `partial:` must NOT advance `<name>`'s last-success timestamp.** The bare key gates the
     12h throttle (`_should_run`), and a stale last-success is exactly what lets the NEXT logon run
     drain the remainder instead of sleeping 12h. Advancing it on `partial:` would turn "banked
     263 rows, 1,000 to go" into "done for 12h" — the 2026-07-27 bug (`_stamp` docstring) wearing
     a new prefix. `_stamp` advances `<name>` on `ok` only; a test proves both directions.
   - **🔴 Progress is counted ONLY after a commit succeeds** (`CancelToken.note_progress`, called in
     `backfill_sync._flush` and after each chunk commit in `sync_fulfillments`, both AFTER the
     writer context exits). Never at a "nothing written" checkpoint, never before `commit()`, never
     for rows merely fetched — a `partial:` that counts un-committed rows claims durability the
     next run will not find, the rule-17 over-advanced-watermark class in a different file.
   - **Zero progress stays `fail:` with the error page, exactly as before.** Do not soften it to
     `partial:0` "for consistency": the whole point of the split is that the two mean opposite
     things and demand opposite actions (wait vs. investigate).
   - **The checker grades `partial:` as info ONLY while it is recent, and escalates to a CRITICAL
     finding when a leg has been `partial:` with no `ok` for > 36h** (`automation_health.
     check_sync_heartbeat`, `SYNC_PARTIAL_ESCALATE_H`). Measured recency = the leg's last SUCCESS
     (`<name>`), not its last attempt — a leg that stamps a fresh `partial:` every run without ever
     finishing is the dead-cadence class, and counting attempts would hold it green forever (rule 3b
     (c), same trap). 36h = three 12h throttle windows: two consecutive partials on Tue+Wed are the
     measured normal; a third with no `ok` means the backlog is not draining.
   - **🔴 AMENDMENT 2026-09-07 — a leg with NO `ok` is graded from the OLDEST OUTSTANDING WORK,
     never from "now". THE ORIGINAL RULE SHIPPED WITH ITS OWN INVERSION IN IT.** The clause above
     said "measured recency = last SUCCESS", but the code (`_grade_partial_legs`) read
     *last success **when present**, otherwise the latest **attempt***. So the one leg the escalation
     exists for — one that has NEVER succeeded and re-stamps a fresh `partial:` every run — reported
     **"last ok 0 hours ago" forever**. An independent Codex audit (2026-09-07) replayed the real
     function with such a leg at **day 0, day 2 and day 7 and got ZERO findings all three times**.
     A permanently-failing ingest leg was structurally incapable of paging anyone. This is the
     loud-failure invariant (rule 1) inverted by the very code added to enforce it, and it is the
     third appearance of the same trap after rule 3b(c) and the `_last_attempt` exclusion in
     `check_sync_heartbeat` — *treating evidence that work was ATTEMPTED as evidence it is HEALTHY*.
     Grading order is now three explicit cases, in `_partial_reference`:
       1. an `ok` exists (`<name>`) → measure from that success (unchanged, and it WINS if both exist);
       2. no `ok` but `<name>_partial_since` exists → measure from the FIRST incomplete attempt;
       3. neither → graded from the attempt stamp, i.e. lenient, and it PRINTS that the stamp is absent.
     `sync_logon._stamp` writes `<name>_partial_since` on the first `partial:` after a success,
     **never refreshes it on a later partial** (refreshing it is the same bug one key over — the age
     would reset to 0 every run), and **clears it on `ok`**. Tests: `tests/
     test_automation_health_dispatch.py::PartialLegEscalationTest` (day 0 info / day 2 CRITICAL /
     day 7 CRITICAL) and `::StampPartialSinceTest` (set-once, never-refreshed, cleared-on-ok).
     NEGATIVES:
     - **🔴 Do NOT fix this with "no `ok` → always CRITICAL".** A leg that has legitimately never
       run yet (a new leg, a rebuilt machine, a first deploy) would page on its very first partial,
       before any backlog could possibly have drained — an expectation nobody can satisfy, which
       rule 4 bans because it teaches everyone to skim the whole health post. **NEVER STARTED** and
       **STARTED AND NEVER FINISHED** are different states; `_partial_since` is what tells them apart.
     - **Case 3 is a real blind window, and it is named rather than papered over.** An unmigrated
       writer (dev committed, `C:\AppyHourProd` not yet deployed) stamps `partial:` with no
       `_partial_since`, so it grades green. Inventing a start time for a backlog we cannot date
       would be a fabricated number inside a monitoring path — strictly worse. It closes on the
       next deploy of `sync_logon.py` plus one `ok`/`partial:` cycle.
     - **`_partial_since` is NOT a freshness signal.** It is excluded from `check_sync_heartbeat`'s
       `newest` max() alongside `_status` and `_last_attempt`; counting it would let a leg stuck
       partial hold the 48h ingest gate green off its own backlog stamp.
     - **A plain `fail:` must not mint a `_partial_since` window.** `fail:` already pages on its own;
       a window opened by a fail would date a later partial's backlog to work that banked nothing.
   - **`sync_heartbeat.merge` needs no special case** — `partial:` writes `_last_attempt`, so
     `stamp_time` already carries it newest-wins over an older `ok` (tested). `_partial_since` is a
     plain timestamp key and merges on its own value; the newest-wins direction is wrong in principle
     for an "oldest outstanding" stamp, but only the deprecated %APPDATA% fallback can produce a
     second copy and nothing writes it any more, so this is recorded, not coded around.
   - **The Slack silence on `partial:` is deliberate and is NOT "exception-only without a watcher".**
     Rule 16's order is honoured: the stamp lands, the checker reads it and escalates, and only then
     is the per-run page removed. Re-adding an `info` notify on every partial re-creates the daily
     page this rule exists to remove.
   **Single-instance guard, same commit.** 🔴 2026-09-03 09:03: three `sync_logon` invocations
   overlapped — `appyhour_sync_on_logon` at 09:20 and 09:40 (two logons) and
   `appyhour_sync_daily_noon` at 09:30 — with NO guard between them; the 09:30 one exited
   `0x40010004` (process terminated). Both tasks run the same script; the DB writelock serialises
   their WRITES, but the polls between writes are not serialised, so they double-polled ParcelPanel
   for nothing and each one's 600s ceiling was spent partly waiting on the other. `sync_logon.main`
   now takes `C:\AppyHourData\sync_logon.lock` (`{pid, started_at}`, `O_CREAT|O_EXCL`) first:
   - **Live holder → print `sync_logon already running (pid N since T) — exiting`, exit 0, stamp
     NOTHING.** Exit 0, not 1: a refused duplicate is not a failure, and a non-zero exit would light
     up `check_schtasks` for the task that correctly stood down.
   - **Dead holder → stale lock, take over, log it.** Liveness is `appyhour_lib.db.pid_alive`
     (Windows `OpenProcess` + `GetExitCodeProcess == STILL_ACTIVE`, ctypes). 🔴 NEVER
     `os.kill(pid, 0)` on Windows: there it is `TerminateProcess(pid, 0)` — it KILLS the process it
     was asked to probe. A test proves the probe is true for a live PID and false for an exited one.
   - **Released in a `finally`, only if it still holds OUR pid** — a successor that took over a
     stale lock must not have its lock deleted by the corpse's unwinding.
   - **The two schtasks are NOT touched.** The guard is the fix; the triggers stay. Do not "fix"
     the overlap by deleting the noon task or staggering the logon delay (rule 15's last bullet:
     scheduling narrows windows, it removes nothing).
   - The lock lives beside the canonical DB at `C:\AppyHourData`, for the same MSIX reason as every
     file above (rules 3, 3b, 17): both tasks run real-context today, but a `%APPDATA%` lock would
     be invisible to any packaged caller and silently un-guard the pair.
19. **A prod script that imports `appyhour_lib` without first putting `C:\AppyHourProd\AppyHour` on
   `sys.path` runs the DEV library — byte-identical to dev, and rule 9's parity check calls it
   "in sync".** 🔴 The mechanism (measured 2026-09-03): `appyhour_lib` is a pip EDITABLE install.
   `site-packages\__editable___appyhour_1_0_0_finder.py` carries
   `MAPPING = {'appyhour_lib': 'C:\Users\Work\Claude Projects\AppyHour\appyhour_lib'}`, and its
   `_EditableFinder` sits AFTER `PathFinder` on `sys.meta_path` — so the prod package wins ONLY when
   some `sys.path` entry already holds an `appyhour_lib/`. A script under
   `C:\AppyHourProd\AppyHour\<subdir>\` has only its own subdir on `sys.path[0]`; unless it (or a
   module it imports first) inserts the parent BEFORE the first `import appyhour_lib`, every
   `appyhour_lib.*` call it makes — `paths.db_path`, `db.connect`, `heartbeat.beat`, `notify` —
   executes whatever the dev tree holds at that moment, mid-edit included. Found while repointing the
   ShippingReports `.bat`s (three schtasks had been `cd`-ing into the dev tree outright);
   `weather_sync_cron.py` (retired) demonstrates it; the first real run of the check found
   `scripts/backup_offsite.py` (`AppyHour Weekly Offsite Backup`) doing it live: its only insert is
   its own dir, its `appyhour_lib.heartbeat`/`notify` imports are inside functions, nothing pins
   the parent.
   **The check:** `automation_health.check_prod_entry_points` enumerates every schtask whose
   `Task To Run` is under `C:\AppyHourProd` (a `.bat` action is parsed for the `.py` it launches —
   `%~dp0`/`set VAR=`/`cd /d` tracked), then simulates each script's `sys.path` edits against its
   FIRST `appyhour_lib` import in execution order, following sibling imports two levels deep with
   the same path state. CRITICAL per SCRIPT (`prod-libpath-<file>`), tasks listed in the finding.
   `check_editable_mapping` reads the finder's `MAPPING` and WARNs if it no longer points at the
   dev tree (dev-side only: tests and MCP servers would then run that tree's code) or is missing.
   NEGATIVES:
   - **Do NOT "fix" this by repointing the editable install, adding a prod `.pth`, or a
     `sitecustomize`.** Repointing breaks dev; a `.pth` in the shared interpreter is a system change
     that alters what EVERY Python process on this machine imports — Kurt decision. The check
     detects; the fix per script is the `postmortem_runner.py:27` shape:
     `sys.path.insert(0, str(Path(__file__).resolve().parents[N]))` before the import.
   - **A pin is judged by WHERE it points, not by its presence.** `sys.path.insert(0, HERE)` is the
     commonest shape in this tree and pins nothing — the library is one level up. A literal
     `C:\Users\Work\Claude Projects\…` insert is a pin to the DEV tree, the trap made explicit
     (`AppyHourMCP/tools/gorgias_sheets_sync.py` carries one inside a function — harmless there only
     because `utils` imported the prod package first; do not copy it).
   - **The FIRST import decides.** `sys.modules` caches the package, so a later pin changes nothing;
     the simulation stops grading at the first import and a fix must land BEFORE it.
   - **Static AST, never an import of the target.** Every target is a live action (ingest, backup,
     Gorgias sync). "Prove the deploy" the same way: assert `appyhour_lib.__file__` starts with
     `C:\AppyHourProd` AND no `sys.path` entry contains `Claude Projects` — an `import` with rc 0 is
     not proof.
   - **An unresolvable pin is trusted, never guessed** (a function result, an env var). Bounded:
     every live entry point's pin is a `Path(__file__)`/`.parent`/`.parents[N]`/`/` shape the
     evaluator resolves, so the lenient branch is not the one grading the live set.
   - **Rule 9 and this rule fail independently and neither implies the other.** Parity says the
     BYTES match; this says the bytes that RUN are the ones that matched. Both green is the only
     "prod runs prod".

20. **The dead-man switch runs 7 DAYS A WEEK — a watcher that only watches on weekdays is not
    headless, it is staffed.** 🔴 THE BURN (2026-09-07, Kurt GO: *"if its for headless stuff, then
    yes build it"*): the routine `automation-health-daily` was cron `15 12 * * 1-5`. The
    `fulfillments` ingest leg crossed its 36h no-success threshold on **Saturday 2026-09-06 21:36**
    and nothing looked at it until **Monday 12:15** — 38 unwatched hours on an ingest leg, and the
    same hole every weekend. The checker was right; nobody was scheduled to run it. Rule 1 says
    silence is the failure signal — this is the schedule half of the same idea: a checker nobody
    runs produces silence indistinguishable from health.
    NEGATIVES, each a design that was considered and is worse:
    - 🔴 **Do NOT add a second, weekend-only task.** Both would run `automation_health.py` and both
      would `beat("automation-health")` — TWO WRITERS ON ONE HEARTBEAT KEY, so a beat from either
      makes the other look alive and `check_beats` cannot detect either dying. That is the
      DUAL-OWNER class of rule 14 / `check_task_set` (the FreshnessSweep pair, 2026-08-31), and
      putting it on the dead-man switch itself is the worst available place for it. **ONE task,
      7-day cron, scope gated INSIDE the script** (`is_weekend_run` + `WEEKEND_ELIGIBLE_BEATS` +
      `SCHTASK_WEEKEND_ELIGIBLE`).
    - 🔴 **Do NOT run every check at the weekend.** A weekday-only subject cannot have failed yet
      on a Saturday: `warm-cohort-report` runs Monday, so a 5-day-old beat on Saturday is what
      HEALTHY looks like. Paging on it is an alarm nobody can act on — rule 4's alarm-deafness,
      and the reason the one page that matters gets skimmed. **Weekend runs are CRITICAL-only:**
      no INFO, no counts, no config/deploy hygiene (`check_task_set`, `check_prod_parity`,
      `check_prod_entry_points`, `check_editable_mapping` are weekday-only for exactly this).
    - **Eligibility is HAND-TRIAGED off each subject's own trigger, never inferred from a name.**
      Same shape and reason as `SCHTASK_EXPECTED`. Weekend-eligible today: beats `offsite-backup`
      (Sunday 02:00 schtask) and `automation-health` (this checker's own, rule 7); schtasks
      `AppyHour Carrier Invoice Sync`, `appyhour_sync_daily_noon`, `appyhour-db-healthcheck`,
      `AppyHour Weekly Offsite Backup`, `appyhour_sync_on_logon`. Always-on regardless of day:
      `check_sync_heartbeat`, `check_shipping_db`, `check_replica_freshness` — their subjects run
      daily, and the first is the one that went unwatched.
    - 🔴 **A weekend run must NOT call `dispatch_findings`.** `finalize(seen)` resets every key
      absent from the run, and a weekend run deliberately omits most keys — so a weekday finding at
      2/3 on Friday would be reset twice and could NEVER reach 3, silently disabling rule 12's
      escalation. Streaks are frozen at the weekend: "consecutive" means consecutive WEEKDAY runs,
      the cadence the counter was calibrated on. A weekend CRITICAL still pages every run.
    - 🔴 **DESTINATION IS NOT A KNOB — #kurt-ops (private) or nowhere** (Kurt 2026-09-07: *"just
      make sure it pings kurt ops and no non private channels"*). Every alarm leaves through
      `appyhour_lib.notify.notify()`, which resolves `AH_SLACK_CHANNEL` → `KURT_OPS_CHANNEL`
      internally; no caller passes a channel and no channel/user id is written into a script or a
      SKILL.md. **NEVER reintroduce an incoming webhook:** its destination lives in the URL and
      cannot be re-pointed, which is how cloud alarms landed in the public `#reships` on
      2026-08-31 and CS agents read `invoice_ingest CRASHED` as an instruction. See rule 5 and
      `appyhour_lib/notify.py`'s module docstring.
    - 🔴 **THE LOCAL CRON IS STILL `1-5`, AND THAT IS THE HONEST ANSWER — the weekend owner has to
      be CLOUD-SIDE (open Kurt decision, 2026-09-07).** The scope gate above shipped; the schedule
      did not, and it must not be forced. `~/.claude/hooks/align-gate.sh:102` DENIES any weekend or
      every-day cron: *"machine off weekends, missed runs don't rerun (7/04 burn)"*, and
      `catch-up-missed-tasks.sh:8` says the same (*"machine is off Sat/Sun; Kurt doesn't want
      weekend runs OR weekend catch-up"*) — that hook is a SessionStart catch-up anyway, i.e. it
      needs Kurt to open Claude, which is staffed, not headless.
      So Kurt's dead-job rule bites: **a schedule that cannot catch up is a dead job.** A local
      `15 12 * * *` on a machine that is off Sat/Sun would fire nothing, catch up nothing, and
      report green on Monday — a weekend watcher that is itself unwatched, which is a WORSE state
      than the known 38h hole because it looks fixed.
      🔴 Do NOT weaken `align-gate.sh` to get past this. The guard's own escape clause is "unless
      Kurt explicitly asked for weekends", and the substance of what Kurt asked for is weekend
      COVERAGE, not a local cron — granting the exception without the machine being on just buys
      the appearance.
      **Evidence considered and rejected as insufficient:** the one-time `audit-remainder-saturday`
      fired ~29h late (`fireAt` 2026-09-05 16:00Z → `lastRunAt` 2026-09-06 21:25Z), which shows a
      one-time `fireAt` task RE-ARMS and lands late. That is a different mechanism from a recurring
      cron OCCURRENCE, and no recurring task in the current set has been observed catching up a
      missed fire. Do not cite it as proof that a 7-day cron self-heals.
      **Therefore the weekend owner is a cloud check** (App Platform, the same destination decision
      already recorded for `freshness-sweep`), running this same scope-gated script so there is
      still ONE grader and ONE `automation-health` beat writer. Until that exists the weekend hole
      is OPEN and is recorded here as open — rule 1: a gap nobody has closed must not be written up
      as closed.
    - **Do not tighten `automation-health`'s 4d max-age just because it now beats daily.** A
      routine's fire is late-on-catch-up by design, so the legal gap between two healthy beats is
      not 24h (rule 4).

21. **A sent vF is not an applied cohort — the ship-day apply assert lives on the CLOUD worker, never
    here.** 🔴 RMFG_20260825 and RMFG_20260901 (ShipRouting BUG_LOG 2026-09-07, NO-APPLY-CONTROL ×3)
    shipped with the sheet at RMFG and Shopify BARE; the one Tuesday flow job died `failed` at
    05:56 ET and nothing paged — the console only pages failures the WORKER ran, and this PC (where
    `apply_tuesday.py` runs) is off on ship mornings, so a local beat could never have caught it.
    Guards: `ShipRouting/server/apply_watch.py` timers `apply_completion_watch` (12:00 ET: every
    cohort with a built vF for today has a gate-confirmed done apply OR a mirrored CLI apply record
    in `apply_record_blobs`) and `failed_flow_job_alarm` (any failed `flow`/`vf_build`/`route_edit`
    job on a ship day, once per job id). Both durable-dedupe their pages (`apply_watch_alarms`,
    `failed_job_alarms`) and freshness-assert off their own `watch_runs` ledger, surfaced on
    `GET /health/timers`. NEGATIVES: never add a local `beat()`/schtask for the apply assert (rule 4
    dead-cadence by construction on a machine that is off); never a success ping; a CLI Tuesday apply
    that fails to mirror `tuesday_apply_results.json` WILL page — fix the mirror, do not silence the
    watch. Tests: `ShipRouting/server/tests/test_apply_completion_watch.py`.

22. **An unshippable order must ALARM, and it must be held in the STANDARD vocabulary — the
    detection is on the cloud PREWARM, never here.** 🔴 `#175517` (Key West FL 33040, Large Tray)
    had no legal lane, was held by hand with the invented tag `HOLD_KeyWest_NoLegalLane_20260821`,
    and sat UNFULFILLED 08-20 → 09-07 (ShipRouting BUG_LOG `NO-LEGAL-LANE`). That token is in no
    vocabulary: `vf_checks.HOLD_TAGS` does not contain it, so no sheet gate, report or alarm could
    see it — and the cohort tag had been removed too, so nothing else saw the order either. Every
    gate read green for 18 days. Guard: `ShipRouting/server/flowhold_watch.py` — the prewarm
    (`server/prewarm_job.run`, cloud, 3h) evaluates every live cohort order through the ONE shared
    legality check (`lib/lane_legality.py`) and, on a CONFIRMED no-lane, writes the standard
    `_FLOWHOLD` (add-only, verified by Shopify read-back) and pages `#kurt-ops` ONCE per
    (order, evidence fingerprint) through `apply_watch.slack_notify` — one notifier, no webhook.
    Timer `flowhold_watch` (15 min) drains only the DURABLE backlog (saturation overflow, retryable
    writes, ambiguous deliveries) and freshness-asserts off the shared `watch_runs` ledger.
    NEGATIVES: never write a bespoke `HOLD_<place>_<reason>_<date>` tag — an invented hold is an
    invisible hold, and that is the whole burn; never hold on an ABSENT quote or an unreadable
    coverage file (that is `UNKNOWN_EVIDENCE`, not proof — §17.14 / wk0810); never strip a cohort
    tag to "hold" an order; never claim the hold exists on a failed write; never propose air for a
    no-air box; never add a local beat/schtask for this (this PC is off when the prewarm fires —
    rule 4 dead-cadence by construction). Kill `FLOWHOLD_WATCH=0`, and the freshness assert then
    RAISES by design. Tests: `ShipRouting/tests/test_lane_legality.py`,
    `ShipRouting/server/tests/test_flowhold_no_legal_lane.py`; rules ShipRouting ROUTING_RULES
    §0-K + TECHNICAL_PRINCIPLES P-hold.

23. **A freshness assert on something that legitimately takes HOURS states a PACE, not a level —
    and an alarm that fires on a healthy state is a dead alarm.** 🔴 2026-09-07: the cloud
    `prewarm` timer's `STALE after run` page fired FIVE times in one day while the quote fill was
    healthy and progressing (ship week 2026-09-14: 1,212/9,810 at 09:57 → 6,480/9,675 that evening,
    ~477 lanes/h, on pace days before the Friday build). Kurt: *"if we know it's healthy, then we
    don't need an alarm."* The predicate was a LEVEL — `uncached > tail_bar()` — true from the first
    minute of every fill, so it was guaranteed to page on a healthy Monday; this is rule 1's "silence
    is the failure signal" over-applied until noise became the failure signal instead.
    🔴 **Two compounding traps, both general.** (a) **An alarm message that embeds live counts
    defeats every digest-keyed throttle**: `ingest_worker._alarm_due` re-pages whenever the text
    changes, so a message carrying "1,212/9,810" minted a new digest every fire and bypassed the 6h
    repeat window by construction. Dedupe per-INCIDENT and durably, never on message text.
    (b) **Progress stated against a moving denominator is progress against nothing**: `requested`
    drifted 9,810 → 9,675 across the same day, and `uncached` legitimately RISES as Recharge
    converts charges overnight. Guard: `ShipRouting/server/prewarm_progress.py` — pages only on
    `no_progress` (`cached` flat across ~6h), `projected_miss` (trailing rate clears the §17.9 tail
    bar after noon ET Friday) or `pre_build_short` (build day arrived, still short — the
    history-free backstop); ONE durable page per (ship date, reason) via the ship-day guards' own
    `apply_watch.guard_alarms` + `watch_runs`, and the marker is RESOLVED when the fill recovers so
    a later stall pages again. NEGATIVES: never measure progress on `uncached` (it rises while the
    fill works); never re-express the bar as a percentage (a ratio tightens as the denominator
    grows); never add a second dedupe table when `guard_alarms` exists; never let the quiet stand
    alone — `GET /health/timers` carries a `prewarm_progress` block (coverage, stated basis, rate,
    projected-done) and DEGRADES its own verdict while an incident is open, because a
    claimed-and-quiet alarm reading green is the silence-equals-health hole again; never add a local
    beat/schtask for this (this PC is off when the prewarm fires — rule 4 dead-cadence by
    construction). Tests: `ShipRouting/server/tests/test_prewarm_progress.py` (replays the measured
    09-07 day and demands ZERO pages); rules ShipRouting ROUTING_RULES §17.17 + §17.9, BUG_LOG
    2026-09-07 DEAD-ALERT.

24. **A BOUNDED RUN MUST ADVANCE, NOT REPEAT — and TWO LEGS NEVER SHARE ONE BUDGET OR ONE
    STAMP.** 🔴 2026-09-04 → 2026-09-07: `sync_logon`'s `fulfillments` leg had not stamped a
    success for **~80 hours** against the 36h rule-18 threshold, reporting
    `partial:Timeout:600s:3365 rows committed; remainder re-selected next run` on every run.
    **There was no remainder mechanism.** That phrase described an intention; no code implemented
    it. This is rule 18's blind spot: the `partial:` split correctly stopped paging for a
    *draining* backlog, and then a backlog that could not drain wore the same stamp.

    **What was actually happening,** from the live log of 09-07 09:45 and a read-only probe of
    the canonical DB — not reconstructed:
    - `backfill_sync.sync_parcel_panel` built its work list as `SELECT DISTINCT order_number`
      with **no ORDER BY and no persisted position**, then walked it from index 0. Work list
      **2,576 orders; the run stopped at order 800.** The next run rebuilt the identical list and
      started again at order 1. **Orders past index 800 were never asked — not once.**
    - `SELECT DISTINCT` orders by TEXT (SQLite's temp B-tree). Order numbers had grown past five
      digits, so `101334` (new) sorted ahead of `94080` (old): **the newest work was polled first
      and the oldest starved.** The queue's oldest member was fulfilled **2025-05-22** and sat in
      the unreachable tail — the 604 class, re-created by scheduling after rule 7 fixed selection.
    - Progress happened anyway, by **attrition**: orders inside the reachable prefix that PP
      answered as delivered left the set. Row counts therefore looked like forward motion.

    NEGATIVES:
    - **🔴 A COUNT OF ROWS IS NOT EVIDENCE OF PROGRESS THROUGH A QUEUE.** `3365` was 2,565
      `fulfillments` rows PLUS 800 `delivery_status` rows — **a sum across two TABLES**, published
      as though it were progress on one. Every stamp for a bounded leg carries what is LEFT
      (`remaining`, `unresolved`) and how old the oldest untouched item is; `written` alone cannot
      distinguish 800-of-800 from 800-of-2,576, and for four days it did not.
    - **🔴 A RESUME POSITION IS NEVER AN INDEX.** The list changes size between runs, so an index
      skips work with nothing to notice it. The cursor is per-item and written in the SAME
      transaction as the work: `delivery_poll_attempts.last_attempt_at`. Orders just polled sort
      to the back by construction, so "where we got to" survives a crash and needs no bookkeeping.
    - **🔴 ORDERING IS PART OF THE CONTRACT, NOT AN OPTIMISATION.** Never-attempted first, then
      least-recently-attempted, oldest first within each. `delivery_window.due_work_list_sql()`
      owns it. An unordered `SELECT DISTINCT` is not "no ordering" — it is an accidental one, and
      here it was exactly backwards.
    - **🔴 TWO LEGS IN ONE STAGE MEANS ONE STARVES THE OTHER AND NEITHER REPORTS HONESTLY.**
      `fulfillments` and `delivery_poll` are now separate stages with separate keys. Under one
      key, `_stamp("fulfillments", "ok")` required BOTH to finish, so the Shopify leg — which
      completed and advanced its watermark on **every** run — held a last-success frozen for 80
      hours while `fulfillments.fulfilled_at` was current to that morning. **The alarm was firing
      on the wrong leg**, and no amount of grading logic could have found that from one key.
    - **🔴 THE SOFT BUDGET IS NOT A SECOND WATCHDOG, AND IT IS SMALLER THAN THE HARD ONE.**
      `DELIVERY_POLL_BUDGET_S` (480s) < `STAGE_TIMEOUT_S` (600s). A watchdog cancel raises out of
      `_flush` and **skips the epilogue** — the final flush, `aged_out_sweep` (the only live
      writer of the terminal state), and the remaining/oldest re-count. So the leg that was
      supposed to shrink the tail could only grow it. A leg that can be bounded must bound
      ITSELF and stop at its own boundary; the watchdog stays above it for a genuine hang.
      **🔴 `STAGE_TIMEOUT_S` was NOT raised** — rule 17 still forbids it, and splitting the stage
      is what bought the budget.
    - **🔴 A RATE CAP AND A TIME CAP ARE BOTH REQUIRED.** Time alone lets a fast ParcelPanel day
      empty the queue at full tilt against a limit shared with other callers; a call cap alone
      says nothing about a slow day. `DELIVERY_POLL_MAX_REQUESTS` and `DELIVERY_POLL_BUDGET_S`
      bite at roughly the same place normally, and the slower one wins when it does not. Neither
      replaces the limiter (`pp_ratelimit`), which still owns pacing and 429s.
    - **🔴 `except PPThrottled: continue` IS A BUSY-RETRY.** With the limiter refusing
      everything, the loop walks the whole list at full speed doing no work, burns the elapsed
      budget, and then reports `partial:` as though a backlog were draining.
      `THROTTLE_ABORT_STREAK` ends the run on an unbroken refusal streak; the streak resets on
      ANY served order, including one PP answers with nothing. A storm is an outage (rule 18's
      "zero rows in a whole ceiling"), not a slow day.
    - **🔴 A RE-POLL GETS A BACKOFF; A FIRST POLL NEVER DOES.** And the backoff ladder must sum
      to less than the age gate that retires an order — otherwise backoff strands boxes a second
      time, by a new mechanism. `POLL_BACKOFF_HOURS` sums to 186h to bank 6 attempts against a
      120-day gate; the test pins the arithmetic, not the constants.
    - **🔴 `ok` MEANS THE QUEUE WAS DRAINED, NEVER THAT ROWS LANDED.** `ok` advances last-success
      and puts the leg to sleep for 12h under `_should_run`. The leg this replaces banked 800
      rows every run and never reached its tail; had "rows landed" produced an `ok`, the stall
      would have been silent instead of merely mis-attributed.
    - **A missing ParcelPanel key stamps `skipped:`, not `ok`.** `ok` would claim a queue was
      drained that was never read. `skipped:` grades as not-ok in `check_sync_heartbeat`, which
      is correct: a delivery poll that cannot run is a defect, quietly.
    **🔴 THE SECOND POLLER, CLOSED 2026-09-08 — and the reason it was NOT already "fine".**
    `daily_shipping_sync.run_pp_sync` shares this queue and carried the identical
    unordered-restart shape. It was left open on 09-07 because its predicate was genuinely
    different. It had not *visibly* bitten only because it had **no budget to truncate the
    queue** — which is not a defence, it is the other failure: an unbounded poll is a
    28-minute (once ~180-minute) stage sitting in front of Gorgias, reclassify and the Tue/Fri
    postmortem on a task that fires at 12:00. Both halves had to land together, because either
    one alone is a NEW bug: **an ordered queue with no budget blocks the day, and a budget over
    an unordered, position-less queue truncates the queue permanently.** Never add a cap to a
    poller without `delivery_window.due_work_list_sql()` underneath it.
    - **🔴 THE CURSOR IS BANKED PER FLUSH, IN THE SAME TRANSACTION AS THE WORK — NEVER ONCE AT
      THE END.** `run_pp_sync` used to call `record_attempts` after the loop, inside the same
      `try:` as `aged_out_sweep`. Under a budget that is a fresh instance of this rule wearing a
      different mechanism: a run that stops early — or an epilogue that raises — leaves NO
      cursor for orders it *did* poll, so the next run re-asks exactly what it just asked,
      while the delivery rows still land and every log line still looks like progress. Banking
      it separately is equally wrong in the other direction: an attempt counted for an order
      whose answer was not durably stored inflates the counter `aged_out_sweep` gates on, and an
      inflated counter retires a LIVE box early. A lock loss therefore holds BOTH, never half.
    - **A DIFFERENT PREDICATE IS RESOLVED BY MEASUREMENT, NEVER ABSORBED.** Two clauses stood
      between this poller and the one owner, and each was checked read-only against the
      canonical DB before it moved: the `pickup_date IS NULL` re-pull **selected 0 orders**
      (work list 2,796 with it, 2,796 without — all 28,398 delivery_date-set/pickup-NULL rows
      are already `delivered`), so it is DROPPED; and the `tracking_number` join → the canonical
      `order_number` join **drops 87 and adds 0**, all 87 already carrying a landed order-level
      `delivery_date` and unreachable only because the fulfilment's tracking ≠ the
      `delivery_status` row's (99 such tracking numbers; the OnTrac→LaserShip label switch).
      Multi-leg was measured too: 19 orders with >1 tracking, **0** mixed. 🔴 The fix for a
      genuinely-missing predicate is a change in `delivery_window` — the ONE owner — never a
      second predicate left in the poller.
    - **A LEG WITH NO STAMP STILL OWES THE SAME REPORT.** `daily_shipping_sync` logs, it does not
      `_stamp`, so `complete=`, `remaining_due=`, `unresolved=` and `oldest_due_days=` go in its
      log every run, and a failed re-count writes UNKNOWN, never 0. A lock-busy schema step logs
      `UNTOUCHED` — nothing polled is not a drained queue and not a dead feed.
    - **THE GUARDS DIVIDE BY WHAT THE RUN REACHED.** With a budget, `polled` and `len(orders)`
      are different numbers; `errors < len(orders) * 0.1` would let a 200-order run that failed
      on 90% of what it touched report success against a 2,796-order denominator.

    Constraints: `SHIPPING_PIPELINE.md` §3 rule 7 (the queue's own contract) ·
    `GelPackCalculator/delivery_window.py` module docstring (negatives 4-6).
    Tests: `tests/test_delivery_poll_resume.py` (22) + `tests/test_daily_pp_sync_resume.py` (17)
    — both offline: fake clock, fake PP client, in-memory sqlite; no writer `main()`, no live
    DB, no network.

## Wired beats (update when adding/removing)

| name | writer | max age |
|------|--------|---------|
| `offsite-backup` | `scripts/backup_offsite.py` end of successful `run()` | 8 days |
| `forecast-a-monitor` | `_outputs/scripts/forecast_a_monitor.py` | 8 days |
| `loop-scorecard` | `ShipRouting/scripts/loop_scorecard.py` | 8 days |
| `corrections-mining` | `_outputs/scripts/corrections_digest.py` | 8 days |
| `automation-health` | `scripts/automation_health.py` self-beat | 4 days (routine is 7-day as of 2026-09-07, rule 20; 4d stays — a catch-up fire is late by design, and 2d graded every Friday run stale on Monday under the old Mon–Fri cron) |
| `freshness-sweep` | `_outputs/scripts/freshness_sweep.py` (weekly data-freshness monitor — Mon 12:33 Claude scheduled task; beats on run, flags or not) | 8 days |
| `pytest-shiprouting` | `_outputs/scripts/pytest_cadence.py` (weekday ShipRouting fast-tier suite via `~/.claude/hooks/catch-up-missed-tasks.sh` — stamp-guarded; Slack only on red, beat every run) | 4 days |
| `warm-cohort-report` | `_outputs/scripts/warm_cohort_report.py` end of `main()`, after the report file is written (routine `warm-cohort-report`, Mon ~14:10) | 10 days |
| `shipping-cost-sheet` | `_outputs/scripts/shipping_cost_report.py` INSIDE the `--push` branch, after `push()` returns a URL (routine `shipping-cost-sheet`, Mon ~13:09) | 10 days |
| `vendor-matrix` | `ingest/slack_reship/sync.py` end of `main()` when `--report` and NOT `--push` (routine `weekly-shipping-vendor-matrix`, Tue ~12:00) | 10 days |
| `slack-reship` | `ingest/slack_reship/weekly_task.py` (routine `weekly-reship-report`, Tue ~12:00) — pre-existing beat, promoted into `EXPECTED` 2026-08-31 | 10 days |
| `truffle-watch` | `InventoryReorder/Errors/truffle_autoswap.py` `_beat_if_completed()` — every completed action (`none`/`swap`/`missed_window`/`no_candidate`), NEVER on `error` (routine `truffle-watch-christine-farley`, Mon–Fri 12:11; edits a live Shopify order) | 4 days |
| `wrong-address-handler` | `scripts/automations/wrong_address_automation.py` `__main__`, only when `main()` returned 0 (routine `wrong-address-handler-daily`, Mon–Fri 12:36; `--apply-untag` removes live order tags) | 4 days |
| `sku-lifecycle-scan` | `InventoryReorder/Errors/sku_lifecycle_scan.py` end of script, after the dated report CSV is written (routine `sku-lifecycle-scan-weekly`, Mon ~14:20) | 10 days |
| `carrier-sla-monitor` | `_outputs/scripts/coldchain_health_brief.py` end of script, after `coldchain_health_brief_latest.md` is written (routine `carrier-sla-monitor-weekly`, Mon ~14:35; posts to Slack ONLY on anomaly, so the beat is its sole liveness evidence) | 10 days |

**Still LOUD, deliberately (2026-09-03, from the Migration Triage coverage audit):**
`ops-issues-weekly-update` — its target `AppyHourMCP/run_gorgias_update.py` was another session's
mid-flight (dirty) file the day the rest were wired, so rule 10 applied; wire `beat("ops-issues")`
at the end of its `main()` after the enrich+linkify succeed, add the `EXPECTED` row (10d), and only
then may its Slack step go exception-only. `evo-transfer-monday-reminder` — no script; it is one
pinned `appyhour_lib.notify --file` command, and the routine allowlist matches that command
byte-for-byte, so a beat there means a Kurt-approved command change, not a code edit. The three
`prewarm-carrier-tnt-*` routines are covered by their receipt+coverage gate
(`prewarm_receipt_<SHIP_DATE>.json`) rather than a beat — stronger than a beat, no row wanted.

Checker also probes (no beat needed): `C:\AppyHourData\sync_heartbeat.json` age (>48h, via
`appyhour_lib.sync_heartbeat.read()` — moved off `%APPDATA%` 2026-09-01, rule 3b), `schtasks` AppyHour* Last
Result ≠ 0, shipping.db `PRAGMA quick_check` (read-only immutable), **dev↔prod tree parity on
DB-relevant `*.py` — CRITICAL only when the file is reachable from a prod entry point, otherwise a
body-only count (rules 9 + 9b)**, **cloud-replica freshness — `shopify_orders`/`weather_history` data
age + `C:\AppyHourData\replica_pull_stamp.json` ingest stamp (rule 11)**, **prod entry-point library
path — every schtask action under `C:\AppyHourProd` statically checked for a prod pin before its first
`appyhour_lib` import, plus the editable-install `MAPPING` itself (rule 19)**.

Ledger file: **`C:\AppyHourData\heartbeats.json`** (moved off `%APPDATA%` 2026-08-31, rule 3).
Access it ONLY through `appyhour_lib.heartbeat.beat()` / `read_ledger()` — a hand-rolled
`%APPDATA%` path is a second ledger that diverges silently. `slack-reship` is now an `EXPECTED` row
(2026-08-31); ✅ `freshness_sweep.py` D3's duplicate row for it — own 8d constant, hand-rolled
`%APPDATA%` read — was **retired the same day** (workspace-root `9a771a7`; a tombstone comment there
records why it is a delete and not a repoint), leaving the rule-13 loop as the single checker on the
one canonical 10d threshold. 8d was also the wrong number for the reason rule 4 gives: a catch-up run
after a slept-through weekly slot legally lands >7d out. ⚠️ The now-orphaned `SLACK_RESHIP_MAX_D = 8`
constant still sits at `freshness_sweep.py:271`, referenced only by its own tombstone — delete it
before someone greps it up and re-adds a per-name row against a second expectation table (rule 4).
