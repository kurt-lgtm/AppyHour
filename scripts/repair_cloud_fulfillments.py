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


def verify(lc, cc) -> tuple[bool, str]:
    """🔴 PROOF IS THE TABLE READING DIFFERENTLY, not the script reporting success.

    Three independent checks — a row count alone cannot see a missing ship week (the 75,000
    histdb floor passes a table missing two of them).
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
        except Exception as exc:
            cc.rollback()
            man["status"] = "FAILED"
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
    args = ap.parse_args()

    if args.apply and not args.yes_write_production:
        print("REFUSED: --apply also requires --yes-write-production. Nothing written.")
        return 2
    if args.batch < 1:
        print("REFUSED: --batch must be >= 1.")
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
            ok, report = verify(lc, cc)
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

        print("\n=== GATES ===")
        g = gates(m)
        for name, ok_, detail in g:
            print(f"  [{'PASS' if ok_ else 'REFUSE'}] {name}\n           {detail}")
        all_ok = all(x[1] for x in g)

        # 🔴 IDEMPOTENCY IS A SUCCESS, NOT A REFUSAL. A second run — a resume, a re-check, a
        # scheduled sweep — finds the hole already filled and must say so and exit 0. Reporting
        # "a gate did not pass" for the intended end state trains the operator to ignore the
        # refusal message, which is how a real refusal gets waved through. Every OTHER gate must
        # still hold: a cloud-newer row or a key-form split is a finding even with nothing to do.
        others_ok = all(ok_ for name, ok_, _ in g if "hole to fill" not in name)
        if m["missing_key_count"] == 0 and others_ok:
            print("\n✅ NOTHING TO DO — cloud already holds every local row on the natural key. "
                  "Re-running this is a no-op by design.")
            return 0

        if args.json_out:
            Path(args.json_out).write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
            print(f"\nmeasurement written to {args.json_out}")

        if not (args.apply and args.yes_write_production):
            print(f"\nDRY RUN — nothing written. {m['missing_key_count']} row(s) would be "
                  f"INSERTED into `{DEST_TABLE}`; 0 existing rows would be modified or removed.")
            print("To apply: --apply --yes-write-production   (🔴 needs Kurt's explicit go; the "
                  "cloud write is Routing Coordinator's surface)")
            return 0 if all_ok else 1

        if not all_ok:
            print("\n🔴 REFUSED: a gate above did not pass. Nothing written.")
            return 2

        manifest = REPORTS / f"repair_cloud_fulfillments_{stamp}.json"
        manifest.write_text(json.dumps(
            {"pre_write_measurement": m, "gates": [[a, b, c] for a, b, c in g],
             "delta_file": str(delta), "destination_table": DEST_TABLE},
            indent=2, default=str), encoding="utf-8")
        print(f"\nrollback manifest (pre-write state) -> {manifest}")

        print(f"\n=== INSERTING {m['missing_key_count']} row(s) into `{DEST_TABLE}`, "
              f"{args.batch}/batch, committing each batch ===")
        man = insert_missing(lc, cc, m, manifest, batch=args.batch)
        print(f"inserted {man['inserted_count']} row(s) in {len(man['batches'])} batch(es); "
              f"manifest -> {manifest}")

        # PROOF comes from re-reading DO, on fresh connections.
        cc.close()
        cc = cloud_con()
        ok, report = verify(lc, cc)
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
