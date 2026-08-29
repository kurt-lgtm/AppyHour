"""Compact history store: the same answers as history.py, at ~1/9th the size.

  python -m order_checks.history_compact seed     # one-time, from the 700 MB db
  python -m order_checks.history_compact stats
  python -m order_checks.history_compact verify   # parity vs the fat db

The fat store kept a 38-char order gid plus the SKU string on all 1.87M line rows, which
is where 700 MB went -- not the data. Dictionary-encoding the two id columns and
collapsing `items` to the question the checks actually ask ("has this customer ever had
this SKU, when last") gives 75 MB with identical answers.

  cust  49,681   shopify id <-> recharge id <-> email, each string stored ONCE
  sku      616
  ord   176,955  one row per order
  oi  1,857,... (ord, sku, qty) -- int-keyed; the strings live in the dims
  ev    267,908  login + contents-touching ONLY; the other ~2M Recharge events
                 (updated/sent/payment) are read by no check
  verb / src     dims for the event columns the customize gate reads

🔴 GOTCHAS, each of which broke a build of this file:

  * `orders.customer_id` is a GID (`gid://shopify/Customer/123`); `rc_customers.shopify_id`
    is the BARE numeric tail. Joining them raw yields 65 rows out of 268k and looks like a
    working query. Always join on the stripped tail.
  * Timestamps are epoch SECONDS, never a truncated day. `previous_orders` filters
    `< before`, so a day-resolution column silently includes the order being checked
    against itself, and every customer looks like they already own their own contents.
  * `oi` is per-order and append-only. Seeding is idempotent (it drops the file); an
    incremental writer must DELETE an order's rows before re-inserting it, or a re-ingested
    order double-counts. Re-ingestion is not hypothetical -- our own swaps edit line items
    after the order was first pulled.
  * 164 login events belong to Recharge customers with no Shopify order here, and drop.
    Safe ONLY because a customer with no order cannot be a swap candidate -- it is not a
    licence to treat an unmapped customer as clear. Absence of evidence is not clearance.
  * `ev` keeps `kind` and `src`, which an earlier cut of this schema threw away. The
    customize gate is not a boolean on "touched": it fires on kind='human', and an
    api-origin change counts only when a human event lands within HUMAN_WINDOW of it.
    Collapsing the row to a login/touch bit weakens that gate silently -- api 47,151 /
    automated 53,150 / human 48,984 all look identical once the column is gone.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

FAT = r"C:\Users\Work\Claude Projects\_outputs\cache\order_history.db"
DB = r"C:\Users\Work\Claude Projects\_outputs\cache\order_history_compact.db"
# A 2,500-customer slice of FAT, kept so `verify` survives the fat store being deleted.
# 🔴 It carries ALL events for those customers, not just logins/touches -- the customize
# gate's nearby-human lookup reads events that are neither.
FIXTURE = r"C:\Users\Work\Claude Projects\_outputs\cache\order_history_fixture.db"

STRIP = "replace(customer_id,'gid://shopify/Customer/','')"

SCHEMA = """
CREATE TABLE cust(id INTEGER PRIMARY KEY, shop TEXT UNIQUE, rc TEXT, email TEXT);
CREATE TABLE sku (id INTEGER PRIMARY KEY, code TEXT UNIQUE);
CREATE TABLE ord (id INTEGER PRIMARY KEY, cust INTEGER, name TEXT, ts INTEGER, tags TEXT);
-- 🔴 PER-ORDER, not a (cust, sku) rollup. A rollup cannot answer "what was in the
-- previous N orders": it keeps only each SKU's LAST receipt, so a SKU received again
-- later drops out of every earlier order's list. That is check7's whole question, and
-- the rollup silently cut its repeat count from 502 to 5.
CREATE TABLE oi  (ord INTEGER, sku INTEGER, qty INTEGER);
-- login = 1 for a login event; touch = 1 for a contents-touching one.
-- kind: 0 human, 1 api, 2 automated -- the customize gate reads it, do not drop it.
-- nearhuman: a human-kind event on this customer within HUMAN_WINDOW of this row.
-- 🔴 Computed at seed against ALL rc_events, not just the rows kept here -- the human
-- event that qualifies an api-origin change is often neither a login nor a contents
-- touch, so it is absent from `ev` and the lookup cannot be done at read time.
CREATE TABLE ev  (cust INTEGER, ts INTEGER, login INTEGER, touch INTEGER,
                  kind INTEGER, verb INTEGER, src INTEGER, nearhuman INTEGER);
CREATE TABLE verb(id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE src (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
"""
INDEXES = """
CREATE INDEX ix_cust_rc ON cust(rc);
CREATE INDEX ix_ord_c   ON ord(cust, ts);
CREATE INDEX ix_ord_n   ON ord(name);
CREATE INDEX ix_oi_o    ON oi(ord);
CREATE INDEX ix_oi_s    ON oi(sku);
CREATE INDEX ix_ev_log  ON ev(cust, login, ts);
CREATE INDEX ix_ev_tch  ON ev(cust, touch, ts);
CREATE INDEX ix_ev_hum  ON ev(cust, kind, ts);
"""
KIND = {"human": 0, "api": 1, "automated": 2}
HUMAN_WINDOW = 300          # seconds; Dan uses 5 minutes. Mirrors recharge_gate.


def connect(db=DB):
    return sqlite3.connect(db)


def seed(fat=FAT, db=DB):
    """One-time build from the fat store. Idempotent -- drops and rebuilds."""
    t0 = time.time()
    if os.path.exists(db):
        os.remove(db)
    d = sqlite3.connect(db)
    d.executescript("PRAGMA journal_mode=off; PRAGMA synchronous=off;" + SCHEMA)
    d.execute("ATTACH ? AS src", (fat,))

    # 🔴 UNION, not just orders. A customer whose orders all postdate the last bulk pull
    # has no row in src.orders; seeding from orders alone leaves _cid() returning None,
    # and a None read silently becomes "no login" = CLEAR. That is the unmapped-is-not-
    # clear trap, and it wrongly cleared #177001 (a real login on 2026-08-24).
    d.execute(f"""INSERT INTO cust(shop)
        SELECT DISTINCT {STRIP} FROM src.orders WHERE customer_id LIKE 'gid://%'
        UNION
        SELECT shopify_id FROM src.rc_customers
        WHERE shopify_id IS NOT NULL AND shopify_id <> ''""")
    d.execute("INSERT INTO sku(code) SELECT DISTINCT sku FROM src.items "
              "WHERE sku IS NOT NULL AND sku <> ''")
    d.execute("""UPDATE cust SET
        rc    = (SELECT r.recharge_id FROM src.rc_customers r WHERE r.shopify_id = cust.shop),
        email = (SELECT r.email       FROM src.rc_customers r WHERE r.shopify_id = cust.shop)""")
    d.execute("CREATE INDEX ix_seed_rc ON cust(rc)")

    d.execute(f"""INSERT INTO ord(id, cust, name, ts, tags)
        SELECT o.rowid, c.id, o.name, CAST(strftime('%s', o.created_at) AS INTEGER),
               nullif(o.tags, '')
        FROM src.orders o LEFT JOIN cust c ON c.shop = {STRIP}""")
    d.execute("""INSERT INTO oi(ord, sku, qty)
        SELECT o.rowid, k.id, SUM(i.qty)
        FROM src.items i
        JOIN src.orders o USING(order_gid)
        JOIN sku k ON k.code = i.sku
        WHERE i.qty > 0
        GROUP BY 1, 2""")
    d.execute("INSERT INTO verb(name) SELECT DISTINCT verb FROM src.rc_events "
              "WHERE verb = 'login' OR touches = 1")
    d.execute("INSERT INTO src(name) SELECT DISTINCT source FROM src.rc_events "
              "WHERE (verb = 'login' OR touches = 1) AND source IS NOT NULL")
    # 🔴 recharge_id <-> the events' customer_id; shopify_id <-> the stripped order gid
    d.execute(f"""INSERT INTO ev
        SELECT c.id, CAST(strftime('%s', e.created_at) AS INTEGER),
               (e.verb = 'login'), COALESCE(e.touches, 0),
               CASE e.kind {' '.join(f"WHEN '{k}' THEN {v}" for k, v in KIND.items())} END,
               v.id, s.id, 0
        FROM src.rc_events e
        JOIN src.rc_customers r ON r.recharge_id = e.customer_id
        JOIN cust c ON c.shop = r.shopify_id
        LEFT JOIN verb v ON v.name = e.verb
        LEFT JOIN src  s ON s.name = e.source
        WHERE e.verb = 'login' OR e.touches = 1""")

    # 🔴 against ALL events of kind 'human', not the filtered `ev` subset
    d.execute("""CREATE TEMP TABLE hum AS
        SELECT c.id AS cust, CAST(strftime('%s', e.created_at) AS INTEGER) AS ts
        FROM src.rc_events e
        JOIN src.rc_customers r ON r.recharge_id = e.customer_id
        JOIN cust c ON c.shop = r.shopify_id
        WHERE e.kind = 'human'""")
    d.execute("CREATE INDEX ix_hum ON hum(cust, ts)")
    d.execute("""UPDATE ev SET nearhuman = 1 WHERE kind = ? AND EXISTS (
        SELECT 1 FROM hum h WHERE h.cust = ev.cust AND ABS(h.ts - ev.ts) <= ?)""",
              (KIND["api"], HUMAN_WINDOW))
    d.execute("DROP TABLE hum")

    d.execute("DROP INDEX ix_seed_rc")
    d.executescript(INDEXES)
    for k, v in (("seeded_at", time.strftime("%Y-%m-%dT%H:%M:%S")), ("source", fat),
                 ("ev_floor", str(d.execute("SELECT MIN(ts) FROM ev").fetchone()[0])),
                 ("max_order_ts", str(d.execute("SELECT MAX(ts) FROM ord").fetchone()[0]))):
        d.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))
    d.commit()
    d.execute("VACUUM")
    d.close()
    print(f"seeded in {time.time() - t0:.0f}s   "
          f"{os.path.getsize(fat) / 1e6:.0f} MB -> {os.path.getsize(db) / 1e6:.0f} MB")
    stats(db)


# ---------------------------------------------------------------- read API
# Same names and signatures as history.py, so callers swap the import and nothing else.

def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""


def _cid(con, customer_gid):
    """Shopify gid (or bare id) -> internal cust id."""
    if not customer_gid:
        return None
    r = con.execute("SELECT id FROM cust WHERE shop = ?",
                    (str(customer_gid).rsplit("/", 1)[-1],)).fetchone()
    return r[0] if r else None


def _ts(con, iso):
    return con.execute("SELECT CAST(strftime('%s', ?) AS INTEGER)", (iso,)).fetchone()[0]


def ever_received(con, customer_id, skus):
    """-> set of SKUs this customer has EVER received."""
    if not skus:
        return set()
    cid = _cid(con, customer_id)
    if cid is None:
        return set()
    marks = ",".join("?" * len(skus))
    return {r[0] for r in con.execute(
        f"""SELECT DISTINCT k.code FROM oi
            JOIN ord o ON o.id = oi.ord
            JOIN sku k ON k.id = oi.sku
            WHERE o.cust = ? AND k.code IN ({marks})""", [cid, *skus])}


def previous_orders(con, customer_id, before_iso, limit=4):
    """-> [(name, created_at_iso, [skus])] for the N most recent PRIOR orders.

    The SKU list is THAT ORDER's own contents, matching history.previous_orders exactly.
    check7 flattens the N lists into one set to detect repeats, so an aggregate that
    loses per-order composition is not a substitute -- see the `oi` note in the schema.
    """
    cid = _cid(con, customer_id)
    if cid is None:
        return []
    rows = con.execute(
        """SELECT id, name, ts FROM ord WHERE cust = ? AND ts < ?
           ORDER BY ts DESC LIMIT ?""", (cid, _ts(con, before_iso), limit)).fetchall()
    out = []
    for oid, name, ots in rows:
        skus = [r[0] for r in con.execute(
            "SELECT k.code FROM oi JOIN sku k ON k.id = oi.sku WHERE oi.ord = ?", (oid,))]
        out.append((name, _iso(ots), skus))
    return out


def sku_first_seen(con):
    """SKU -> first-ever appearance, ISO. Ranking input for 'prefer the newest item'."""
    return {s: _iso(t) for s, t in con.execute(
        """SELECT k.code, MIN(o.ts) FROM oi
           JOIN sku k ON k.id = oi.sku
           JOIN ord o ON o.id = oi.ord
           GROUP BY k.code""")}


def recharge_id(con, shopify_customer_gid):
    """Shopify gid -> Recharge customer id. 🔴 Never join these by email."""
    cid = _cid(con, shopify_customer_gid)
    if cid is None:
        return None
    r = con.execute("SELECT rc FROM cust WHERE id = ?", (cid,)).fetchone()
    return r[0] if r and r[0] else None


def known(con, shopify_customer_gid):
    """Is this customer in the store at all? An unknown customer is UNKNOWN, not clear."""
    return _cid(con, shopify_customer_gid) is not None


def ev_floor(con):
    """Earliest indexed event -- the date before which a login is INVISIBLE."""
    r = con.execute("SELECT v FROM meta WHERE k = 'ev_floor'").fetchone()
    t = int(r[0]) if r and r[0] else con.execute("SELECT MIN(ts) FROM ev").fetchone()[0]
    return _iso(t)


def logged_in_since(con, shopify_customer_gid, since_iso):
    """-> login timestamp ISO, or '' if none since `since_iso`.

    🔴 '' means "no login on record", NOT "did not log in". A customer absent from `cust`
    also returns '' -- callers must check `known()` and treat an unknown customer as
    UNKNOWN, never as cleared for a swap.
    """
    cid = _cid(con, shopify_customer_gid)
    if cid is None:
        return ""
    r = con.execute("""SELECT ts FROM ev WHERE cust = ? AND login = 1 AND ts >= ?
                       ORDER BY ts LIMIT 1""", (cid, _ts(con, since_iso))).fetchone()
    return _iso(r[0]) if r else ""


def customized(con, shopify_customer_gid, since_iso=None):
    """True when a HUMAN changed this customer's contents. Port of recharge_gate.customized.

    🔴 Not a boolean on "touched". A contents change of api origin counts ONLY when a
    human-sourced event on the same customer lands within HUMAN_WINDOW seconds of it --
    a portal edit runs as an api call, so api-origin alone is neither proof nor clearance.

    Takes a SHOPIFY gid, where recharge_gate.customized takes a Recharge id; the map is
    already inside this store, so callers no longer thread recharge_id() through.
    """
    cid = _cid(con, shopify_customer_gid)
    if cid is None:
        return False, ""
    sql = """SELECT e.ts, v.name, s.name, e.kind, e.nearhuman FROM ev e
             LEFT JOIN verb v ON v.id = e.verb LEFT JOIN src s ON s.id = e.src
             WHERE e.cust = ? AND e.touch = 1"""
    args = [cid]
    if since_iso:
        sql += " AND e.ts >= ?"
        args.append(_ts(con, since_iso))
    hits = con.execute(sql + " ORDER BY e.ts", args).fetchall()
    for ts, verb, source, kind, _nh in hits:
        if kind == KIND["human"]:
            return True, f"{_iso(ts)} {verb} [{source}]"
    for ts, verb, source, kind, nearhuman in hits:
        if kind == KIND["api"] and nearhuman:
            return True, f"{_iso(ts)} {verb} [{source}] ~ human within {HUMAN_WINDOW}s"
    return False, ""


def stats(db=DB):
    c = sqlite3.connect(db)

    def q(s):
        return c.execute(s).fetchone()[0]

    seeded = q("SELECT v FROM meta WHERE k = 'seeded_at'")
    print(f"  {os.path.getsize(db) / 1e6:.0f} MB   seeded {seeded}")
    for t in ("cust", "sku", "ord", "oi", "ev"):
        print(f"  {t:<8}{q('SELECT COUNT(*) FROM ' + t):>12,}")
    print(f"  {'w/ rc':<8}{q('SELECT COUNT(*) FROM cust WHERE rc IS NOT NULL'):>12,}")
    print(f"  {'logins':<8}{q('SELECT COUNT(*) FROM ev WHERE login = 1'):>12,}"
          f"   touches {q('SELECT COUNT(*) FROM ev WHERE touch = 1'):,}"
          f"  (human {q('SELECT COUNT(*) FROM ev WHERE touch = 1 AND kind = 0'):,})")
    print(f"  event floor {ev_floor(c)}")
    c.close()


def verify(fat=None, db=DB, n=400):
    """Parity against the fat store on the questions the checks actually ask.

    Falls back to FIXTURE when the fat store is gone. The fixture is a strict SUBSET, so
    a pass proves the QUERIES agree -- not that the compact store is complete. Row counts
    in `stats` are what prove completeness.
    """
    import random
    if fat is None:
        fat = FAT if os.path.exists(FAT) else FIXTURE
    print(f"  reference: {os.path.basename(fat)}")

    from . import history as fatmod
    from .customer_map import recharge_id as fat_rc
    from .recharge_gate import customized as fat_customized
    from .recharge_gate import norm_ts

    def fat_login(con, gid, since):
        """The pre-compact query, inlined -- login_gate now reads the compact store and
        cannot serve as its own reference."""
        rid = fat_rc(con, gid)
        if not rid:
            return ""
        r = con.execute(
            """SELECT created_at FROM rc_events WHERE customer_id = ? AND verb = 'login'
               AND created_at >= ? ORDER BY created_at LIMIT 1""",
            (str(rid), norm_ts(since))).fetchone()
        return r[0] if r else ""

    o, d = sqlite3.connect(fat), sqlite3.connect(db)
    gids = [r[0] for r in o.execute(
        "SELECT DISTINCT customer_id FROM orders WHERE customer_id LIKE 'gid://%' LIMIT 20000")]
    random.seed(7)
    samp = random.sample(gids, min(n, len(gids)))
    bad_ever = bad_prev = bad_cust = bad_rc = bad_login = 0
    for g in samp:
        # the two guardrail halves -- the ones that actually gate a live swap
        rid = fat_rc(o, g)
        if rid != recharge_id(d, g):
            bad_rc += 1
        if bool(fat_customized(o, rid)[0]) != bool(customized(d, g)[0]):
            bad_cust += 1
        floor = fatmod.previous_orders(o, g, "2099-01-01", 1)
        since = floor[0][1] if floor else "2026-05-01T00:00:00Z"
        if bool(fat_login(o, g, since)) != bool(logged_in_since(d, g, since)):
            bad_login += 1
        a = {r[0] for r in o.execute(
            """SELECT DISTINCT i.sku FROM items i JOIN orders o USING(order_gid)
               WHERE o.customer_id = ? AND i.qty > 0""", (g,))} - {"", None}
        if a != ever_received(d, g, [*a, "__nope__"]):
            bad_ever += 1
        ref = o.execute("SELECT MAX(created_at) FROM orders WHERE customer_id = ?",
                        (g,)).fetchone()[0]
        if ref:
            # 🔴 compare the SKU LISTS, not just the order names. An earlier cut of this
            # store returned the right orders with the wrong contents, and a name-only
            # comparison passed it 400/400 while check7's repeat count fell 502 -> 5.
            # SETS, not lists: the fat store keeps one row per LINE ITEM, so a SKU
            # ordered on two lines of the same order appears twice; `oi` groups it and
            # sums qty. Every consumer builds a set from this, so the multiplicity is
            # not information. The names and the SKU sets must both match exactly.
            x = [(r[0], set(r[2])) for r in fatmod.previous_orders(o, g, ref, 3)]
            y = [(r[0], set(r[2])) for r in previous_orders(d, g, ref, 3)]
            if x != y:
                bad_prev += 1
    print(f"  recharge_id     {len(samp) - bad_rc}/{len(samp)} identical")
    print(f"  customized      {len(samp) - bad_cust}/{len(samp)} identical   <- guardrail half 1")
    print(f"  logged_in_since {len(samp) - bad_login}/{len(samp)} identical   <- guardrail half 2")
    print(f"  ever_received   {len(samp) - bad_ever}/{len(samp)} identical")
    print(f"  previous_orders {len(samp) - bad_prev}/{len(samp)} identical")
    uni_a = {r[0] for r in o.execute("SELECT DISTINCT sku FROM items WHERE qty > 0")} - {"", None}
    uni_b = set(sku_first_seen(d))
    # 🔴 Against the FIXTURE this is a SUBSET test, not equality -- the fixture holds
    # 2,500 customers and so fewer SKUs. Requiring equality there fails a correct store.
    missing = uni_a - uni_b
    print(f"  sku universe    {len(uni_b)} in store, {len(uni_a)} in reference"
          + (f"   🔴 missing {sorted(missing)[:5]}" if missing else "   (reference is a subset)"
             if len(uni_a) < len(uni_b) else ""))
    ok = not (bad_ever or bad_prev or bad_cust or bad_rc or bad_login) and not missing
    print("  PARITY OK" if ok else "  🔴 PARITY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    sys.exit({"seed": seed, "stats": stats, "verify": verify}[cmd]() or 0)
