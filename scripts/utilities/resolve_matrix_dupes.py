"""Canonical resolver for a Matrixify add-sheet — dupes, removed-line switch, slot-keyed swaps.

Constraints SSOT: scripts/RESOLVE_DUPES_RULES.md — READ IT BEFORE CHANGING THIS FILE. Rule
numbers below refer to that doc. Process doc: ~/.claude/skills/matrixify-import-dupe-check/SKILL.md.

What it does (per order, negatives-first):
  rule 1  in-sheet dupe  -> keep the 1st row, SWAP each later occurrence. NEVER drop a row.
  rule 2  live dupe      -> child_sku already on the order (currentQuantity>0) -> swap.
  rule 3  removed line   -> child_sku on the order with quantity>0 & currentQuantity==0 -> switch
                            it in-sheet (a MERGE re-add silently fails). EVERY SKU, not just FCROSE.
  rule 5  slot-keyed pools: the substitute matches the row's parent_sku SLOT (CEX-CR=cracker,
                            EX-EA/CEX-EA=accompaniment, EX-EM/CEX-EM=meat, EX-EC/CEX-EC=cheese,
                            PR-CJAM=config'd cheese+jam pair). Never inferred from a title.
  rule 6  stock-aware     : Shopify LIVE inventoryQuantity of the $0 variant; a pool SKU whose
                            (live - drawn this run) <= --min-avail (30) is skipped; round-robin
                            across the pool; --floor/--cap/--zero force-swap source adds out;
                            every SKU pushed below --min-avail is REPORTED before resolving.
  rule 7  barred list     : -FS-, MT-HOTP, AC-RMC, MT-IBRES, MT-BSS, CH-MAFT, AC-RBOL, AC-BLUCAR,
                            mini jams as generic subs, all brie. MT-CCCS first-priority meat sub.
  rule 8  post-import guard: > --imported-threshold of add-rows read as live dupes -> abort.
  rule 9  swap log        : <out>_SWAPLOG.txt every run; an existing --out is never overwritten
                            (versioned -2, -3, ...).
  rule 11 swap storm      : an order with >= --storm substitutions is flagged PROBABLE-ALREADY-
                            PROCESSED and left untouched instead of mass-swapped.

🔴 READ-ONLY vs Shopify. Writes ONE corrected CSV + one swap log. Never edits an order.
`--fresh` re-pulls the mutable box/removed state (rule 2 — a stale cache hid dupes).

Usage:
  python resolve_matrix_dupes.py --src "<add.csv>" --out "<resolved.csv>" [--fresh]
      [--floor CH-OGK=30 --floor CH-SHADOW=10] [--cap CH-BRIE=24] [--zero CH-CCC]
      [--min-avail 30] [--storm 5] [--imported-threshold 0.5] [--cjam-pair CH-SOT=AC-MFJ]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


def _utf8_stdout() -> None:
    """Windows cp1252 kills any non-ASCII print mid-run (rule 9). CLI-only: rewrapping at import
    closes pytest's capture file and takes the whole test session down with it."""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


AH = Path(__file__).resolve().parents[2]
for _p in (AH, AH / "InventoryReorder" / "fulfillment_web", AH / "scripts" / "utilities"):
    sys.path.insert(0, str(_p))
from order_state_cache import OrderStateCache  # noqa: E402

SHOP = "504ac4"
SETTINGS = AH / "InventoryReorder" / "dist" / "inventory_reorder_settings.json"
_TOKEN: list[str] = []


def gql(query: str, variables: dict) -> dict:
    """Shopify Admin GraphQL — lazy import so tests can monkeypatch this symbol. READ-ONLY queries only."""
    from shopify_swap import _gql  # noqa: PLC0415

    if not _TOKEN:
        with open(SETTINGS, encoding="utf-8") as f:
            _TOKEN.append(json.load(f)["shopify_access_token"])
    return _gql(SHOP, _TOKEN[0], query, variables)


norm = lambda v: re.sub(r"\D", "", v or "")  # noqa: E731

# ── Rule 5: slot from parent_sku ─────────────────────────────────────────────────────────────
SLOT_OF_PARENT = (
    (re.compile(r"^CEX-CR"), "cracker"),
    (re.compile(r"^(EX|CEX)-EA"), "accompaniment"),
    (re.compile(r"^(EX|CEX)-EM"), "meat"),
    (re.compile(r"^(EX|CEX)-EC"), "cheese"),
    (re.compile(r"^PR-CJAM"), "cjam"),
)


def slot_for(parent_sku: str, child_sku: str) -> str | None:
    """The parent_sku IS the slot. Fallback only for CH-/MT- (unambiguous per product-rules);
    an AC- child under a non-slot parent is NOT categorised (AC spans crackers/nuts/jams) -> None."""
    p = (parent_sku or "").strip().upper()
    for rx, slot in SLOT_OF_PARENT:
        if rx.match(p):
            return slot
    c = (child_sku or "").strip().upper()
    if c.startswith("CH-"):
        return "cheese"
    if c.startswith("MT-"):
        return "meat"
    return None


# ── Rule 7: barred list (regardless of stock) ────────────────────────────────────────────────
BARRED_EXACT = frozenset({"MT-HOTP", "AC-RMC", "MT-IBRES", "MT-BSS", "CH-MAFT", "AC-RBOL", "AC-BLUCAR"})
BARRED_SUBSTR = ("-FS-", "BRIE")
MINI_JAMS = frozenset({"AC-GBEF", "AC-SCJ", "AC-SRHUB", "AC-MFJ"})  # legal ONLY inside a PR-CJAM pair
MEAT_PRIORITY = "MT-CCCS"


def is_barred(sku: str, *, generic: bool = True) -> bool:
    s = (sku or "").upper()
    if s in BARRED_EXACT or any(x in s for x in BARRED_SUBSTR):
        return True
    return generic and s in MINI_JAMS


# ── Rule 5 pools (Kurt's sets; barred members are stripped at load so a stale edit can't leak) ──
_RAW_POOLS = {
    "cracker": ["AC-FCROSE", "AC-TOK", "AC-FCFIGO", "AC-FCWALN", "AC-FCEVOO", "AC-PFLAT"],
    "accompaniment": ["AC-PRPE", "AC-QUIC", "AC-SDF", "AC-MISS", "AC-DTCH", "AC-MARC", "AC-BRJA", "AC-BLBALS"],
    "meat": [MEAT_PRIORITY, "MT-SPAP", "MT-STUF", "MT-SCHI", "MT-TUSC", "MT-SBRES", "MT-PARM", "MT-LONZ",
             "MT-JAMS", "MT-CCBS"],
    "cheese": ["CH-OTTA", "CH-SMG", "CH-CARO", "CH-ASST", "CH-ETX", "CH-BARI", "CH-CONI", "CH-CABR",
               "CH-WWHO", "CH-QOTA", "CH-MONT", "CH-BRZ", "CH-UROSE"],
}
POOLS = {k: [s for s in v if not is_barred(s)] for k, v in _RAW_POOLS.items()}
# Rule 7: PR-CJAM legality is CONFIG — legacy authority (MS = CH-SOT/AC-MFJ, MONT/AC-SCJ).
DEFAULT_CJAM_PAIRS = OrderedDict([("CH-SOT", "AC-MFJ"), ("CH-MONT", "AC-SCJ")])

# Dietary boxes (product-rules): match the letters, never `RS`. Restricted slot -> flag, never guess.
DIETARY_RX = re.compile(r"^AHB-[ML]?CUST-(NN|CO|NC)(RS|FS)-", re.I)
DIETARY_SLOT = {"NC": "cracker", "CO": "meat", "NN": "accompaniment"}  # NN: nuts live in accompaniments

ONE = ('query($q:String!){orders(first:1,query:$q){edges{node{id customer{email} '
       'lineItems(first:100){nodes{sku currentQuantity quantity}}}}}}')
HIST = ('query($q:String!){orders(first:20,query:$q){edges{node{lineItems(first:100)'
        '{nodes{sku currentQuantity quantity}}}}}}')
V = ('query($q:String!){productVariants(first:20,query:$q){edges{node{sku price availableForSale '
     'inventoryQuantity product{id title handle}}}}}')


@dataclass(frozen=True)
class Prod:
    pid: str
    handle: str
    qty: int


def _skus(node) -> set[str]:
    o = set()
    for n in node["lineItems"]["nodes"]:
        cq = n.get("currentQuantity")
        cq = n.get("quantity", 0) if cq is None else cq
        s = (n.get("sku") or "").strip()
        if s and cq and cq > 0:
            o.add(s)
    return o


def fetch_state(order: str) -> dict:
    """Live order state: box (currentQuantity>0), removed (quantity>0 & currentQuantity==0), ever."""
    d = gql(ONE, {"q": f"name:#{order}"})["orders"]["edges"]
    if not d:
        return {"box": [], "removed": [], "ever": [], "last": None, "email": None}
    node = d[0]["node"]
    em = (node.get("customer") or {}).get("email")
    box, rem = set(), set()
    for n in node["lineItems"]["nodes"]:
        s = (n.get("sku") or "").strip()
        cq = n.get("currentQuantity")
        q = n.get("quantity", 0)
        if not s:
            continue
        if (cq or 0) > 0:
            box.add(s)
        elif q > 0:
            rem.add(s)
    ever = set(box)
    if em:
        for e in gql(HIST, {"q": f"email:{em}"})["orders"]["edges"]:
            ever |= _skus(e["node"])
    return {"box": sorted(box), "removed": sorted(rem), "ever": sorted(ever), "last": None, "email": em}


def fetch_prod(sku: str) -> Prod | None:
    """Rule 4/6: the $0 variant(s) of `sku` must resolve to exactly ONE product; qty = LIVE inventoryQuantity."""
    e = gql(V, {"q": f"sku:{sku}"})["productVariants"]["edges"]
    z = [x["node"] for x in e if (x["node"].get("sku") or "") == sku and float(x["node"].get("price") or 0) == 0.0]
    if not z or len({x["product"]["id"] for x in z}) != 1:
        return None
    qty = sum(int(x.get("inventoryQuantity") or 0) for x in z)
    p = z[0]["product"]
    return Prod(p["id"].split("/")[-1], p.get("handle") or p.get("title") or "", qty)


# ── Rule 6: stock-aware inventory ────────────────────────────────────────────────────────────
class Inventory:
    def __init__(self, prod_fn, *, min_avail: int = 30, floors: dict | None = None,
                 caps: dict | None = None, zero: set | None = None):
        self._prod, self._pc = prod_fn, {}
        self.min_avail = min_avail
        self.floors, self.caps, self.zero = dict(floors or {}), dict(caps or {}), set(zero or ())
        self.drawn: Counter = Counter()

    def prod(self, sku: str) -> Prod | None:
        if sku not in self._pc:
            self._pc[sku] = self._prod(sku)
        return self._pc[sku]

    def live(self, sku: str) -> int:
        p = self.prod(sku)
        return p.qty if p else 0

    def available(self, sku: str) -> int:
        return self.live(sku) - self.drawn[sku]

    def can_draw(self, sku: str) -> bool:
        """A pool SKU is pickable only if $0-unique, not floored/capped/zeroed, and live-drawn > min_avail."""
        if sku in self.floors or sku in self.caps or sku in self.zero:
            return False
        return self.prod(sku) is not None and self.available(sku) > self.min_avail

    def draw(self, sku: str) -> None:
        self.drawn[sku] += 1

    def keep_limit(self, sku: str) -> int | None:
        """How many source add-rows of `sku` may stay (None = unlimited). Overflow is force-swapped."""
        if sku in self.zero:
            return 0
        if sku in self.caps:
            return self.caps[sku]
        if sku in self.floors:
            return max(0, self.live(sku) - self.floors[sku])
        return None


def low_stock_report(rows: list[dict], inv: Inventory) -> list[tuple[str, int, int, int]]:
    """Rule 6: every SKU the sheet's adds would push below min_avail -> (sku, live, adds, after)."""
    adds = Counter(r["child_sku"].strip() for r in rows if r.get("child_sku", "").strip())
    out = []
    for sku, n in sorted(adds.items()):
        live = inv.live(sku)
        if live - n < inv.min_avail:
            out.append((sku, live, n, live - n))
    return out


# ── Core resolve ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Options:
    storm: int = 5
    imported_threshold: float = 0.5
    cjam_pairs: OrderedDict = field(default_factory=lambda: OrderedDict(DEFAULT_CJAM_PAIRS))


@dataclass
class Result:
    rows: list[dict]
    swaplog: list[dict]
    report: Counter
    flags: list[str]


class AlreadyImported(RuntimeError):
    """Rule 8 — the sheet appears already imported; refuse to mass-swap."""


def _set_target(r: dict, p: Prod | None, sku: str) -> None:
    if p is None:  # can_draw() already proved the $0-unique product; never write an unverified target (rule 4)
        raise RuntimeError(f"{sku}: no unique $0 product -- refusing to write an unverified target")
    r["Line: Product ID"], r["Line: Product Handle"], r["child_sku"] = p.pid, p.handle, sku


class _Picker:
    """Round-robin, stock-aware pool draw (rule 6) with the meat first-priority (rule 7)."""

    def __init__(self, inv: Inventory):
        self.inv, self.rr = inv, Counter()

    def pick(self, slot: str, blocked: set) -> str | None:
        pool = POOLS[slot]
        order = []
        if slot == "meat" and MEAT_PRIORITY in pool:
            order.append(MEAT_PRIORITY)
        rest = [s for s in pool if s != MEAT_PRIORITY] if slot == "meat" else list(pool)
        start = self.rr[slot] % len(rest) if rest else 0
        order += rest[start:] + rest[:start]
        for s in order:
            if s in blocked or is_barred(s) or not self.inv.can_draw(s):
                continue
            if s in rest:
                self.rr[slot] = rest.index(s) + 1
            self.inv.draw(s)
            return s
        return None

    def pick_cjam_pair(self, pairs: OrderedDict, blocked: set) -> tuple[str, str] | None:
        for chz, jam in pairs.items():
            if {chz, jam} & blocked or is_barred(chz) or is_barred(jam, generic=False):
                continue
            if self.inv.can_draw(chz) and self.inv.can_draw(jam):
                self.inv.draw(chz)
                self.inv.draw(jam)
                return chz, jam
        return None


def guard_already_imported(rows: list[dict], states: dict, threshold: float) -> float:
    """Rule 8: fraction of add-rows whose child_sku is already live on the order."""
    if not rows:
        return 0.0
    live = sum(1 for r in rows if r["child_sku"].strip() in set(states[norm(r["Name"])]["box"]))
    frac = live / len(rows)
    if frac > threshold:
        raise AlreadyImported(
            f"{live}/{len(rows)} add-rows ({frac:.0%}) already live on their orders (> {threshold:.0%}): "
            "sheet appears already imported -- run the not-landed scan instead (rule 8)")
    return frac


def _dietary_slots(box: set) -> set[str]:
    out = set()
    for s in box:
        m = DIETARY_RX.match(s)
        if m:
            out.add(DIETARY_SLOT[m.group(1).upper()])
    return out


def _rewrite_cjam_pair(rows, idxs, pair, inv, done: dict, override: dict, why: str, cur_i: int, o_log: list):
    """Rewrite EVERY PR-CJAM row of this order to the new (cheese, jam) pair -- rows already emitted
    (in `done`) and rows still to come (via `override`). Keeps the pair coherent (rule 5)."""
    chz, jam = pair
    for j in idxs:
        src = done.get(j) or override.get(j) or rows[j]
        if slot_for(src.get("parent_sku", ""), src["child_sku"]) != "cjam":
            continue
        cur = src["child_sku"].strip()
        new = chz if cur.startswith("CH-") else jam
        tgt = dict(src)
        _set_target(tgt, inv.prod(new), new)
        if j in done:
            done[j] = tgt
        else:
            override[j] = tgt
        o_log.append({"order": None, "orig": cur, "new": new, "reason": why if j == cur_i else "CJAM-PAIR",
                      "slot": "cjam"})


def resolve(rows: list[dict], states: dict, inv: Inventory, opts: Options) -> Result:
    """Pure resolve over already-fetched state. `states[order_digits]` = {box, removed, ever}.
    Never mutates `rows`; never drops a row (rule 1)."""
    g: OrderedDict[str, list[int]] = OrderedDict()
    for i, r in enumerate(rows):
        g.setdefault(norm(r["Name"]), []).append(i)
    picker, out, log, rep, flags = _Picker(inv), [], [], Counter(), []
    kept: Counter = Counter()   # SHEET-wide count of source adds kept per SKU (floors/caps span orders)

    for o, idxs in g.items():
        st = states[o]
        box, rem, ever = set(st["box"]), set(st["removed"]), set(st["ever"])
        adds = {rows[i]["child_sku"].strip() for i in idxs}
        restricted = _dietary_slots(box)
        drawn_before, rr_before, kept_before = Counter(inv.drawn), Counter(picker.rr), Counter(kept)
        placed, chosen, o_rep = set(), set(), Counter()
        done: dict[int, dict] = {}        # idx -> emitted row (may be rewritten later by a cjam pair)
        override: dict[int, dict] = {}    # idx -> row pre-rewritten by an earlier cjam pair pick
        o_log, cjam_done = [], False

        for i in idxs:
            r = dict(override.get(i) or rows[i])
            orig = r["child_sku"].strip()
            slot = slot_for(r.get("parent_sku", ""), orig)
            limit = inv.keep_limit(orig)
            reasons = []
            if orig in rem:
                reasons.append("REMOVED")
            if orig in box:
                reasons.append("LIVE-DUPE")
            if orig in placed:
                reasons.append("IN-SHEET-DUPE")
            if limit is not None and kept[orig] >= limit:
                reasons.append("ZERO" if orig in inv.zero else "CAP" if orig in inv.caps else "FLOOR")
            if not reasons:
                kept[orig] += 1
                placed.add(orig)
                done[i] = r
                continue

            why = "+".join(reasons)
            blocked = box | rem | ever | adds | placed | chosen
            entry = {"order": o, "orig": orig, "new": None, "reason": why, "slot": slot or "?"}
            if slot is None:
                entry["new"] = "NO-SUB:UNKNOWN-SLOT"
                o_rep["NO-SUB"] += 1
            elif slot in restricted:
                entry["new"] = "NEEDS-DIETARY-REVIEW"
                o_rep["NEEDS-DIETARY-REVIEW"] += 1
            elif slot == "cjam":
                pair = None if cjam_done else picker.pick_cjam_pair(opts.cjam_pairs, blocked)
                if pair is None:
                    entry["new"] = "NO-SUB" if not cjam_done else "NO-SUB:CJAM-ALREADY-REWRITTEN"
                    o_rep["NO-SUB"] += 1
                else:
                    _rewrite_cjam_pair(rows, idxs, pair, inv, done, override, why, i, o_log)
                    r = dict(override.pop(i))
                    chosen |= set(pair)
                    o_rep["cjam-swap"] += 1
                    cjam_done, entry = True, None
            else:
                sub = picker.pick(slot, blocked)
                if sub is None:
                    entry["new"] = "NO-SUB"
                    o_rep["NO-SUB"] += 1
                else:
                    _set_target(r, inv.prod(sub), sub)
                    chosen.add(sub)
                    entry["new"] = sub
                    o_rep[why] += 1
            if entry:
                o_log.append(entry)
            placed.add(r["child_sku"].strip())
            done[i] = r

        for e in o_log:
            e["order"] = o
        swaps = sum(1 for e in o_log if e["new"] and not str(e["new"]).startswith(("NO-SUB", "NEEDS")))
        if swaps >= opts.storm:
            # Rule 11: swap storm = box already processed. Revert this order, undo draws, flag it.
            inv.drawn, picker.rr, kept = drawn_before, rr_before, kept_before
            rep["PROBABLE-ALREADY-PROCESSED"] += 1
            flags.append(f"#{o} PROBABLE-ALREADY-PROCESSED ({swaps} substitutions >= {opts.storm}) -- left untouched")
            log.append({"order": o, "orig": "*", "new": "UNTOUCHED", "reason": f"SWAP-STORM({swaps})", "slot": "-"})
            out.extend(dict(rows[i]) for i in idxs)
            continue
        out.extend(done[i] for i in idxs)   # rule 1: one output row per input row, same order
        log.extend(o_log)
        rep.update(o_rep)

    return Result(out, log, rep, flags)


# ── Rule 9: never overwrite, always log ──────────────────────────────────────────────────────
def versioned(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while (cand := path.with_name(f"{path.stem}-{n}{path.suffix}")).exists():
        n += 1
    return cand


def swaplog_path(out: Path) -> Path:
    return out.with_name(f"{out.stem}_SWAPLOG.txt")


def write_outputs(out: Path, fields: list[str], res: Result, header: list[str]) -> tuple[Path, Path]:
    out = versioned(out)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(res.rows)
    lp = swaplog_path(out)
    with open(lp, "w", encoding="utf-8") as f:
        f.write("\n".join(header) + "\n")
        f.write("order\torig\tnew\treason\tslot\n")
        for e in res.swaplog:
            f.write(f"#{e['order']}\t{e['orig']}\t{e['new']}\t{e['reason']}\t{e['slot']}\n")
        for fl in res.flags:
            f.write(f"FLAG\t{fl}\n")
    return out, lp


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def _kv(items: list[str] | None) -> dict[str, int]:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        if not v.strip().lstrip("-").isdigit():
            raise SystemExit(f"bad SKU=N flag: {it!r}")
        out[k.strip().upper()] = int(v)
    return out


def _pairs(items: list[str] | None) -> OrderedDict:
    if not items:
        return OrderedDict(DEFAULT_CJAM_PAIRS)
    od = OrderedDict()
    for it in items:
        c, _, j = it.partition("=")
        if not c or not j:
            raise SystemExit(f"bad --cjam-pair (want CH-X=AC-Y): {it!r}")
        od[c.strip().upper()] = j.strip().upper()
    return od


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fresh", action="store_true", help="re-pull mutable box/removed for every order (rule 2)")
    ap.add_argument("--floor", action="append", metavar="SKU=N", help="never let SKU's available fall below N")
    ap.add_argument("--cap", action="append", metavar="SKU=N", help="keep at most N add-rows of SKU")
    ap.add_argument("--zero", action="append", metavar="SKU", help="swap every add of SKU out (0 stock)")
    ap.add_argument("--min-avail", type=int, default=30, help="pool SKU skipped when live-drawn <= N (default 30)")
    ap.add_argument("--storm", type=int, default=5, help="rule 11: substitutions per order that flag it (default 5)")
    ap.add_argument("--imported-threshold", type=float, default=0.5,
                    help="rule 8: abort when this fraction of add-rows are live dupes (default 0.5)")
    ap.add_argument("--cjam-pair", action="append", metavar="CH-X=AC-Y", help="override the PR-CJAM legal pairs")
    ap.add_argument("--cache-db", default=None, help="order_state_cache sqlite path (tests)")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    src, out = Path(a.src), Path(a.out)
    with open(src, encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        fields, rows = list(rdr.fieldnames or []), list(rdr)
    for need in ("Name", "child_sku", "parent_sku", "Line: Product ID", "Line: Product Handle"):
        if need not in fields:
            raise SystemExit(f"--src missing column {need!r}; not a Matrixify add-sheet")

    cache = OrderStateCache(a.cache_db) if a.cache_db else OrderStateCache()
    orders = list(OrderedDict.fromkeys(norm(r["Name"]) for r in rows))
    states = {o: cache.get(o, fetch_state, refresh=a.fresh) for o in orders}

    frac = guard_already_imported(rows, states, a.imported_threshold)  # rule 8 -- raises
    inv = Inventory(fetch_prod, min_avail=a.min_avail, floors=_kv(a.floor), caps=_kv(a.cap),
                    zero={z.strip().upper() for z in a.zero or []})
    low = low_stock_report(rows, inv)
    print(f"orders {len(orders)}  rows {len(rows)}  live-dupe fraction {frac:.1%}")
    print(f"-- rule 6: SKUs the sheet pushes below {a.min_avail} available (live, adds, after) --")
    for sku, live, n, after in low:
        print(f"  {sku:<12} live={live:<5} adds={n:<4} after={after}")
    if not low:
        print("  (none)")

    res = resolve(rows, states, inv, Options(storm=a.storm, imported_threshold=a.imported_threshold,
                                             cjam_pairs=_pairs(a.cjam_pair)))
    header = [f"# resolve_matrix_dupes  src={src}  fresh={a.fresh}  min_avail={a.min_avail}  storm={a.storm}",
              f"# floors={_kv(a.floor)} caps={_kv(a.cap)} zero={sorted(inv.zero)} cjam={dict(_pairs(a.cjam_pair))}",
              f"# low-stock: {low}"]
    outp, logp = write_outputs(out, fields, res, header)
    for e in res.swaplog:
        print(f"#{e['order']} {e['orig']} -> {e['new']}  [{e['reason']} / {e['slot']}]")
    for fl in res.flags:
        print("FLAG", fl)
    c = Counter((norm(r["Name"]), r["child_sku"].strip()) for r in res.rows)
    print("report:", dict(res.report))
    print("in-sheet dupes remaining:", sum(1 for v in c.values() if v > 1))
    print(f"rows {len(rows)} -> {len(res.rows)}  (row count preserved per order)")
    if outp != out:
        print(f"NOTE: --out existed; versioned to {outp.name} (rule 9)")
    print(f"OUT: {outp}\nSWAPLOG: {logp}")
    return 0


if __name__ == "__main__":
    _utf8_stdout()
    try:
        sys.exit(main())
    except AlreadyImported as e:
        print(f"ABORT: {e}")
        sys.exit(2)
