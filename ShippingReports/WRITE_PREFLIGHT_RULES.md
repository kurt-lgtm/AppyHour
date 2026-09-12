# WRITE_PREFLIGHT_RULES.md — pre-flight writer-collision check (constraints SSOT)

> 🔴 **PRE-CHANGE GATE.** Read this BEFORE touching `appyhour_lib/write_preflight.py`,
> `scripts/db_write_gate.py`, or any writer's preflight call site. Change the rules HERE
> first, in the same commit as the code.

## 🧭 North Star

**No writer against `C:\AppyHourData\shipping.db` ever starts while another writer — running,
pending, or about to be scheduled — can touch the same surface; and when one is detected, the
writer REFUSES and NAMES it.** The goal is not "fewer collisions"; it is that a collision is
impossible to walk into unknowingly. A change that makes the check quieter, softer, or easier
to bypass moves AWAY from this even if every test still passes.

## 🔴 Gotchas / negatives FIRST — the burns this exists to stop

1. **Three WAL corruptions (2026-06-27, 07-01, 07-03) were caused by a SECOND CONCURRENT
   WRITER with no mutual awareness** — never by "a repair script ran". Under MSIX one path
   string resolved to two physical files, so two writers each folded their own WAL into one
   shared image. Do NOT describe the fix as "we stopped running repair scripts"; the writers
   were legitimate, and they could not see each other.

2. **🔴 2026-09-07 — the PENDING half, which nothing checked at all.** A review agent went to
   land `GelPackCalculator@2e60c45` and found **93 lines of another session's uncommitted
   work** in `shipping_invoice_db.py`: session `UPSParserFix` was hand-implementing the same
   line-summing fix inside the very functions that commit deletes. Neither side was warned.
   **A writer does not have to be RUNNING to collide.** Uncommitted in-flight work on the
   same source is a writer that is about to run, and it is invisible to every process-level,
   lock-level and sqlite-level check ever built here. This is the axis Kurt named:
   *"then make it so that we check against any running writers ... or pending writers"*.

3. **🔴 "The lockfile is free" proves nothing.** Measured 2026-08-20: **25 of 33 files that
   write `shipping.db` open it with raw `sqlite3.connect`** and never touch
   `<db>.writelock` — among them `AppyHourMCP/tools/cache.py`, which runs inside the live MCP
   servers. Only the `BEGIN IMMEDIATE` probe and the PROCESS scan see those. Never report a
   free advisory lock as "the DB is quiet".

4. **🔴 UNKNOWN is NEVER idle** (`COORDINATION_RECORDS_RULES.md` gotcha 1). A session with no
   beat or a stale beat is UNKNOWN. Absence of beats is absence of evidence, so an empty
   `busy-state.jsonl` yields `coordination_verdict = UNKNOWN`, never "clear". Do NOT
   "simplify" that into a pass because it reads as noise.

5. **🔴 A stale lock is broken; a LIVE lock is NEVER auto-stolen.** A dead holder walling a
   writer forever is a deadlock, not safety. An alive holder is a real writer, and taking its
   lock is exactly the collision this module exists to prevent. Both halves are pinned by
   tests; neither may be relaxed alone.

6. **🔴 A probe that could not RUN is not a clear result.** Failing to enumerate processes or
   read scheduled tasks REFUSES. "We couldn't check, so we assumed quiet" is how a silent
   degrade becomes an outage (`silent-degrade-class`).

7. **🔴 There is deliberately NO bare `--force`.** The escape hatch is `--force-writer NAME`
   and it must name **every** overridable collider, comma-separated. A bare force is a bypass
   with a polite name, and it becomes a habit within a week.
   **The empirical checks are not overridable at all** — a held sqlite write lock or a failed
   probe. Overriding an *advertised* writer is a judgement call; writing over a *held write
   lock* is the corruption mechanism itself.

8. **🔴 Observe before you perturb.** Sidecar (`-wal`/`-shm`) inspection runs BEFORE the
   `BEGIN IMMEDIATE` probe. sqlite deletes a stray `-wal` on connect when the header is not
   WAL, so probing first destroys the evidence the check exists to read. Found by a test, not
   by review — keep that ordering.

9. **🔴 A hot `-wal` WARNS, never refuses.** A large WAL is entirely normal right after a bulk
   load. Refusing on it would train the operator to reach for `--force-writer` on a
   non-event, which is worse than the signal is worth.

10. **🔴 Two copies of a safety list is one copy plus a lie.** `WRITER_TASKS` and
    `BYPASSING_WRITERS` live in `appyhour_lib/write_preflight.py` and are IMPORTED by
    `scripts/db_write_gate.py`. They were duplicated and drifted: db_write_gate's copy was
    missing **both** sync_logon schtasks (`appyhour_sync_on_logon`, `appyhour_sync_daily_noon`)
    — the busiest writer on the machine was absent from the "is a scheduled writer about to
    fire" check. Never re-fork them.

11. **🔴 The check alone is a TOCTOU race.** Two writers can both pass a clean preflight a
    millisecond apart. `writer_lock()` takes the surface lock AFTER the preflight passes and
    holds it for the write. A caller that only calls `assert_no_conflicting_writer()` gets the
    verdict but not the guarantee — that is correct for a short single-transaction write and
    wrong for a long one.

12. **🔴 Gate the WRITE branch, not the read branch.** `recover_undelivered_from_invoice.py`
    opens `mode=ro` on a dry run and cannot collide; gating that path would train the operator
    to pass `--force-writer` on a read. Only `--apply` is gated.

13. **🔴 A scheduled task with no `NextRunTime` is not "never".** `appyhour_sync_on_logon` is
    trigger-driven and can fire at any logon. It is covered by the PROCESS scan, not the
    schedule scan. Do not conclude a blank next-run means the task is dead.

14. **🔴 NEVER decode the process enumeration with `text=True` — and an EMPTY result is a
    FAILURE, not "no colliders" (2026-09-12).** `_live_bypassing_writers` shelled out to
    PowerShell with `capture_output=True, text=True`. Python then decodes with the ANSI
    codepage (cp1252 here), and **one** process whose command line carries a byte cp1252 cannot
    map — measured live: `0x8f` at position 139,321 — raises `UnicodeDecodeError` and destroys
    the **entire** enumeration. Not that one process: all of them. `.stdout` came back `None`,
    and the next line called `.splitlines()` on it, so the guard died with
    `AttributeError: 'NoneType' object has no attribute 'splitlines'`.

    Three separate defects, each worth naming:
    - **A guard that CRASHES is worse than one that refuses.** A traceback reads as "the tool is
      broken", and the obvious next move is to bypass it. A refusal reads as "the answer is
      unknown", which is the truth and stops the write.
    - **This is a SILENT-DEGRADE instance in the guard itself.** The axis that covers the
      **25 of 33 writers on raw `sqlite3.connect`** — the ones the advisory lock structurally
      cannot see — had been returning nothing on this machine. It surfaced only because a
      `shipments.acct` repair happened to call it; no test caught it, because the tests stub the
      subprocess.
    - **An empty process list can never mean "quiet".** This machine always has running
      processes, so empty output means the enumeration FAILED (PowerShell absent, a sandbox
      blocking `Win32_Process`, a nonzero exit). It now fails CLOSED with the return code and
      first stderr line, same doctrine as the exception branch.

    Fix: ask PowerShell for UTF-8 (`[Console]::OutputEncoding`), capture BYTES, and decode
    `utf-8` with `errors="replace"` ourselves. A mangled character in one command line must
    never cost the other 400 rows. Workspace rule this violated: *"Write files with explicit
    UTF-8. cp1252 default breaks on Unicode."*

15. **🔴 The 12 live `AppyHourMCP\server.py` hits are TRUE POSITIVES, not noise.** They write via
    raw `sqlite3.connect` **per batch**, so they hold the advisory lock for a fraction of their
    run and `check_lockfile` waves them through — which is exactly why the process axis exists
    and why an entry is never removed just because a writer "migrated to `db.connect()`".
    A free write lock and a 0-byte `-wal` prove only that nothing is writing *this instant*, not
    that nothing will write a second from now. 🔴 Do not pattern-match this refusal as a false
    positive and reach for `--force-writer`: the sanctioned path is to stop the MCP servers, or
    to name every collider deliberately. Standing rule it enforces: *agents stay READ-ONLY;
    manual writers run only when the MCP servers aren't mid-sync.*

## What it is

`appyhour_lib/write_preflight.py`. Five axes, ALL required — each covers writers the others
structurally cannot see:

| # | Axis | Sees | Blind to |
|---|------|------|----------|
| 1 | **sqlite** `BEGIN IMMEDIATE` probe + `-wal`/`-shm` | a REAL writer holding the lock right now, however it connected | a writer that is between transactions |
| 2 | **surface lock** `C:\AppyHourData\writer_locks\<surface>.writer.lock` | another run of a preflight-aware writer on the same surface | writers that never adopted the gate |
| 3 | **busy-state beats** `_coordination/busy-state.jsonl` | another SESSION claiming this surface | anything that does not beat (⇒ UNKNOWN) |
| 4 | **pending** `git status --porcelain` on the writer's own sources + ACTIVE-CLAIMS | uncommitted in-flight work — **the 2026-09-07 axis** | work not yet written to disk |
| 5 | **process + schedule** `Win32_Process`, `Get-ScheduledTask` | the 25 raw-connect writers and imminent scheduled owners | a writer not on the roster |

### API

```python
from appyhour_lib.write_preflight import assert_no_conflicting_writer, writer_lock

assert_no_conflicting_writer(surface, *, db_path=None, source_files=(),
                             blackout_min=20, force_override=None) -> PreflightReport
with writer_lock(surface, source_files=[__file__]) as report: ...   # + holds the lock
preflight(surface, ...) -> PreflightReport                          # non-raising form
```

`assert_no_conflicting_writer` **raises `WriterCollision`** (a `RuntimeError`) naming the
writer and the axis. It never returns a boolean for a caller to ignore — that is precisely
how the advisory lock came to be bypassed 25 times.

`source_files` is what powers axis 4. **Pass `[__file__]` at minimum**, plus any module the
writer is about to rewrite. Omitting it silently disables the only check that would have
caught the UPSParserFix collision.

`surface` is both the report label and the mutual-exclusion key. Writers that touch the same
rows share a surface so they serialise: `recover_undelivered_from_invoice.py` and
`pp_backfill_aged_out.py` both rewrite `delivery_status.status`/`delivery_date`, so both
declare `"delivery-status-recovery"`.

## Wiring status

| Writer | Surface | Gated |
|--------|---------|-------|
| `_outputs/scripts/recover_undelivered_from_invoice.py` | `delivery-status-recovery` | ✅ `--apply` only |
| `GelPackCalculator/pp_backfill_aged_out.py` | `delivery-status-recovery` | ✅ `--apply` only, before any PP call |
| `scripts/db_write_gate.py` | (operator gate) | ✅ delegates axes 5 + the rosters |
| `GelPackCalculator/shipping_invoice_db.store_shipments` | `invoice-ingest` | ❌ **BLOCKED** — see below |

> 🔴 **`shipping_invoice_db.store_shipments` is deliberately NOT wired.** That file carries
> session `UPSParserFix`'s uncommitted work (branch `restore/sync-perf-2026-06`). Editing it
> would clobber exactly the collision this module was built to detect. The preflight itself
> reports it correctly today — see the live verdict below. Wire it in the commit that lands
> or stashes UPSParserFix's work, not before.

## Verification

```
python -m pytest tests/test_write_preflight.py -q          # 25 tests, offline, scratch DBs only
python -m appyhour_lib.write_preflight <surface> --source-file <path>   # live verdict, read-only
```

🔴 **Tests never point at the live DB and never invoke any writer's `main()`** — several post
to Slack, poll ParcelPanel, or edit live orders. Every test builds its own scratch sqlite file
under `tmp_path` and its own scratch coordination/git fixtures.

## Related SSOTs

`appyhour_lib/CLAUDE.md` (connect/connect_ro contract, single-writer lock, canonical-path
guard) · `HEARTBEAT_RULES.md` rules 14/15/18 · `_config/COORDINATION_RECORDS_RULES.md`
(UNKNOWN-is-never-idle) · `_config/ENGINEERING_GOTCHAS.md` class 12 (surplus write handles).
