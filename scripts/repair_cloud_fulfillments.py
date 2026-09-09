"""Repair the DigitalOcean `fulfillments` table so DO can be canon for it (DO_READ_CONTRACT B1-R).

🔴 DRY BY DEFAULT. Writing requires BOTH `--apply` and `--yes-write-production`.
   Without them this measures the gap, predicts every gate, and touches nothing.

🔴 THE WRITE IS INSERT-ONLY, AND IT IS PERFORMED HERE (2026-08-31, Kurt's go).
   Every earlier revision of this file said the write was delegated to
   `ShipRouting/server/etl_history.py --load`, and four cross-session handoffs called this
   script "ready" on the strength of that sentence. It was not: the file contained zero INSERT
   statements. The scaffolding (measurement, fingerprint, gates, manifest, verification) was
   real; the copy was never implemented. It is implemented now — see `insert_missing()`.

   WHY NOT `etl_history --load` (it is otherwise the better tool, and this is not a slight on it):
     * `etl_history` publishes by FULL REFRESH — staging table, then one atomic multi-table
       `RENAME`. Its own gotcha 2 is correct that live tables are never DELETEd: the former live
       table survives as `etl_rollback_<table>_<token>` and `rollback_publication()` restores it.
       So a refresh is RECOVERABLE. But between publish and rollback, the LIVE table is the one
       built from local — any cloud-only or cloud-newer row is out of the table that readers read.
     * That is safe if and only if cloud-only == 0 AND cloud-newer == 0, re-measured at write
       time. On 2026-08-31 that measurement CANNOT BE TAKEN from this machine: the DO MySQL
       socket times out (`2003 ... timed out`, ~20s) because this egress is not in the cluster's
       trusted sources, and the trusted-sources list is IP-based, so Kurt's real terminal is
       blocked identically. UNKNOWN is not zero.
     * INSERT-ONLY does not need that measurement. It never removes and never overwrites a cloud
       row, so a cloud-only or cloud-newer row survives this repair whether or not anybody
       measured it first. That is the entire reason this path exists rather than a `--load`.
   🔴 If an operator WITH cluster access measures cloud-only == 0 and cloud-newer == 0 there,
   prefer `etl_history` (tested, rollback-token'd) and do not run this. Verified 2026-08-31 for
   the record: `etl_history` carries the same 16 columns, hard-fails on a missing one
   (`etl_history.py:389-391`), inserts them verbatim with no `NOW()` anywhere, and does NOT
   exclude `fulfillments` (`cloud_owned` at `:585` is shopify_orders/weather_history/
   delivery_status only). It satisfies rule 18a. It is disqualified here by reachability, not by
   correctness.

WHY THIS EXISTS
===============
The cloud `fulfillments` writer is not merely flag-off — **there is no `fulfillments` timer in
`server/ingest_worker.REGISTRY` at all**. The cloud copy only ever moved via a manual
`etl_history --load`, and the last one was 2026-08-12. Measured 2026-08-27, read-only, both sides:

    local  118,904 rows   MAX(updated_at) 2026-08-27 16:17:42
    cloud  113,993 rows   MAX(updated_at) 2026-08-12 05:12:11
    LOCAL-ONLY 4,911      CLOUD-ONLY 0      (identical on tracking_number AND order_number)

Ship weeks `2026-08-17` (2,362) and `2026-08-24` (2,545) are missing from cloud ENTIRELY.

Re-read local-side only on 2026-08-31 (the cloud side was UNREACHABLE, see WHERE THIS CAN RUN):
local is now **121,375 rows**, MAX(updated_at) `2026-08-31 20:28:17`, and a THIRD week has joined
the hole — `2026-08-31` (2,471). If cloud has not moved, the gap is 2,362 + 2,545 + 2,471 = 7,378
plus 4 stragglers = 7,382. 🔴 That arithmetic is an ESTIMATE off a 4-day-old cloud reading and is
never what the run acts on: `measure()` re-reads both sides every time, and every count printed or
gated on comes from that read.

🔴 The hole is INVISIBLE to every guard that exists today. `histdb.FLOORS['fulfillments']` is
75,000 and cloud holds 113,993, so the row floor passes a table missing two whole ship weeks.
Only a per-ship-week assertion catches this — which is why `--verify` below checks per-week
parity and not a row count.

A restarted cloud writer fixes this table FORWARD only; it cannot backfill 4,911 rows it never
saw. This repair fills the HOLE. Both are required, and this one lands FIRST — see ORDERING.

WHY LOCAL IS A SAFE SOURCE (measured, not assumed)
==================================================
Repairing a canonical table from an unvetted source spreads dirt instead of data. Local was
checked for the exact defect classes that blocked `shipments` (B2/B2b), and is clean:

  * duplicates      0 duplicate `(order_number, tracking_number)` groups; 118,904 rows /
                    118,904 distinct tracking — the UNIQUE index enforces it. (Cloud: also 0.)
                    Contrast `shipments`, where cloud carries 25,795 duplicate rows.
  * date formats    ONE format per column. `updated_at` len 19 on all 118,904 rows;
                    `fulfilled_at` len 25 (ISO+offset) on all; `ship_date` len 10 on 118,827
                    and NULL on 77. No `YYYYMMDD`/`YYYY-MM-DD` split — the B2 defect is ABSENT.
  * NULL keys       0 NULL/blank `order_number`, 0 NULL/blank `tracking_number`.
  * key format      bare digits both sides, 0 rows carrying '#'. No normalization needed.
  * the 4,911       0 NULL/blank in ANY column; `tracking_company` ∈ {OnTrac, FedEx, UPS};
                    all `dest_state` 2-char; all `dest_zip` match ^\\d{5}(-\\d{4})?$;
                    `fulfilled_at` 2026-08-17 .. 2026-08-25 — consistent with the two weeks.
  * grain           19 orders carry 2 fulfillments, matching the DATA_CANON declaration exactly.

⚠️ Local carries the DATA_CANON `known_defect` (writer keys on Shopify REST's numeric
`order_number`, so `#164878A` and `#164878` collide). This repair does NOT fix it and MUST NOT be
recorded as having fixed it. It copies the defect forward deliberately — the ETL mirrors, it does
not clean (DATA_CANON gotcha: "Fix defects at the writer").

THE KEY, AND THE SURVIVOR RULE
==============================
There is no survivor contest, because there is no contest: **a row is INSERTED only when its
natural key `(order_number, tracking_number)` is ABSENT from cloud.** A key present on both sides
is left exactly as cloud has it — this repair issues no `UPDATE`, no `REPLACE`, no
`ON DUPLICATE KEY UPDATE`, and `_assert_insert_only()` fails the run if the generated SQL ever
grows one. `id` is deliberately NOT this table's identity; it is copied verbatim like every other
column, because the cloud copy is a mirror (id-identical on all 113,993 shared rows on 8/27).

🔴 CLOUD-NEWER IS A FINDING, NOT A MERGE. Cloud-newer was 0 on 8/27, but that is a measurement
with a shelf life. If any shared key has a cloud `COALESCE(updated_at, fulfilled_at)` NEWER than
its local twin, something wrote cloud that local never saw, and this run REFUSES rather than
proceed — it does not overwrite (it could not: insert-only) and it does not shrug it off.
Cloud-ONLY rows, by contrast, are merely REPORTED: insert-only cannot harm them. That is the one
gate that legitimately relaxes when you stop doing a full refresh, and it is why it relaxed.

🔴 THE `#` ASYMMETRY IS CHECKED, NEVER PAPERED OVER. `order_number` is `'172607'` in this table
and `'#172607'` in `shopify_orders`-shaped sources; `lib/canon.sql_order_join()` exists because a
hand-written join across that seam matched 0 rows on every build for weeks and never errored.
Here both sides were bare digits on 8/27 (0 rows with '#'), so:
  * the anti-join compares NORMALIZED keys (leading '#' stripped) so a stray '#' row can never
    look "missing" and get inserted a second time under a different spelling; AND
  * `_key_form_census()` REFUSES the run if the two sides disagree on the dominant form, because
    inserting a bare key into a '#'-keyed table mints a second identity for the same fulfilment.
Normalization is used for MATCHING only. The values WRITTEN are byte-verbatim from the source —
the ETL mirrors, it does not clean (DATA_CANON: "fix defects at the writer").

TIMESTAMPS ARE COPIED, NEVER STAMPED — `STATUS_INGEST_RULES.md` rule 18a
=======================================================================
`fulfillments` has NO provenance column. `server/cloud_freshness.py` grades this table on
`MAX(COALESCE(updated_at, fulfilled_at))` at an 8-day bar, so that expression is the ONLY signal
distinguishing a live writer from a dead one. A backfill that stamped its own clock would
fresh-wash a possibly-dead writer for the whole window and silence a real alarm.
Therefore `updated_at` and `fulfilled_at` are ordinary copied columns in `COLS`, written with the
source's exact strings. There is no `NOW()`, no `CURRENT_TIMESTAMP` and no repair clock anywhere
in the write path; `_assert_insert_only()` rejects the SQL if one appears, and `--verify`
byte-compares both timestamps on a sample of inserted rows read back FROM DO. The alarm clears
only if the copied rows are genuinely recent, which is the truth.

`--update-stale` — THE INSERT-ONLY BLIND SPOT, AND WHY IT IS A SEPARATE MODE
===========================================================================
🔴 INSERT-ONLY CANNOT CORRECT A ROW THAT ALREADY EXISTS, AND `ship_date` MOVES. That is not a
theoretical gap — it was MEASURED on 2026-09-08, immediately after the insert path ran cleanly:
2,703 rows inserted, cloud rows == local rows == 124,078, local-only 0, cloud-only 0 — and
`--verify` STILL FAILED per-ship-week parity on five weeks. Cause, exactly: **11 rows whose cloud
`ship_date` is an EARLIER date than local.** They are RESHIPS — a box shipped 2026-07-20 and
reshipped 2026-07-27; local moved `ship_date`/`ship_week` (and dropped the stale `_SHIP_` tags),
and the cloud copy never learned, because an INSERT structurally cannot touch a key that is
already there.

    163680 163719 163720 163722 163723 163724 163735   (07-20 -> 07-27)
    169599 170532 170844                               (08-10 -> 08-17)
    168231                                             (08-03 -> 08-17)

🔴 THE 11 MATTER LESS THAN THE RECURRENCE. **Every future reship re-opens this.** An insert-only
sync against a MUTABLE source column diverges a little further every week, and the per-ship-week
parity check then fails forever — until somebody mutes it for crying wolf, at which point the one
guard that can see a missing ship week is gone. The mode exists to keep that check honest.

WHAT BOUNDS IT (measured, because the naive version is enormous)
  A blanket "sync every column that differs" is NOT what this does, and the numbers say why. Over
  the 124,078 shared rows on 2026-09-08: `updated_at` differs on **17,881** (a local bulk
  re-stamp), `tags` on 281, and `ship_date`/`ship_week` on exactly **11**. A column-blind update
  would rewrite 17,881 rows to repair 11.
  So SELECTION and WRITE are two different sets, deliberately:
    * `--stale-on` (default `ship_date,ship_week`) selects WHICH ROWS are candidates. It is the
      row bound, and it is the reason this run is 11 rows and not 17,881.
    * For a selected row, the WRITE set is every column that ACTUALLY DIFFERS and is not an
      identity column — so the 11 also get their stale `tags` and `updated_at` corrected. Leaving
      a row whose `ship_date` we just moved to 07-27 still carrying `_SHIP_2026-07-20` in `tags`
      would be a knowingly half-repaired row, which is worse than either extreme.
  `--max-rows` (default 500) REFUSES the whole run rather than truncating it. A truncating cap
  turns "this is bigger than you thought" into a silent partial write.

NON-NEGOTIABLES, EACH ONE A SEPARATE ASSERTION
  * `--update-stale` requires **BOTH** `--apply` and `--yes-write-production`, exactly as the
    insert path does. Dry by default: it measures, names every row, predicts every gate, writes
    a delta manifest, and touches nothing.
  * 🔴 A ZERO IS A CLAIM, AND THIS MODE'S ZERO HAS ITS OWN CONTROL. `_control_join()` proves the
    INSERT path's join is live; it does not vouch for `measure_stale()`, which builds its own dict
    on the normalized natural key. A key that stopped lining up would make every cloud row miss
    that dict, drop `shared_rows` to 0, and print "no shared row has drifted" — a clean run
    manufactured by a broken join. `_stale_join_alive()` therefore REFUSES when two non-empty
    mirrors share zero keys, and the measurement reports `local_rows`/`cloud_rows`/`shared_rows`
    so the zero can be falsified by a reader.
  * `_assert_insert_only()` is UNCHANGED and still guards the INSERT path. The update path does
    not borrow a hole in it — it has its own `_assert_update_only()`, which refuses anything that
    is not a single parameterized `UPDATE ... WHERE order_number=%s AND tracking_number=%s`, and
    refuses a SET list touching `id`, `order_number` or `tracking_number`.
  * 🔴 CLOUD-NEWER IS A REFUSAL, NEVER A MERGE — the same rule the insert path states, now with
    teeth, because here we CAN overwrite. If any candidate's cloud `COALESCE(updated_at,
    fulfilled_at)` is newer than local's, the run refuses and reports it: that is a cloud writer
    this repair does not know about, and overwriting it destroys someone else's work.
  * 🔴 NO REPAIR CLOCK, SAME AS THE INSERT PATH. Values are byte-verbatim from local;
    `_assert_update_only` bans `NOW()`/`CURRENT_TIMESTAMP`/`SYSDATE`/`UNIX_TIMESTAMP`; and
    `_server_clock_gate()` REFUSES if any destination column carries `ON UPDATE
    CURRENT_TIMESTAMP` in `information_schema` — a server-side clock would stamp the repair's own
    time on every UPDATE with no SQL to inspect, which is the one way rule 18a could be broken by
    a statement that reads perfectly clean. (Checked 2026-09-08: no cloud column has it. The gate
    stays, because the schema is not ours.)
  * Every UPDATE must affect EXACTLY ONE row. The batch's `rowcount` is checked BEFORE the commit
    and the batch is rolled back if it does not match — a WHERE that matched 0 or 2 rows means the
    key is not the key, and that must stop the run, not be discovered later.
  * The manifest records CLOUD's prior value for every column of every row BEFORE the write, so
    the undo is exact, and it is re-flushed after every batch.

WHERE THIS CAN RUN (read before planning the run)
=================================================
The write needs a sqlite read AND a MySQL socket in one process, and no host has both: this PC
holds `shipping.db` but is outside the DO trusted-sources list, and the cluster has the socket but
no `shipping.db`. So `--source-db` exists: take a snapshot with the canonical
`etl_history.py --snapshot-from-canonical --out snap.db` (a `Connection.backup()` off a
`connect_ro()` handle — no write lock), move `snap.db` inside the network, and run this there with
`--source-db snap.db`. Same deployment shape as the loader. With no `--source-db` the source is
the canonical DB through `connect_ro()`, which is the right thing anywhere the socket is reachable.

ROLLBACK
========
Written BEFORE the write and re-flushed AFTER EVERY BATCH, to
`_outputs/reports/repair_cloud_fulfillments_<ts>.json`:
  * the pre-write cloud fingerprint (rows, distinct keys, MAX(updated_at)/MAX(fulfilled_at),
    per-ship-week counts) — so "did this help" is answerable without this script;
  * `inserted_keys`: every `(order_number, tracking_number)` actually committed, in order;
  * `inserted_count`, the batch size, and per-batch commit timestamps;
  * `undo_sql`: the exact statement that removes precisely those keys and nothing else.
Undo is a DELETE of the recorded keys — there is no rollback table, because nothing was replaced.
An interrupted run leaves a manifest describing exactly the batches that committed, so the undo is
still exact.

USAGE
    python repair_cloud_fulfillments.py                    # dry run: gap + gates + full delta file
    python repair_cloud_fulfillments.py --verify           # post-repair proof only, read-only
    python repair_cloud_fulfillments.py --apply --yes-write-production
    python repair_cloud_fulfillments.py --apply --yes-write-production \
        --scratch-table fulfillments_repair_scratch      # REHEARSAL: writes to a copy, not live
    python repair_cloud_fulfillments.py --update-stale                   # DRY: name the stale rows
    python repair_cloud_fulfillments.py --update-stale --apply --yes-write-production
"""
from __future__ import annotations

import argparse
import collections
import datetime
import io
import json
import re
import sys
from pathlib import Path

_STDOUT_WRAPPED = False


def _force_utf8_stdout() -> None:
    """cp1252 is the Windows default and this script prints 🔴/✅ — without this it dies with a
    UnicodeEncodeError mid-report. 🔴 Called from `main()` ONLY, never at import: rebinding
    `sys.stdout` at import time breaks any harness that captures output (it took pytest's capture
    file out from under it, which is how this moved out of module scope).

    🔴 IDEMPOTENT. Wrapping twice orphaned the first wrapper, which closed the underlying buffer
    and made a SECOND `main()` in the same process die with `ValueError: I/O operation on closed
    file` — hit while proving the re-run is a no-op, i.e. the exact scenario a resume performs.
    """
    global _STDOUT_WRAPPED
    if _STDOUT_WRAPPED:
        return
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    _STDOUT_WRAPPED = True

WORKSPACE = Path(r"C:\Users\Work\Claude Projects")
AH = WORKSPACE / "AppyHour"
SR = WORKSPACE / "ShipRouting"          # etl_history lives here; see the docstring's WHY NOT block
SCRIPTS = WORKSPACE / "_outputs" / "scripts"   # for pull_cloud_replicas.database_url()
REPORTS = WORKSPACE / "_outputs" / "reports"
for _p in (str(AH), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TABLE = "fulfillments"
# The 16 columns, identical on both sides (verified 2026-08-27). Order is the sqlite DDL order.
COLS = ["id", "order_number", "order_id", "order_date", "tags", "tracking_number",
        "tracking_company", "tracking_url", "fulfilled_at", "customer_name", "dest_city",
        "dest_state", "dest_zip", "updated_at", "ship_date", "ship_week"]
# etl_history TABLES["fulfillments"] — the column its regression gate compares.
FRESHNESS_COL = "fulfilled_at"
# etl_history NATURAL_KEYS["fulfillments"]
NATURAL_KEY = ("order_number", "tracking_number")

# 🔴 IDENTITY. Never in an UPDATE's SET list, never a `--stale-on` column, and a difference in one
# of them on a shared key is a REFUSAL, not a repair: `order_number`/`tracking_number` ARE the key
# (rewriting one mints a second identity for the same fulfilment, the '#'-asymmetry failure in a
# different costume), and `id` is MySQL `auto_increment` — the cloud copy is id-identical to local
# on every shared row, so an `id` that disagrees means the mirror assumption is already broken and
# nothing downstream of that assumption should be trusted.
IMMUTABLE_COLS = ("id", *NATURAL_KEY)

# `--stale-on` default: the columns whose drift the insert path cannot see. Measured 2026-09-08 on
# 124,078 shared rows — ship_date/ship_week differ on 11, `updated_at` on 17,881, `tags` on 281.
# Selecting on the 11-row columns is what makes this bounded; see the docstring's WHAT BOUNDS IT.
DEFAULT_STALE_ON = ("ship_date", "ship_week")
# REFUSAL ceiling, never a truncation. 500 clears today's 11 by 45x and stops a `--stale-on
# updated_at` style mistake dead instead of half-writing it.
MAX_UPDATE_ROWS = 500

# The DESTINATION table on the MySQL side. `--scratch-table` repoints this at a seeded COPY so the
# whole run — measure, gates, insert, verify, re-run — can be rehearsed without touching live.
# 🔴 Only `main()` may reassign it, only from the CLI flag, and it is validated as an identifier.
DEST_TABLE = TABLE


# ---------------------------------------------------------------- connections (READ-ONLY both)

CANONICAL_DB_DIR = Path(r"C:\AppyHourData")
BATCH = 500          # rows per INSERT+COMMIT. Small enough that an interrupt loses <1s of work.


def local_con(source_db: str | None = None):
    """Read-only sqlite. 🔴 `connect_ro` only — Claude never write-connects shipping.db
    (three WAL corruptions).

    `source_db` is for the moved-snapshot deployment (see WHERE THIS CAN RUN). It is opened
    `mode=ro` too, and a PATH GUARD refuses any file NAMED `shipping.db` that lives outside
    `C:\\AppyHourData` — the canonical location. That guard is live because a second copy of the
    canonical DB under another root is how a stale replica gets read as authority.
    """
    if source_db is None:
        from appyhour_lib.db import connect_ro
        return connect_ro()

    import sqlite3
    p = Path(source_db).resolve()
    if p.name.lower() == "shipping.db" and p.parent != CANONICAL_DB_DIR:
        raise RuntimeError(
            f"🔴 REFUSED: {p} is named shipping.db but is not in {CANONICAL_DB_DIR}. The canonical "
            f"DB has exactly one home; a copy under another root must be renamed (a snapshot is "
            f"`snap.db`, not `shipping.db`) so it can never be mistaken for the authority.")
    if not p.exists():
        raise RuntimeError(f"--source-db not found: {p}")
    con = sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True, timeout=30.0)
    con.execute("PRAGMA busy_timeout=30000")
    return con


def cloud_con():
    """The MySQL handle. 🔴 `autocommit` is left OFF (pymysql's default) ON PURPOSE — this module
    commits explicitly, once per batch, and an autocommitting connection would make
    `rollback()` in the failure path a no-op and the batch boundary meaningless.

    Every statement is a SELECT except the INSERTs in `insert_missing()` (and the scratch-table
    setup in `prepare_scratch()`), which run only under `--apply --yes-write-production`.
    """
    import pymysql
    import pull_cloud_replicas as pcr
    url = pcr.database_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL missing: not in env and no %APPDATA%\\AppyHour\\database_url.txt. "
            "🔴 The file must be created from a REAL terminal — Claude/MSIX writes to %APPDATA% "
            "land in a sandbox shadow.")
    m = re.match(r"mysql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/([^?]+)", url)
    if not m:
        raise RuntimeError("DATABASE_URL unparseable")
    u, p, h, port, db = m.groups()
    return pymysql.connect(host=h, port=int(port), user=u, password=p, database=db,
                           ssl={"ssl": {}})


# ---------------------------------------------------------------- measurement

def _control_join(lc, cc) -> tuple[bool, str]:
    """🔴 A ZERO IS A CLAIM. Prove a KNOWN-PRESENT key survives the join before trusting any
    zero this script prints.

    Positive control: five old local rows must be FOUND in cloud by tracking_number.
    Negative control: the same order under a '#'-prefixed key must return 0 — proving the
    comparison is format-sensitive and a silent normalization is not papering over the join.
    (`#132940` vs `132940` has produced confident zeros in this operation three times.)
    """
    # 🔴 The OLDEST rows by id, not "rows before a hardcoded date". The literal here used to be
    # '2026-08-01', which silently stops selecting anything the moment the table's history moves
    # past it — a control that selects nothing reports "no control available" and, if anyone ever
    # softened that to a pass, would wave every zero through. Oldest-by-id cannot go empty.
    rows = lc.execute(
        f"SELECT tracking_number, order_number FROM {TABLE} "
        f"WHERE tracking_number IS NOT NULL ORDER BY id LIMIT 5").fetchall()
    if not rows:
        return False, "local table is empty — no control row available"
    cur = cc.cursor()
    for tr, _on in rows:
        cur.execute(f"SELECT COUNT(*) FROM {DEST_TABLE} WHERE tracking_number=%s", (tr,))
        if cur.fetchone()[0] != 1:
            return False, f"positive control FAILED: known-present tracking {tr!r} not in cloud"
    cur.execute(f"SELECT COUNT(*) FROM {DEST_TABLE} WHERE order_number=%s",
                ("#" + str(rows[0][1]),))
    if cur.fetchone()[0] != 0:
        return False, "negative control FAILED: a '#'-prefixed key matched — key formats differ"
    return True, f"{len(rows)}/5 positive + negative control passed"


def _norm(v) -> str:
    """Normalize an `order_number` FOR MATCHING ONLY — never for writing.

    🔴 Strips one leading '#'. `'#172607'` and `'172607'` are the same fulfilment; treating them
    as different keys is what made `RESHIP_RECOVERY`'s db lookup match 0 rows on every build for
    weeks without erroring. Matching normalizes; the INSERT still carries the source bytes.
    """
    s = "" if v is None else str(v).strip()
    return s[1:] if s.startswith("#") else s


def _key_form_census(lc, cc) -> dict:
    """Count '#'-prefixed vs bare `order_number` on BOTH sides.

    🔴 A silent normalization would hide a real format split. This makes the split VISIBLE, and
    `gates()` refuses when the two sides disagree on the dominant form — writing a bare key into a
    '#'-keyed table mints a second identity for the same fulfilment.
    """
    lh = lc.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE order_number LIKE '#%'").fetchone()[0]
    lt = lc.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    cur = cc.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {DEST_TABLE} WHERE order_number LIKE '#%'")
    ch = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {DEST_TABLE}")
    ct = cur.fetchone()[0]
    def form(hashed: int, total: int) -> str:
        if 0 < hashed < total:
            return "mixed"
        return "hash" if hashed and hashed == total else "bare"

    return {"local": {"hashed": lh, "total": lt, "form": form(lh, lt)},
            "cloud": {"hashed": ch, "total": ct, "form": form(ch, ct)}}


def column_contract_gate(cc) -> tuple[bool, str]:
    """PRE-FLIGHT, and it must run BEFORE `measure()`.

    🔴 Ordering bug this exists to fix: the column contract used to be evaluated only inside
    `gates()`, which runs on the OUTPUT of `measure()` — but `measure()` itself selects
    `ship_week`, `updated_at` and `fulfilled_at`, so a cloud table missing one of them blew up
    with `sqlite3/pymysql OperationalError: no such column` before the gate that was supposed to
    catch it ever evaluated. The check that reports a broken schema cannot depend on that schema.
    Found by running the suite, not by reading it.
    """
    missing, extra = _column_contract(cc)
    if missing:
        return False, (f"🔴 cloud `{DEST_TABLE}` is MISSING {missing} — an INSERT naming all "
                       f"{len(COLS)} COLS cannot run against it, and one naming fewer would drop "
                       f"data silently. Nothing measured, nothing written.")
    return True, f"all {len(COLS)} columns present" + (f" (cloud also has {extra})" if extra else "")


def _column_contract(cc) -> tuple[list[str], list[str]]:
    """(missing_in_cloud, extra_in_cloud) against COLS. A missing column = REFUSE, not a truncated
    INSERT."""
    cur = cc.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name=%s", (DEST_TABLE,))
    have = {r[0] for r in cur.fetchall()}
    return [c for c in COLS if c not in have], sorted(have - set(COLS))


def measure(lc, cc) -> dict:
    """Everything the gate and the report need, in one pass."""
    cur = cc.cursor()

    ltrk = {r[0] for r in lc.execute(f"SELECT tracking_number FROM {TABLE}")}
    cur.execute(f"SELECT tracking_number FROM {DEST_TABLE}")
    ctrk = {r[0] for r in cur.fetchall()}

    lord = {str(r[0]) for r in lc.execute(f"SELECT order_number FROM {TABLE}")}
    cur.execute(f"SELECT order_number FROM {DEST_TABLE}")
    cord = {str(r[0]) for r in cur.fetchall()}

    lcnt = lc.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    lupd = lc.execute(f"SELECT MAX(updated_at) FROM {TABLE}").fetchone()[0]
    lful = lc.execute(f"SELECT MAX({FRESHNESS_COL}) FROM {TABLE}").fetchone()[0]
    cur.execute(f"SELECT COUNT(*), MAX(updated_at), MAX({FRESHNESS_COL}) FROM {DEST_TABLE}")
    ccnt, cupd, cful = cur.fetchone()

    lwk = dict(lc.execute(
        f"SELECT COALESCE(ship_week,'(null)'), COUNT(*) FROM {TABLE} GROUP BY 1"))
    cur.execute(f"SELECT COALESCE(ship_week,'(null)'), COUNT(*) FROM {DEST_TABLE} GROUP BY 1")
    cwk = {r[0]: r[1] for r in cur.fetchall()}

    ldup = lc.execute(
        f"SELECT COUNT(*) FROM (SELECT {NATURAL_KEY[0]},{NATURAL_KEY[1]} FROM {TABLE} "
        f"GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM (SELECT {NATURAL_KEY[0]},{NATURAL_KEY[1]} FROM {DEST_TABLE} "
                f"GROUP BY 1,2 HAVING COUNT(*)>1) t")
    cdup = cur.fetchone()[0]

    only_local = ltrk - ctrk
    only_cloud = ctrk - ltrk
    lonly_orders = sorted(lord - cord)

    # ---- THE ANTI-JOIN THAT DRIVES THE WRITE, on the NATURAL KEY, normalized both sides.
    # freshness = COALESCE(updated_at, fulfilled_at) — the same expression `cloud_freshness.py`
    # grades this table on, so "cloud is newer" here means what it means to the alarm.
    lkey: dict[tuple[str, str], str] = {}
    for on, tn, ua, fa in lc.execute(
            f"SELECT order_number, tracking_number, updated_at, {FRESHNESS_COL} FROM {TABLE}"):
        lkey[(_norm(on), _norm(tn))] = str(ua or fa or "")
    cur.execute(
        f"SELECT order_number, tracking_number, updated_at, {FRESHNESS_COL} FROM {DEST_TABLE}")
    ckey: dict[tuple[str, str], str] = {}
    for on, tn, ua, fa in cur.fetchall():
        ckey[(_norm(on), _norm(tn))] = str(ua or fa or "")

    missing = sorted(set(lkey) - set(ckey))
    # 🔴 Cloud-newer is a FINDING, not a merge input. Compared as ISO strings after normalizing
    # the 'T'/space separator; a value that will not parse counts as a finding (fail closed).
    newer = []
    for k, cv in ckey.items():
        lv = lkey.get(k)
        if lv is None:
            continue
        hit, note = _cloud_is_newer(cv, lv)
        if hit:
            newer.append([list(k), cv, lv, note])

    # profile the local-only rows by ship_week (the shape of the hole)
    hole = collections.Counter()
    for tr, wk in lc.execute(f"SELECT tracking_number, ship_week FROM {TABLE}"):
        if tr in only_local:
            hole[wk or "(null)"] += 1

    return {
        "measured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "local": {"rows": lcnt, "max_updated_at": lupd, f"max_{FRESHNESS_COL}": lful,
                  "distinct_tracking": len(ltrk), "distinct_order": len(lord),
                  "natural_key_dup_groups": ldup, "by_ship_week": lwk},
        "cloud": {"rows": ccnt, "max_updated_at": cupd, f"max_{FRESHNESS_COL}": cful,
                  "distinct_tracking": len(ctrk), "distinct_order": len(cord),
                  "natural_key_dup_groups": cdup, "by_ship_week": cwk},
        "only_local_tracking": len(only_local),
        "only_cloud_tracking": len(only_cloud),
        "only_cloud_sample": sorted(only_cloud)[:20],
        "only_local_orders": lonly_orders,
        "hole_by_ship_week": dict(sorted(hole.items())),
        # the write plan
        "missing_keys": [list(k) for k in missing],
        "missing_key_count": len(missing),
        "cloud_newer_count": len(newer),
        "cloud_newer_sample": newer[:20],
        "key_form_census": _key_form_census(lc, cc),
        "column_contract_missing_in_cloud": _column_contract(cc)[0],
    }


def _iso(v):
    try:
        return datetime.datetime.fromisoformat(str(v).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _cloud_is_newer(cloud_val: str, local_val: str) -> tuple[bool, str]:
    """Is the cloud row newer than its local twin? FAIL CLOSED on anything ambiguous.

    Mixed awareness is real here: `updated_at` is naive (`2026-08-31 20:28:17`) and
    `fulfilled_at` carries an offset (`2026-08-31T05:54:03-04:00`), so a COALESCE can hand back
    one of each. Comparing those raises `TypeError` in Python, and a comparison that raises inside
    a safety gate is how a gate silently stops gating — so an unorderable pair is REPORTED as a
    finding rather than resolved by guesswork.
    """
    a, b = _iso(cloud_val), _iso(local_val)
    if a is None or b is None:
        return (str(cloud_val) > str(local_val), "unparseable-timestamp")
    if (a.tzinfo is None) != (b.tzinfo is None):
        return True, "mixed naive/aware timestamps — not orderable, reported as a finding"
    return (a > b, "")


def gates(m: dict) -> list[tuple[str, bool, str]]:
    """Predict every gate. `ok=False` on ANY row means --apply must refuse.

    🔴 One gate DELIBERATELY RELAXED when this became insert-only: "cloud-only rows == 0" was THE
    gate while the write was a full refresh, because a refresh replaces the live table and a
    cloud-only row would not be in the replacement. An INSERT cannot remove a row, so that count
    is now reported and not gated. It is recorded here, loudly, so nobody re-reads the relaxation
    as an oversight — and so anyone who ever switches this back to a refresh restores it first.
    """
    lc_, cc_ = m["local"], m["cloud"]
    out = []

    # 🔴 THE gate now. Insert-only cannot overwrite, so a newer cloud row is a FINDING: something
    # wrote cloud that local never saw, and that changes what this repair means.
    n = m["cloud_newer_count"]
    out.append(("cloud-newer rows == 0 (a newer cloud row is a finding, not a merge)", n == 0,
                f"cloud-newer={n}" + ("" if n == 0 else
                                      f"  sample={m['cloud_newer_sample'][:3]}")))

    cens = m["key_form_census"]
    same_form = cens["local"]["form"] == cens["cloud"]["form"]
    out.append(("order_number key FORM agrees across sides ('#' asymmetry)", same_form,
                f"local={cens['local']['form']}({cens['local']['hashed']} hashed) "
                f"cloud={cens['cloud']['form']}({cens['cloud']['hashed']} hashed)"))

    miss = m["column_contract_missing_in_cloud"]
    out.append((f"column contract: all {len(COLS)} COLS exist in cloud", not miss,
                "all present" if not miss else f"🔴 MISSING IN CLOUD: {miss}"))

    out.append(("natural-key metrics invariant (0 dup groups both sides)",
                lc_["natural_key_dup_groups"] == 0 and cc_["natural_key_dup_groups"] == 0,
                f"local={lc_['natural_key_dup_groups']} cloud={cc_['natural_key_dup_groups']}"))

    out.append(("there is actually a hole to fill", m["missing_key_count"] > 0,
                f"rows to insert={m['missing_key_count']}"))

    # REPORTED, NOT GATED — see the docstring above.
    n2 = m["only_cloud_tracking"]
    out.append((f"[report only] cloud-only rows = {n2} — insert-only cannot touch them", True,
                "none" if n2 == 0 else f"sample={m['only_cloud_sample'][:5]}"))
    return out


# ------------------------------------------------- STALE ROWS (the `--update-stale` measurement)

def _cell(v) -> str:
    """One comparable spelling for a column value, both sides.

    🔴 STRINGS, NOT PARSED VALUES — the same reason `verify()` byte-compares timestamps. pymysql
    hands back `text` columns as `str` and local sqlite stores them as `str`, so a difference here
    is a real difference in the stored bytes and not a parser's opinion. NULL and '' collapse to
    the same thing deliberately: this table's writer produces both for "absent", and treating them
    as different manufactures a diff on a row nobody changed.
    """
    return "" if v is None else str(v)


def _validate_stale_on(names: list[str]) -> list[str]:
    """`--stale-on` names real, non-identity columns — checked here, not at the SQL."""
    if not names:
        raise ValueError("--stale-on must name at least one column")
    for c in names:
        if c not in COLS:
            raise ValueError(f"--stale-on: {c!r} is not a column of `{TABLE}` ({COLS})")
        if c in IMMUTABLE_COLS:
            raise ValueError(
                f"🔴 REFUSED: --stale-on {c!r} is an identity column {IMMUTABLE_COLS}. A row is "
                f"not 'stale' on its own key — a differing key is a DIFFERENT row, and repairing "
                f"one by rewriting the other's key mints a second identity for one fulfilment.")
    return list(names)


def measure_stale(lc, cc, stale_on: list[str] | tuple[str, ...] = DEFAULT_STALE_ON) -> dict:
    """Find shared keys whose SOURCE VALUES CHANGED, and say exactly which columns and to what.

    🔴 This is a FULL-COLUMN read of both sides. It is not folded into `measure()` on purpose:
    `measure()` is the insert path's measurement, every existing gate and test is written against
    its shape, and widening it to serve a second mode is how one function ends up meaning two
    things. Two modes, two measurements, one `_norm()` for matching.

    Returns candidates ALREADY SPLIT into what may be repaired and what may not:
      * `stale`            — selected rows, each with the exact per-column cloud/local pair
      * `cloud_newer`      — 🔴 a candidate whose cloud copy is NEWER. A finding. Refuses the run.
      * `immutable_diffs`  — a candidate differing on `id`/the key. A finding. Refuses the run.
    """
    stale_on = _validate_stale_on(list(stale_on))
    oi, ti = COLS.index("order_number"), COLS.index("tracking_number")
    ui, fi = COLS.index("updated_at"), COLS.index(FRESHNESS_COL)

    lrows: dict[tuple[str, str], tuple] = {}
    sql = "SELECT " + ",".join(f'"{c}"' for c in COLS) + f" FROM {TABLE}"
    for row in lc.execute(sql):
        lrows[(_norm(row[oi]), _norm(row[ti]))] = tuple(row)

    cur = cc.cursor()
    cur.execute("SELECT " + ",".join(f"`{c}`" for c in COLS) + f" FROM {DEST_TABLE}")

    stale, cloud_newer, immutable_diffs = [], [], []
    shared = 0
    cloud_rows = 0
    diff_census: collections.Counter = collections.Counter()
    for crow in cur.fetchall():
        cloud_rows += 1
        k = (_norm(crow[oi]), _norm(crow[ti]))
        lrow = lrows.get(k)
        if lrow is None:
            continue                      # cloud-only: insert-only never touched it, nor does this
        shared += 1
        diffs = {c: {"cloud": _cell(crow[i]), "local": _cell(lrow[i])}
                 for i, c in enumerate(COLS) if _cell(crow[i]) != _cell(lrow[i])}
        for c in diffs:
            diff_census[c] += 1
        triggered = [c for c in stale_on if c in diffs]
        if not triggered:
            continue

        # 🔴 The WHERE key is CLOUD's OWN raw spelling, never local's and never the normalized
        # form. Normalization exists to MATCH; the statement that edits a cloud row must address it
        # by the bytes cloud actually stores, or the UPDATE silently matches nothing.
        entry = {
            "key": [_cell(crow[oi]), _cell(crow[ti])],
            "normalized_key": list(k),
            "triggered_by": triggered,
            "diffs": diffs,
            "cloud_freshness": _cell(crow[ui]) or _cell(crow[fi]),
            "local_freshness": _cell(lrow[ui]) or _cell(lrow[fi]),
        }

        bad_immutable = [c for c in IMMUTABLE_COLS if c in diffs]
        if bad_immutable:
            entry["immutable_cols"] = bad_immutable
            immutable_diffs.append(entry)
            continue

        hit, note = _cloud_is_newer(entry["cloud_freshness"], entry["local_freshness"])
        if hit:
            entry["cloud_newer_note"] = note
            cloud_newer.append(entry)
            continue

        # 🔴 Only columns that ACTUALLY DIFFER, and never an identity column. No blind whole-row
        # overwrite: a row is repaired by the smallest statement that fixes it.
        entry["update_cols"] = [c for c in COLS if c in diffs and c not in IMMUTABLE_COLS]
        stale.append(entry)

    stale.sort(key=lambda e: e["key"])
    return {
        "measured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "stale_on": stale_on,
        # 🔴 THE DENOMINATOR IS REPORTED SO THE ZERO CAN BE FALSIFIED. `stale_count == 0` is a
        # claim, and a broken join produces exactly that zero with no error — the '#'-asymmetry
        # class, which has minted confident zeros in this operation three times. These three
        # numbers are what `_stale_join_alive()` gates on before a zero is allowed to read clean.
        "local_rows": len(lrows),
        "cloud_rows": cloud_rows,
        "shared_rows": shared,
        "shared_diff_census": dict(diff_census),
        "stale": stale,
        "stale_count": len(stale),
        "cloud_newer": cloud_newer,
        "cloud_newer_count": len(cloud_newer),
        "immutable_diffs": immutable_diffs,
        "immutable_diff_count": len(immutable_diffs),
        "update_col_census": dict(collections.Counter(
            c for e in stale for c in e["update_cols"])),
    }


def _server_clock_gate(cc) -> tuple[bool, str]:
    """🔴 REFUSE if any destination column carries `ON UPDATE CURRENT_TIMESTAMP`.

    Rule 18a bans a repair clock, and `_assert_update_only()` can only police the SQL WE write. A
    server-side `ON UPDATE CURRENT_TIMESTAMP` stamps the repair's own time on every UPDATE with
    nothing in the statement to inspect — the one way a perfectly clean-reading statement still
    fresh-washes a possibly-dead writer for the whole 8-day window. `fulfillments` has no
    provenance column, so that stamp would be indistinguishable from a live writer.
    Checked 2026-09-08: no cloud column has it. The gate stays; the schema is not ours to assume.
    """
    cur = cc.cursor()
    cur.execute("SELECT column_name, extra FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name=%s", (DEST_TABLE,))
    hits = [r[0] for r in cur.fetchall() if "on update" in str(r[1] or "").lower()]
    if hits:
        return False, (f"🔴 {hits} carry ON UPDATE CURRENT_TIMESTAMP — an UPDATE would stamp the "
                       f"repair's clock server-side (rule 18a). Nothing written.")
    return True, "no destination column carries ON UPDATE CURRENT_TIMESTAMP"


def _stale_join_alive(sm: dict) -> tuple[bool, str]:
    """🔴 A ZERO IS A CLAIM — prove the stale join MATCHED before letting `stale_count == 0` read
    as "nothing has drifted".

    `_control_join()` proves the INSERT path's join is live. It cannot vouch for this one:
    `measure_stale()` builds its own full-column dict keyed on `(_norm(order_number),
    _norm(tracking_number))`, and if that key ever stops lining up — a '#' creeping onto one side,
    a renamed column, a `--source-db` snapshot of the wrong table — every cloud row misses the
    dict, `shared` stays 0, and the run prints "no shared row has drifted" and exits 0. That is a
    clean bill of health produced by a broken join, which is the failure this whole script's
    docstring is written against.

    The invariant that cannot be satisfied by accident: two non-empty tables that are supposed to
    MIRROR each other must overlap. So a zero-overlap read is a defect in the join, never a fact
    about the data, and it refuses. (Bar is deliberately just `> 0`, not a ratio: the insert path
    is what owns "how much of local is missing from cloud", and duplicating that judgement here
    would give this mode a second opinion on a number it does not own.)
    """
    lr, cr, sh = sm["local_rows"], sm["cloud_rows"], sm["shared_rows"]
    if lr == 0 or cr == 0:
        return False, (f"🔴 a side is EMPTY (local={lr} cloud={cr}) — nothing can be compared, and "
                       f"stale={sm['stale_count']} is an artifact of that, not a measurement.")
    if sh == 0:
        return False, (f"🔴 local={lr} cloud={cr} but shared keys=0 — two non-empty mirrors CANNOT "
                       f"have zero overlap. The natural-key join {NATURAL_KEY} is broken (key form "
                       f"split?), so stale={sm['stale_count']} is meaningless. Nothing written.")
    return True, (f"local={lr} cloud={cr} shared={sh} — join matched; "
                  f"columns differing on a shared row: {sm['shared_diff_census'] or '{} (none)'}")


def update_gates(sm: dict, max_rows: int, clock_ok: bool = True,
                 clock_msg: str = "") -> list[tuple[str, bool, str]]:
    """Predict every gate on the UPDATE path. `ok=False` anywhere means --apply must refuse.

    These are the update path's OWN assertions. None of them relaxes anything the insert path
    asserts, and `_assert_insert_only()` is untouched — an update-mode run still cannot make the
    INSERT statement mutate a row.
    """
    out: list[tuple[str, bool, str]] = []

    # 🔴 FIRST, because every gate below it is a statement ABOUT a number this one proves is real.
    # It is deliberately NOT exempted from `update_others_ok` in main(): a dead join must block the
    # "NOTHING TO DO — no shared row has drifted" exit, which is the only place a broken join could
    # still be reported as a clean run.
    alive_ok, alive_msg = _stale_join_alive(sm)
    out.append(("🔴 the stale join MATCHED (a zero is a claim; prove the denominator)",
                alive_ok, alive_msg))

    n = sm["cloud_newer_count"]
    out.append(("🔴 no candidate row is CLOUD-NEWER (a newer cloud row is a refusal, not a merge)",
                n == 0,
                f"cloud-newer candidates={n}" + ("" if n == 0 else
                                                 f"  sample={sm['cloud_newer'][:2]}")))

    n2 = sm["immutable_diff_count"]
    out.append((f"no candidate differs on an identity column {IMMUTABLE_COLS}", n2 == 0,
                f"identity-diff candidates={n2}" + ("" if n2 == 0 else
                                                    f"  sample={sm['immutable_diffs'][:2]}")))

    out.append(("no destination column carries ON UPDATE CURRENT_TIMESTAMP (rule 18a)",
                clock_ok, clock_msg or "not evaluated"))

    n3 = sm["stale_count"]
    out.append((f"stale rows <= --max-rows {max_rows} (REFUSES, never truncates)", n3 <= max_rows,
                f"stale={n3} ceiling={max_rows}"
                + ("" if n3 <= max_rows else
                   "  🔴 raise --max-rows deliberately, or narrow --stale-on. A cap that "
                   "truncated would half-write this.")))

    out.append(("every stale row has at least one non-identity column to write",
                all(e["update_cols"] for e in sm["stale"]),
                f"rows={n3} columns touched={sm['update_col_census']}"))

    out.append(("there is actually stale drift to repair", n3 > 0, f"stale rows={n3}"))
    return out


def stale_table(sm: dict, n: int = 50) -> str:
    """Name every row. A count is not a diff — the same rule the insert path's delta file follows."""
    if not sm["stale"]:
        return "  (none)"
    lines = []
    for e in sm["stale"][:n]:
        lines.append(f"  ~ order={e['key'][0]:<8} trk={e['key'][1]:<20} "
                     f"trigger={','.join(e['triggered_by'])}")
        for c in e["update_cols"]:
            d = e["diffs"][c]
            cv, lv = d["cloud"], d["local"]
            if len(cv) > 60 or len(lv) > 60:
                cv, lv = cv[:57] + "...", lv[:57] + "..."
            lines.append(f"        {c:<16} cloud={cv!r}  ->  local={lv!r}")
    if sm["stale_count"] > n:
        lines.append(f"  ... {sm['stale_count'] - n} more (all of them are in the delta file)")
    return "\n".join(lines)


def per_week_table(m: dict) -> str:
    lw, cw = m["local"]["by_ship_week"], m["cloud"]["by_ship_week"]
    lines = [f"{'ship_week':<16}{'local':>9}{'cloud':>9}{'delta':>9}"]
    for w in sorted(set(lw) | set(cw), key=str):
        a, b = lw.get(w, 0), cw.get(w, 0)
        if a != b or str(w) >= "2026-07-20":
            lines.append(f"{str(w):<16}{a:>9}{b:>9}{a - b:>9}"
                         + ("   🔴 MISSING FROM CLOUD" if b == 0 and a else ""))
    return "\n".join(lines)


def sample_diff(lc, m: dict, n: int = 10) -> str:
    """The exact rows that would be added — a count alone is not a diff.

    Driven by `missing_keys` (the natural-key anti-join that ACTUALLY drives the write), not by a
    separately-derived order list: a preview computed a different way than the write is a preview
    of something else.
    """
    keys = [tuple(k) for k in m["missing_keys"][:n]]
    if not keys:
        return "  (none)"
    out = []
    for on, tn in keys:
        r = lc.execute(
            f"SELECT order_number, ship_week, tracking_company, tracking_number, dest_state, "
            f"{FRESHNESS_COL} FROM {TABLE} WHERE order_number=? AND tracking_number=?",
            (on, tn)).fetchone()
        if r:
            out.append(f"  + order={str(r[0]):<8} wk={str(r[1]):<12} {str(r[2]):<7} "
                       f"{str(r[3]):<20} {str(r[4]):<3} {r[5]}")
    return "\n".join(out) or "  (none)"


# ---------------------------------------------------------------- verification

# Ship weeks the hole spans. 🔴 Derived, not hardcoded from a prior run: a week that joins the hole
# after this file was written (2026-08-31 did, four days after the original measurement) must be
# spot-checked too, or the proof silently narrows to the weeks somebody remembered.
def _repaired_weeks(lc, m: dict) -> list[str]:
    return [w for w in sorted(m["hole_by_ship_week"]) if w != "(null)"]


REPAIRED_WEEKS: list[str] = []


def _recent_keys(lc, n: int) -> list[tuple[str, str]]:
    """The n most recently fulfilled local keys — the rows a backfill most likely just wrote, and
    the ones whose timestamps a repair clock would have advanced."""
    return [(r[0], r[1]) for r in lc.execute(
        f"SELECT order_number, tracking_number FROM {TABLE} "
        f"ORDER BY id DESC LIMIT {int(n)}").fetchall()]


def verify(lc, cc, stale_on: list[str] | tuple[str, ...] | None = None) -> tuple[bool, str]:
    """🔴 PROOF IS THE TABLE READING DIFFERENTLY, not the script reporting success.

    Three independent checks — a row count alone cannot see a missing ship week (the 75,000
    histdb floor passes a table missing two of them).

    `stale_on` adds a FOURTH when `--update-stale` is in play: zero shared rows still drifted on
    those columns. It is opt-in because it costs a second full-column read of both sides, and
    because the insert path's proof must keep meaning exactly what it meant before this mode
    existed. 🔴 The per-ship-week check below is the one that FAILED on 2026-09-08 with the table
    at full parity on row count — it is what this mode has to turn green, and it is the proof.
    """
    m = measure(lc, cc)
    global REPAIRED_WEEKS
    REPAIRED_WEEKS = _repaired_weeks(lc, m) or REPAIRED_WEEKS
    lines, ok = [], True

    n = m["only_local_tracking"]
    good = n == 0
    ok &= good
    lines.append(f"  [{'PASS' if good else 'FAIL'}] cloud-only-missing (local-only rows) = {n}"
                 f"  (target 0)")

    # 🔴 CONTRACT 1, PROVEN BY READ-BACK: the copied timestamps must be BYTE-IDENTICAL to source.
    # Rule 18a rests on this — `fulfillments` has no provenance column, so an advanced
    # `updated_at`/`fulfilled_at` is indistinguishable from a live writer and greens the 8-day bar
    # off rows nobody delivered. Comparing the parsed instants would hide exactly the rewrite that
    # matters (a `NOW()` stamp reformatted to look like the source), so this compares STRINGS.
    cur0 = cc.cursor()
    checked = mismatched = 0
    for on, tn in _recent_keys(lc, 25):
        row = lc.execute(
            f"SELECT updated_at, {FRESHNESS_COL} FROM {TABLE} "
            f"WHERE order_number=? AND tracking_number=?", (on, tn)).fetchone()
        cur0.execute(f"SELECT updated_at, {FRESHNESS_COL} FROM {DEST_TABLE} "
                     f"WHERE order_number=%s AND tracking_number=%s", (on, tn))
        got = cur0.fetchone()
        if row is None or got is None:
            continue
        checked += 1
        if str(got[0]) != str(row[0]) or str(got[1]) != str(row[1]):
            mismatched += 1
            if mismatched <= 3:
                lines.append(f"       🔴 {on}/{tn}: cloud={got!r} source={row!r}")
    good = checked > 0 and mismatched == 0
    ok &= good
    lines.append(f"  [{'PASS' if good else 'FAIL'}] timestamps byte-identical to source on "
                 f"{checked} read-back rows, {mismatched} mismatched (rule 18a: no repair clock)")

    n2 = m["only_cloud_tracking"]
    lines.append(f"  [info] cloud-only rows = {n2} — insert-only never removes; a nonzero here is "
                 f"a cloud writer this repair did not touch, not damage")

    lw, cw = m["local"]["by_ship_week"], m["cloud"]["by_ship_week"]
    bad = [w for w in set(lw) | set(cw) if lw.get(w, 0) != cw.get(w, 0)]
    good = not bad
    ok &= good
    lines.append(f"  [{'PASS' if good else 'FAIL'}] per-ship-week parity across "
                 f"{len(set(lw) | set(cw))} weeks"
                 + ("" if good else f"  MISMATCHED: {sorted(map(str, bad))}"))

    if stale_on:
        sm = measure_stale(lc, cc, stale_on)
        good = sm["stale_count"] == 0 and sm["cloud_newer_count"] == 0
        ok &= good
        lines.append(f"  [{'PASS' if good else 'FAIL'}] no shared row drifted on {list(stale_on)} "
                     f"— stale={sm['stale_count']} cloud-newer={sm['cloud_newer_count']}"
                     + ("" if good else
                        f"  STILL STALE: {[e['key'] for e in sm['stale'][:10]]}"))

    # Spot-check named orders from the repaired weeks, read back FROM DO.
    cur = cc.cursor()
    for wk in REPAIRED_WEEKS:
        spot = lc.execute(
            f"SELECT order_number, tracking_number, tracking_company FROM {TABLE} "
            f"WHERE ship_week=? ORDER BY order_number LIMIT 3", (wk,)).fetchall()
        for on, tr, co in spot:
            cur.execute(f"SELECT tracking_number, tracking_company FROM {DEST_TABLE} "
                        f"WHERE order_number=%s", (on,))
            got = cur.fetchall()
            hit = any(g[0] == tr and g[1] == co for g in got)
            ok &= hit
            lines.append(f"  [{'PASS' if hit else 'FAIL'}] wk{wk} order {on} readable from DO "
                         f"with tracking {tr} / {co}")

    lines.append(f"\n  cloud now: {m['cloud']['rows']} rows, "
                 f"MAX(updated_at) {m['cloud']['max_updated_at']}")
    return ok, "\n".join(lines)


# ---------------------------------------------------------------- the write (INSERT-ONLY)

# 🔴 Matched as KEYWORDS, with quoted identifiers stripped first — never as raw substrings.
# A plain `"UPDATE" in sql` fires on the column `updated_at` and refuses the correct statement;
# it did, on the first run of the test suite. A guard that rejects the right SQL gets deleted by
# whoever hits it next, which turns a safety check into a liability.
FORBIDDEN_SQL: tuple[tuple[str, str], ...] = (
    ("UPDATE", r"\bUPDATE\b"),
    ("REPLACE", r"\bREPLACE\b"),
    ("ON DUPLICATE", r"\bON\s+DUPLICATE\b"),
    ("DELETE", r"\bDELETE\b"),
    ("TRUNCATE", r"\bTRUNCATE\b"),
    ("DROP", r"\bDROP\b"),
    ("IGNORE", r"\bIGNORE\b"),
    ("NOW()", r"\bNOW\s*\("),
    ("CURRENT_TIMESTAMP", r"\bCURRENT_TIMESTAMP\b"),
    ("SYSDATE", r"\bSYSDATE\b"),
    ("UNIX_TIMESTAMP", r"\bUNIX_TIMESTAMP\s*\("),
)
_QUOTED_IDENT = re.compile(r"`[^`]*`")


def build_insert_sql(table: str) -> str:
    """The ONE write statement. Plain INSERT, every column named, every value a parameter.

    🔴 No `ON DUPLICATE KEY UPDATE` (contract: never modify an existing cloud row) and no
    `INSERT IGNORE` (it would swallow a truncation or a key collision — exactly the errors that
    must stop the run). Idempotency comes from the anti-join, not from the verb.
    """
    cols = ",".join(f"`{c}`" for c in COLS)
    marks = ",".join(["%s"] * len(COLS))          # pymysql paramstyle, not string formatting
    return f"INSERT INTO `{table}` ({cols}) VALUES ({marks})"


def _assert_insert_only(sql: str) -> None:
    """Fail the run if the write statement ever grows a mutating or clock-stamping clause.

    🔴 This is the executable form of contracts 1 and 2. Rule 18a says a `NOW()` on copied rows
    fresh-washes a possibly-dead writer for 8 days and silences a real alarm; a future edit that
    "just adds an upsert" would break the never-UPDATE contract silently. Neither can survive here.
    """
    # Backticked identifiers are NAMES, not keywords — `updated_at` and `fulfilled_at` are the
    # very columns rule 18a requires us to carry, and they must not read as `UPDATE`.
    scan = _QUOTED_IDENT.sub("`x`", sql).upper()
    hits = [label for label, pat in FORBIDDEN_SQL if re.search(pat, scan)]
    if not scan.strip().startswith("INSERT INTO") or hits:
        raise RuntimeError(
            f"🔴 REFUSED: the write statement is not insert-only / not timestamp-preserving. "
            f"Offending token(s): {hits or ['does not start with INSERT INTO']}\nSQL: {sql}")


# 🔴 The UPDATE path's OWN forbidden list. It is NOT `FORBIDDEN_SQL` minus a line: `UPDATE` is the
# verb here, so this list has to be built from the other direction — everything that would make the
# statement do something other than set named columns on ONE row addressed by the natural key.
# `_assert_insert_only()` and `FORBIDDEN_SQL` are UNCHANGED and still guard the INSERT path; this
# does not borrow a hole in them.
FORBIDDEN_UPDATE_SQL: tuple[tuple[str, str], ...] = (
    ("INSERT", r"\bINSERT\b"),
    ("REPLACE", r"\bREPLACE\b"),
    ("DELETE", r"\bDELETE\b"),
    ("TRUNCATE", r"\bTRUNCATE\b"),
    ("DROP", r"\bDROP\b"),
    ("IGNORE", r"\bIGNORE\b"),          # would swallow the errors that must stop the run
    ("JOIN", r"\bJOIN\b"),              # multi-table UPDATE — blast radius we never want
    ("SELECT", r"\bSELECT\b"),
    ("LIMIT", r"\bLIMIT\b"),            # a LIMITed UPDATE is a silent partial write
    ("NOW()", r"\bNOW\s*\("),
    ("CURRENT_TIMESTAMP", r"\bCURRENT_TIMESTAMP\b"),
    ("SYSDATE", r"\bSYSDATE\b"),
    ("UNIX_TIMESTAMP", r"\bUNIX_TIMESTAMP\s*\("),
)
_SET_CLAUSE = re.compile(r"^`\w+`=%s(?:,`\w+`=%s)*$")


def build_update_sql(table: str, cols: list[str] | tuple[str, ...]) -> str:
    """The ONE update statement shape: named columns, parameterized values, natural-key WHERE.

    🔴 Every value is a `%s` parameter and the WHERE names BOTH key columns. There is no
    `ON DUPLICATE`, no LIMIT, no join, and no expression on the right of any `=` — a repair that
    could compute a value is a repair that could compute the wrong one. The values come byte-
    verbatim from local, exactly as the INSERT path's do.
    """
    if not cols:
        raise ValueError("build_update_sql: no columns to set")
    sets = ",".join(f"`{c}`=%s" for c in cols)
    return (f"UPDATE `{table}` SET {sets} "
            f"WHERE `{NATURAL_KEY[0]}`=%s AND `{NATURAL_KEY[1]}`=%s")


def _assert_update_only(sql: str, cols: list[str] | tuple[str, ...]) -> None:
    """Fail the run unless this is a single, parameterized, natural-key-scoped column update.

    🔴 The executable form of the update path's contract, and the counterpart to
    `_assert_insert_only()` — which it deliberately does NOT reuse, because that one refuses on the
    word UPDATE. Refuses: a SET list touching `id`/`order_number`/`tracking_number`; any literal on
    the right of a `=`; a missing or key-incomplete WHERE; a clock function; anything that inserts,
    deletes, joins, ignores or limits.
    """
    bad = [c for c in cols if c in IMMUTABLE_COLS]
    if bad:
        raise RuntimeError(
            f"🔴 REFUSED: the update statement is not identity-preserving — SET touches {bad}. "
            f"{IMMUTABLE_COLS} identify the row; rewriting one mints a second identity.")
    unknown = [c for c in cols if c not in COLS]
    if unknown:
        raise RuntimeError(f"🔴 REFUSED: SET names columns outside the contract: {unknown}")

    scan = _QUOTED_IDENT.sub("`x`", sql).upper()
    hits = [label for label, pat in FORBIDDEN_UPDATE_SQL if re.search(pat, scan)]
    if hits or not scan.startswith("UPDATE `X` SET "):
        raise RuntimeError(
            f"🔴 REFUSED: the update statement is not a plain, timestamp-preserving column update. "
            f"Offending token(s): {hits or ['does not start with UPDATE <table> SET']}\nSQL: {sql}")

    try:
        set_part, where_part = sql.split(" SET ", 1)[1].split(" WHERE ", 1)
    except (IndexError, ValueError):
        raise RuntimeError(f"🔴 REFUSED: update statement has no WHERE clause.\nSQL: {sql}") from None
    if not _SET_CLAUSE.fullmatch(set_part.strip()):
        raise RuntimeError(
            f"🔴 REFUSED: every SET value must be a bare `%s` parameter — no literals, no "
            f"expressions.\nSET: {set_part}")
    want = f"`{NATURAL_KEY[0]}`=%s AND `{NATURAL_KEY[1]}`=%s"
    if where_part.strip() != want:
        raise RuntimeError(
            f"🔴 REFUSED: the WHERE must be exactly the natural key ({want}); an update scoped by "
            f"anything else can hit rows nobody named.\nWHERE: {where_part}")
    if sql.count("%s") != len(cols) + 2:
        raise RuntimeError(f"🔴 REFUSED: parameter count {sql.count('%s')} != {len(cols) + 2}")


def _validate_identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name):
        raise ValueError(f"unsafe MySQL identifier: {name!r}")
    return name


def prepare_scratch(cc, name: str, reset: bool) -> None:
    """Seed a REHEARSAL copy of live `fulfillments` so the whole run can be exercised safely.

    `CREATE TABLE <scratch> LIKE fulfillments` + `INSERT ... SELECT *` gives a destination with the
    identical schema, indexes and contents, so the measured gap, the gates, the insert and the
    re-run-is-a-no-op proof all behave as they will against live. 🔴 Refuses to touch a table whose
    name is not clearly scratch, so a typo can never point the rehearsal at the real table.
    """
    _validate_identifier(name)
    if name == TABLE or "scratch" not in name.lower():
        raise RuntimeError(f"🔴 REFUSED: --scratch-table {name!r} must be a distinct name "
                           f"containing 'scratch'. Never rehearse onto the live table.")
    cur = cc.cursor()
    if reset:
        cur.execute(f"DROP TABLE IF EXISTS `{name}`")
    cur.execute(f"SHOW TABLES LIKE '{name}'")
    if not cur.fetchone():
        cur.execute(f"CREATE TABLE `{name}` LIKE `{TABLE}`")
        cur.execute(f"INSERT INTO `{name}` SELECT * FROM `{TABLE}`")
        cc.commit()
        cur.execute(f"SELECT COUNT(*) FROM `{name}`")
        print(f"scratch table `{name}` seeded from live: {cur.fetchone()[0]} rows")
    else:
        cur.execute(f"SELECT COUNT(*) FROM `{name}`")
        print(f"scratch table `{name}` reused as-is: {cur.fetchone()[0]} rows "
              f"(pass --scratch-reset to reseed)")


def source_rows_for(lc, missing: set[tuple[str, str]]):
    """Yield the source rows to insert, in `COLS` order, byte-verbatim.

    Selection is by NORMALIZED natural key against `missing`; the VALUES yielded are untouched
    source values — the ETL mirrors, it does not clean. A key already yielded in this pass is
    skipped, so a local duplicate can never become two cloud rows in one run.
    """
    oi, ti = COLS.index("order_number"), COLS.index("tracking_number")
    seen: set[tuple[str, str]] = set()
    sql = "SELECT " + ",".join(f'"{c}"' for c in COLS) + f" FROM {TABLE}"
    for row in lc.execute(sql):
        k = (_norm(row[oi]), _norm(row[ti]))
        if k in missing and k not in seen:
            seen.add(k)
            yield k, tuple(row)


def insert_missing(lc, cc, m: dict, manifest_path: Path, batch: int = BATCH) -> dict:
    """Copy the missing rows, BATCHED, COMMITTING EACH BATCH.

    🔴 Commit-per-batch is contract 4 and it is not a performance choice: buffering everything and
    writing once is what left `delivery_status` dark for six days — an interrupted run banked
    nothing and the next run started from zero. Here an interrupt keeps every committed batch, the
    manifest is re-flushed after each commit so the undo stays exact, and a resume is just a
    re-run: `measure()` re-reads cloud, the anti-join skips what landed, and the remainder goes.
    """
    missing = {tuple(k) for k in m["missing_keys"]}
    sql = build_insert_sql(DEST_TABLE)
    _assert_insert_only(sql)

    cur = cc.cursor()
    man = json.loads(manifest_path.read_text(encoding="utf-8"))
    man.update({"destination_table": DEST_TABLE, "insert_sql": sql, "batch_size": batch,
                "inserted_keys": [], "inserted_count": 0, "batches": [], "status": "running"})

    def flush(buf, keys):
        if not buf:
            return
        try:
            cur.executemany(sql, buf)
            cc.commit()
        except BaseException as exc:
            # 🔴 BaseException, not Exception. A real interrupt — Ctrl+C, a killed process, a
            # SystemExit — raises KeyboardInterrupt/SystemExit, which `except Exception` does NOT
            # catch. Proven against live MySQL on 2026-08-31: the committed batches were banked
            # correctly, but the manifest was left saying `status: running`, so the durable record
            # of a DEAD run claimed it was still going. The banked keys were never at risk; the
            # ability to TELL was. An interrupt is the case this manifest exists for.
            cc.rollback()
            man["status"] = ("INTERRUPTED" if isinstance(exc, KeyboardInterrupt | SystemExit)
                             else "FAILED")
            man["failed_batch_keys"] = [list(k) for k in keys]
            man["error"] = f"{type(exc).__name__}: {exc}"
            _flush_manifest(manifest_path, man)
            raise
        man["inserted_keys"].extend([list(k) for k in keys])
        man["inserted_count"] += len(buf)
        man["batches"].append({"rows": len(buf), "committed_at":
                               datetime.datetime.now().isoformat(timespec="seconds")})
        _flush_manifest(manifest_path, man)
        print(f"  committed batch of {len(buf):>4}  total {man['inserted_count']:>6}"
              f" / {len(missing)}")

    buf, keys = [], []
    for k, row in source_rows_for(lc, missing):
        buf.append(row)
        keys.append(k)
        if len(buf) >= batch:
            flush(buf, keys)
            buf, keys = [], []
    flush(buf, keys)

    man["status"] = "complete"
    man["undo_sql"] = (
        f"DELETE FROM `{DEST_TABLE}` WHERE (order_number, tracking_number) IN "
        f"( ... the {man['inserted_count']} pairs in inserted_keys ... )  "
        f"-- exact keys are in this manifest; nothing else was written, and no existing row was "
        f"modified, so this DELETE is a complete undo.")
    _flush_manifest(manifest_path, man)
    return man


def _flush_manifest(path: Path, man: dict) -> None:
    path.write_text(json.dumps(man, indent=2, default=str), encoding="utf-8")


# ------------------------------------------------------- the write (UPDATE, `--update-stale`)

def update_stale_rows(cc, sm: dict, manifest_path: Path, batch: int = BATCH) -> dict:
    """Correct the drifted columns of the selected rows. BATCHED, COMMITTING EACH BATCH.

    🔴 Statements are GROUPED BY THE EXACT SET OF COLUMNS THAT DIFFER, so each `executemany` sends
    one statement shape and every row in it is repaired by the smallest statement that fixes it.
    The alternative — one statement listing every column and letting the unchanged ones "write
    themselves back" — is a whole-row overwrite wearing a diff's clothing: it re-writes columns
    nobody compared, and any cloud value in them dies without ever appearing in a diff.

    🔴 ROWCOUNT IS CHECKED BEFORE THE COMMIT. Each UPDATE must affect exactly one row. If the batch
    does not, the natural key is not behaving like a key against this table and the run stops with
    the batch rolled back — discovering that after the commit means discovering it too late.

    Commit-per-batch and the re-flushed manifest are the insert path's contract 4, unchanged: an
    interrupt keeps every committed batch, and the resume is just a re-run, because `measure_stale`
    re-reads cloud and a repaired row is no longer stale.
    """
    cur = cc.cursor()
    man = json.loads(manifest_path.read_text(encoding="utf-8"))
    man.update({"mode": "update-stale", "destination_table": DEST_TABLE,
                "stale_on": sm["stale_on"], "batch_size": batch,
                "updated_keys": [], "updated_count": 0, "batches": [], "statements": [],
                "status": "running"})

    groups: dict[tuple[str, ...], list[dict]] = collections.defaultdict(list)
    for e in sm["stale"]:
        groups[tuple(e["update_cols"])].append(e)

    def flush(sql, cols, buf, entries):
        if not buf:
            return
        try:
            cur.executemany(sql, buf)
            affected = cur.rowcount
            if affected != len(buf):
                raise RuntimeError(
                    f"🔴 REFUSED MID-RUN: {len(buf)} statements affected {affected} rows. Each "
                    f"UPDATE must hit exactly one row on ({NATURAL_KEY[0]}, {NATURAL_KEY[1]}); "
                    f"this batch is rolled back and nothing further is written.")
            cc.commit()
        except BaseException as exc:
            # BaseException for the same reason the insert path uses it: Ctrl+C / a kill raises
            # KeyboardInterrupt|SystemExit, which `except Exception` misses, and the manifest of a
            # dead run must not claim it is still running.
            cc.rollback()
            man["status"] = ("INTERRUPTED" if isinstance(exc, KeyboardInterrupt | SystemExit)
                             else "FAILED")
            man["failed_batch_keys"] = [e["key"] for e in entries]
            man["error"] = f"{type(exc).__name__}: {exc}"
            _flush_manifest(manifest_path, man)
            raise
        man["updated_keys"].extend([e["key"] for e in entries])
        man["updated_count"] += len(buf)
        man["batches"].append({"rows": len(buf), "cols": list(cols), "committed_at":
                               datetime.datetime.now().isoformat(timespec="seconds")})
        _flush_manifest(manifest_path, man)
        print(f"  committed batch of {len(buf):>4}  cols={list(cols)}  "
              f"total {man['updated_count']:>5} / {sm['stale_count']}")

    for cols, entries in sorted(groups.items()):
        sql = build_update_sql(DEST_TABLE, cols)
        _assert_update_only(sql, cols)          # 🔴 per statement shape, not once for the run
        man["statements"].append({"sql": sql, "rows": len(entries)})
        _flush_manifest(manifest_path, man)
        buf, chunk = [], []
        for e in entries:
            buf.append(tuple([e["diffs"][c]["local"] for c in cols] + e["key"]))
            chunk.append(e)
            if len(buf) >= batch:
                flush(sql, cols, buf, chunk)
                buf, chunk = [], []
        flush(sql, cols, buf, chunk)

    man["status"] = "complete"
    man["undo_note"] = (
        "Undo is row-by-row: for each entry in `pre_write_stale.stale`, re-apply the `cloud` value "
        "of each column in `update_cols` under the same natural-key WHERE. Nothing was inserted or "
        "removed and no column outside `update_cols` was written, so that restores cloud exactly.")
    _flush_manifest(manifest_path, man)
    return man


def write_stale_delta_file(sm: dict, path: Path) -> int:
    """Every stale row, every column, cloud value AND local value — written BEFORE the write.

    Same rule as the insert path's delta file: a dry run that prints a count is not a dry run. This
    file is also the undo record, because it holds cloud's prior value for every column touched.
    """
    path.write_text(json.dumps(
        {"measured_at": sm["measured_at"], "stale_on": sm["stale_on"],
         "stale_count": sm["stale_count"], "update_col_census": sm["update_col_census"],
         "shared_diff_census": sm["shared_diff_census"],
         "cloud_newer": sm["cloud_newer"], "immutable_diffs": sm["immutable_diffs"],
         "stale": sm["stale"]},
        indent=2, default=str), encoding="utf-8")
    return sm["stale_count"]


def write_delta_file(lc, m: dict, path: Path) -> int:
    """Write EVERY row the run would insert. A dry run that prints a count is not a dry run."""
    missing = {tuple(k) for k in m["missing_keys"]}
    rows = [dict(zip(COLS, row, strict=True)) for _k, row in source_rows_for(lc, missing)]
    path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    return len(rows)


# ---------------------------------------------------------------- main

def main() -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="perform the repair (also needs --yes-write-production)")
    ap.add_argument("--yes-write-production", action="store_true",
                    help="second gate: acknowledges this mutates the DO MySQL primary")
    ap.add_argument("--verify", action="store_true",
                    help="read-only post-repair proof; makes no changes")
    ap.add_argument("--json-out", default=None, help="write the full measurement to this path")
    ap.add_argument("--source-db", default=None,
                    help="sqlite SNAPSHOT to read instead of the canonical DB — for running this "
                         "from inside the network, where shipping.db does not exist "
                         "(see WHERE THIS CAN RUN). Opened mode=ro; path-guarded.")
    ap.add_argument("--scratch-table", default=None,
                    help="REHEARSAL: seed a copy of the live table under this name and write "
                         "THERE instead of `fulfillments`. Name must contain 'scratch'.")
    ap.add_argument("--scratch-reset", action="store_true",
                    help="with --scratch-table: DROP and reseed the scratch copy first")
    ap.add_argument("--batch", type=int, default=BATCH,
                    help=f"rows per INSERT+COMMIT (default {BATCH})")
    ap.add_argument("--update-stale", action="store_true",
                    help="ALSO correct rows that already exist in cloud but whose SOURCE VALUES "
                         "CHANGED (reships move ship_date; insert-only cannot see it). Dry unless "
                         "BOTH --apply and --yes-write-production are given, same as the insert "
                         "path. Off by default.")
    ap.add_argument("--stale-on", default=",".join(DEFAULT_STALE_ON),
                    help=f"comma-separated columns whose drift SELECTS a row as stale "
                         f"(default {','.join(DEFAULT_STALE_ON)}). This is the row bound — see the "
                         f"docstring's WHAT BOUNDS IT. Identity columns are refused.")
    ap.add_argument("--max-rows", type=int, default=MAX_UPDATE_ROWS,
                    help=f"ceiling on stale rows (default {MAX_UPDATE_ROWS}). 🔴 REFUSES the run "
                         f"when exceeded — it never truncates to fit.")
    args = ap.parse_args()

    if args.apply and not args.yes_write_production:
        print("REFUSED: --apply also requires --yes-write-production. Nothing written.")
        return 2
    if args.batch < 1:
        print("REFUSED: --batch must be >= 1.")
        return 2
    if args.max_rows < 1:
        print("REFUSED: --max-rows must be >= 1.")
        return 2
    try:
        stale_on = _validate_stale_on([c.strip() for c in args.stale_on.split(",") if c.strip()])
    except ValueError as exc:
        print(f"REFUSED: {exc}")
        return 2

    lc, cc = local_con(args.source_db), cloud_con()

    global DEST_TABLE
    if args.scratch_table:
        prepare_scratch(cc, args.scratch_table, args.scratch_reset)
        DEST_TABLE = args.scratch_table
        print(f"🧪 REHEARSAL MODE — destination is `{DEST_TABLE}`, live `{TABLE}` is untouched.")
    try:
        # 🔴 SCHEMA FIRST — measure() reads columns this proves exist. Order matters.
        col_ok, col_msg = column_contract_gate(cc)
        print(f"column contract: {col_msg}")
        if not col_ok:
            return 2

        ctrl_ok, ctrl_msg = _control_join(lc, cc)
        print(f"control join: {ctrl_msg}")
        if not ctrl_ok:
            print("🔴 REFUSED: the join control failed, so every zero below is untrustworthy.")
            return 2

        if args.verify:
            ok, report = verify(lc, cc, stale_on if args.update_stale else None)
            print("\n=== VERIFICATION (read-only) ===")
            print(report)
            print("\nVERDICT:", "REPAIRED ✅" if ok else "🔴 NOT REPAIRED")
            return 0 if ok else 1

        m = measure(lc, cc)
        print(f"\n=== GAP, measured {m['measured_at']} ===")
        print(f"  local  {m['local']['rows']:>7} rows   MAX(updated_at) {m['local']['max_updated_at']}")
        print(f"  cloud  {m['cloud']['rows']:>7} rows   MAX(updated_at) {m['cloud']['max_updated_at']}")
        print(f"  LOCAL-ONLY {m['only_local_tracking']}    CLOUD-ONLY {m['only_cloud_tracking']}")
        print(f"\n=== PER SHIP WEEK ===\n{per_week_table(m)}")
        print(f"\n=== THE HOLE, by ship_week ===\n  {m['hole_by_ship_week']}")
        print(f"\n=== SAMPLE OF ROWS THAT WOULD BE ADDED (first 10 of "
              f"{m['missing_key_count']}) ===\n{sample_diff(lc, m)}")

        REPORTS.mkdir(parents=True, exist_ok=True)
        stamp = f"{datetime.datetime.now():%Y%m%d%H%M%S}"
        delta = REPORTS / f"repair_cloud_fulfillments_delta_{stamp}.json"
        n_delta = write_delta_file(lc, m, delta)
        print(f"\nEXACT rows this would insert: {n_delta} -> {delta}")
        if n_delta != m["missing_key_count"]:
            print(f"🔴 REFUSED: the delta file holds {n_delta} rows but the anti-join said "
                  f"{m['missing_key_count']}. The plan and the count disagree; nothing written.")
            return 2

        print("\n=== GATES (insert path) ===")
        g = gates(m)
        for name, ok_, detail in g:
            print(f"  [{'PASS' if ok_ else 'REFUSE'}] {name}\n           {detail}")

        # ---------------------------------------------------------- the UPDATE path (opt-in)
        # 🔴 Measured SEPARATELY and gated SEPARATELY. Nothing here relaxes an insert-path gate.
        sm = update_g = stale_delta = None
        if args.update_stale:
            clock_ok, clock_msg = _server_clock_gate(cc)
            sm = measure_stale(lc, cc, stale_on)
            stale_delta = REPORTS / f"repair_cloud_fulfillments_stale_delta_{stamp}.json"
            n_stale = write_stale_delta_file(sm, stale_delta)
            print("\n=== STALE ROWS — cloud row EXISTS but its source values CHANGED ===")
            print(f"  selected on {sm['stale_on']} across {sm['shared_rows']} shared rows")
            print(f"  columns differing anywhere on a shared row: {sm['shared_diff_census']}")
            print(f"  stale rows={sm['stale_count']}  cloud-newer={sm['cloud_newer_count']}  "
                  f"identity-diff={sm['immutable_diff_count']}")
            print(f"  columns this would write: {sm['update_col_census']}")
            print(stale_table(sm))
            print(f"\nEXACT rows this would UPDATE: {n_stale} -> {stale_delta}")

            print("\n=== GATES (update path) ===")
            update_g = update_gates(sm, args.max_rows, clock_ok, clock_msg)
            for name, ok_, detail in update_g:
                print(f"  [{'PASS' if ok_ else 'REFUSE'}] {name}\n           {detail}")

        # 🔴 IDEMPOTENCY IS A SUCCESS, NOT A REFUSAL. A second run — a resume, a re-check, a
        # scheduled sweep — finds the hole already filled and must say so and exit 0. Reporting
        # "a gate did not pass" for the intended end state trains the operator to ignore the
        # refusal message, which is how a real refusal gets waved through. Every OTHER gate must
        # still hold: a cloud-newer row or a key-form split is a finding even with nothing to do.
        others_ok = all(ok_ for name, ok_, _ in g if "hole to fill" not in name)
        update_others_ok = (update_g is None or
                            all(ok_ for name, ok_, _ in update_g if "stale drift" not in name))
        nothing_stale = sm is None or sm["stale_count"] == 0
        # 🔴 "there is actually a hole to fill" is a REFUSAL for a run that only has UPDATEs to do,
        # and an exit 1 on a fully-green update plan is exactly the cry-wolf that gets a refusal
        # message ignored — the same reasoning that made idempotency an exit 0 below. So the
        # verdict is: every OTHER gate holds, on both paths, and there is work of SOME kind.
        # With `--update-stale` off, `sm is None` and this reduces to the old `all_ok` exactly.
        work_exists = m["missing_key_count"] > 0 or not nothing_stale
        verdict_ok = others_ok and update_others_ok and work_exists
        if m["missing_key_count"] == 0 and others_ok and nothing_stale and update_others_ok:
            print("\n✅ NOTHING TO DO — cloud already holds every local row on the natural key"
                  + (" and no shared row has drifted." if args.update_stale else ".")
                  + " Re-running this is a no-op by design.")
            return 0

        if args.json_out:
            Path(args.json_out).write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
            print(f"\nmeasurement written to {args.json_out}")

        if not (args.apply and args.yes_write_production):
            print(f"\nDRY RUN — nothing written. {m['missing_key_count']} row(s) would be "
                  f"INSERTED into `{DEST_TABLE}`; "
                  + (f"{sm['stale_count']} existing row(s) would be UPDATED on "
                     f"{sm['update_col_census']}; " if sm else
                     "0 existing rows would be modified or removed; ")
                  + "0 rows would be removed.")
            print("To apply: --apply --yes-write-production   (🔴 needs Kurt's explicit go; the "
                  "cloud write is Routing Coordinator's surface)")
            return 0 if verdict_ok else 1

        if not (others_ok and work_exists):
            print("\n🔴 REFUSED: an insert-path gate above did not pass. Nothing written.")
            return 2
        if args.update_stale and not update_others_ok:
            print("\n🔴 REFUSED: an update-path gate above did not pass. Nothing written — "
                  "including the inserts, because a refusal is about this run, not this phase.")
            return 2

        manifest = REPORTS / f"repair_cloud_fulfillments_{stamp}.json"
        manifest.write_text(json.dumps(
            {"pre_write_measurement": m, "gates": [[a, b, c] for a, b, c in g],
             "delta_file": str(delta), "destination_table": DEST_TABLE},
            indent=2, default=str), encoding="utf-8")
        print(f"\nrollback manifest (pre-write state) -> {manifest}")

        if m["missing_key_count"]:
            print(f"\n=== INSERTING {m['missing_key_count']} row(s) into `{DEST_TABLE}`, "
                  f"{args.batch}/batch, committing each batch ===")
            man = insert_missing(lc, cc, m, manifest, batch=args.batch)
            print(f"inserted {man['inserted_count']} row(s) in {len(man['batches'])} batch(es); "
                  f"manifest -> {manifest}")

        if args.update_stale:
            # 🔴 RE-MEASURE and RE-GATE after the inserts. The plan printed above was measured
            # before anything was written; acting on a stale plan is the class this whole file
            # exists to stop. Re-reading also re-earns the cloud-newer refusal at write time.
            clock_ok, clock_msg = _server_clock_gate(cc)
            sm = measure_stale(lc, cc, stale_on)
            update_g = update_gates(sm, args.max_rows, clock_ok, clock_msg)
            if not all(ok_ for name, ok_, _ in update_g if "stale drift" not in name):
                print("\n🔴 REFUSED: an update-path gate failed on the RE-MEASURE at write time. "
                      "No UPDATE was issued.")
                for name, ok_, detail in update_g:
                    print(f"  [{'PASS' if ok_ else 'REFUSE'}] {name}\n           {detail}")
                return 2
            if sm["stale_count"]:
                umanifest = REPORTS / f"repair_cloud_fulfillments_update_{stamp}.json"
                umanifest.write_text(json.dumps(
                    {"pre_write_stale": sm, "gates": [[a, b, c] for a, b, c in update_g],
                     "stale_delta_file": str(stale_delta), "destination_table": DEST_TABLE},
                    indent=2, default=str), encoding="utf-8")
                print(f"\nupdate manifest (pre-write cloud values) -> {umanifest}")
                print(f"\n=== UPDATING {sm['stale_count']} existing row(s) in `{DEST_TABLE}`, "
                      f"{args.batch}/batch, committing each batch ===")
                uman = update_stale_rows(cc, sm, umanifest, batch=args.batch)
                print(f"updated {uman['updated_count']} row(s) in {len(uman['batches'])} "
                      f"batch(es); manifest -> {umanifest}")
            else:
                print("\n(no stale rows remained at write time — nothing to update)")

        # PROOF comes from re-reading DO, on fresh connections.
        cc.close()
        cc = cloud_con()
        ok, report = verify(lc, cc, stale_on if args.update_stale else None)
        print("\n=== VERIFICATION (re-read from DO) ===")
        print(report)
        print("\nVERDICT:", "REPAIRED ✅" if ok else "🔴 NOT REPAIRED — consider rollback")
        return 0 if ok else 1
    finally:
        try:
            lc.close()
        finally:
            cc.close()


if __name__ == "__main__":
    raise SystemExit(main())
