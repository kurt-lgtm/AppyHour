"""tag_where.py — bulk predicate tagging on a _SHIP cohort. Dry-run DEFAULT.

Constraints SSOT: scripts/TAG_WHERE_RULES.md — READ IT before changing this file.

Usage:
    python scripts/tag_where.py SHIP_TAG [--has SKU]... [--lacks SKU]...
        [--tagged T]... [--not-tagged T]... (--add T... | --remove T...)
        [--prefix] [--apply] [--force-ice-remove]

Guardrails (see TAG_WHERE_RULES.md):
- Tags are a SET: writes go through tools.shopify.apply_tag_update (read-modify-write,
  cache bust) — never a blind overwrite, never normalizes other tags.
- Ice tags (contains 'gel'/'ice', e.g. !ExtraGel48oz!) are ADD-ONLY; --remove of one
  is refused without --force-ice-remove.
- Operator tags are OPAQUE — exact match, or prefix only with --prefix. No format checks.
- --has/--lacks use active_line_items (raw line_items phantom-counts removed items).
- No --apply -> zero writes. Append-only JSONL delta log in _outputs/logs/.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import io
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 🔴 Windows cp1252 kills any non-ASCII print (arrows, emoji) MID-RUN — for a --apply tool that
# means a crash BETWEEN mutations, leaving the batch half-applied. Wrap stdout before anything
# prints, including argparse --help. (Live 2026-08-09: shorts_pass.py --help died on U+2192.)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# Windows cp1252 stdout crashes on emoji/em-dash — force UTF-8 (workspace standard)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent          # AppyHour/scripts
_MCP_DIR = _HERE.parent / "AppyHourMCP"
sys.path.insert(0, str(_MCP_DIR))                # utils.py + tools/ package

ICE_TAG_RE = re.compile(r"(?i)(gel|ice)")        # !ExtraGel24oz!, !ExtraGel48oz!, tray-ice — add-only
LOG_DIR = _HERE.parent.parent / "_outputs" / "logs"


def split_tags(tag_str: str) -> list[str]:
    """Order-preserving split of Shopify's comma-joined tag string. NO normalization."""
    return [t.strip() for t in (tag_str or "").split(",") if t.strip()]


def tag_match(order_tags: list[str], target: str, prefix: bool) -> bool:
    if prefix:
        return any(t.startswith(target) for t in order_tags)
    return target in order_tags


def order_skus(order: dict) -> set[str]:
    """SKUs with net quantity > 0 — MUST use active_line_items (removed-item phantom)."""
    from utils import active_line_items
    return {li.get("sku") or "" for li in active_line_items(order)}


def matches(order: dict, args: argparse.Namespace) -> bool:
    tags = split_tags(order.get("tags", ""))
    for t in args.tagged:
        if not tag_match(tags, t, args.prefix):
            return False
    for t in args.not_tagged:
        if tag_match(tags, t, args.prefix):
            return False
    if args.has or args.lacks:
        skus = order_skus(order)
        for sku in args.has:
            if sku not in skus:
                return False
        for sku in args.lacks:
            if sku in skus:
                return False
    return True


def after_tags(before: list[str], add: list[str], remove: list[str]) -> list[str]:
    """Preview of apply_tag_update's set semantics (kept in sync with tools/shopify.py)."""
    out = [t for t in before if t not in remove]
    out.extend(t for t in add if t not in out)
    return out


def fetch_cohort(ship_tag: str) -> list[dict]:
    from utils import get_shopify_auth, shopify_paginate
    base, headers = get_shopify_auth()
    params = {
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "limit": 250,
        "tag": ship_tag,
        # refunds REQUIRED or active_line_items silently no-ops (RULES gotcha 4)
        "fields": "id,name,tags,line_items,refunds",
    }
    return shopify_paginate(f"{base}/orders.json", headers, params=params, key="orders")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bulk tag orders matching a predicate. Dry-run default.")
    p.add_argument("ship_tag")
    p.add_argument("--has", action="append", default=[], metavar="SKU")
    p.add_argument("--lacks", action="append", default=[], metavar="SKU")
    p.add_argument("--tagged", action="append", default=[], metavar="TAG")
    p.add_argument("--not-tagged", dest="not_tagged", action="append", default=[], metavar="TAG")
    p.add_argument("--add", nargs="+", default=[], metavar="TAG")
    p.add_argument("--remove", nargs="+", default=[], metavar="TAG")
    p.add_argument("--prefix", action="store_true", help="--tagged/--not-tagged match by prefix")
    p.add_argument("--apply", action="store_true", help="actually write (default: dry-run preview)")
    p.add_argument("--force-ice-remove", action="store_true",
                   help="override the ice-tags-are-add-only guardrail (loud warning)")
    p.add_argument("--allow-absent-sku", action="store_true",
                   help="permit a --has/--lacks SKU that appears on ZERO orders (default: refuse)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.add and not args.remove:
        print("ERROR: nothing to do — pass --add and/or --remove.", file=sys.stderr)
        return 2

    # 🔴 Ice tags are ADD-ONLY (TAG_WHERE_RULES gotcha 2)
    ice_removes = [t for t in args.remove if ICE_TAG_RE.search(t)]
    if ice_removes and not args.force_ice_remove:
        print(f"REFUSED: --remove of ice tag(s) {ice_removes} — tray/extra ice tags are ADD-ONLY "
              f"([[tray-ice-tags-never-remove]]). Re-run with --force-ice-remove if Kurt approved.",
              file=sys.stderr)
        return 2
    if ice_removes:
        print(f"⚠️  --force-ice-remove: removing ice tag(s) {ice_removes} — add-only guardrail OVERRIDDEN.")

    cohort = fetch_cohort(args.ship_tag)

    # 🔴 Join-zero guard (added 2026-08-08 after a live smoke test): `--has PR-CJAM` returned
    # 0 of 2321 orders because the real SKU is PR-CJAM-GEN. Matching is EXACT, so a near-miss
    # SKU silently reads as "no orders qualify" instead of "your SKU is wrong". A --lacks SKU
    # that exists nowhere is worse still: every order trivially lacks it, so the predicate
    # matches the WHOLE cohort and would mass-tag it. Prove each SKU appears at least once.
    predicate_skus = list(args.has) + list(args.lacks)
    if predicate_skus and not args.allow_absent_sku:
        present: set[str] = set()
        for o in cohort:
            present |= order_skus(o)
        absent = [s for s in predicate_skus if s not in present]
        if absent:
            near = {s: sorted(p for p in present if p.startswith(s) or s.startswith(p))[:5]
                    for s in absent}
            print(f"REFUSED: SKU(s) {absent} appear on ZERO of {len(cohort)} orders in "
                  f"{args.ship_tag} — that is a wrong/typo'd SKU, not an empty result.",
                  file=sys.stderr)
            for s, cands in near.items():
                print(f"  {s} -> did you mean: {cands or '(no similar SKU in cohort)'}", file=sys.stderr)
            print("  Re-run with --allow-absent-sku only if you truly expect it to be absent.",
                  file=sys.stderr)
            return 2

    matched = [o for o in cohort if matches(o, args)]

    # Zero is a claim (gotcha 8): always show cohort size so a dead tag is visible.
    print(f"cohort {args.ship_tag}: {len(cohort)} open unfulfilled orders fetched, {len(matched)} match")
    if not cohort:
        print("⚠️  0 orders on the tag at all — check the SHIP_TAG spelling before trusting this.")

    plan = []
    for o in matched:
        before = split_tags(o.get("tags", ""))
        after = after_tags(before, args.add, args.remove)
        plan.append({"order_id": o["id"], "name": o.get("name", "?"),
                     "before": before, "after": after, "noop": before == after})

    for row in plan:
        marker = " (no-op)" if row["noop"] else ""
        print(f"  {row['name']}{marker}")
        print(f"    before: {', '.join(row['before'])}")
        print(f"    after:  {', '.join(row['after'])}")

    if not args.apply:
        print(f"\nDRY-RUN — no writes. Re-run with --apply to tag {len(matched)} orders "
              f"(add={args.add} remove={args.remove}).")
        return 0

    from tools.shopify import apply_tag_update  # canonical write path — never reimplement

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"tag_where_{args.ship_tag}_{_dt.date.today():%Y%m%d}.jsonl"

    failures = []
    with open(log_path, "a", encoding="utf-8") as log, ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(apply_tag_update, row["order_id"], args.add, args.remove): row for row in plan}
        for fut in as_completed(futs):
            row = futs[fut]
            try:
                final = fut.result()
            except Exception as e:  # noqa: BLE001 — collected + reported, nonzero exit
                failures.append((row["name"], str(e)))
                print(f"  ❌ {row['name']}: {e}", file=sys.stderr)
                continue
            log.write(json.dumps({
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "ship_tag": args.ship_tag, "order_id": row["order_id"], "name": row["name"],
                "before": row["before"], "after": final,
                "added": args.add, "removed": args.remove,
            }) + "\n")
            log.flush()

    ok = len(plan) - len(failures)
    print(f"\napplied {ok}/{len(plan)} · delta log appended: {log_path}")
    if failures:
        print(f"❌ {len(failures)} failures — NOT retried; verify state before re-running.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
