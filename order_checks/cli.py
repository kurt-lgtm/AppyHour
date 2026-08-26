"""python -m order_checks --tag RMFG_20260821 --ship _SHIP_2026-08-24 --sheet x.xlsx

🔴 Run AFTER child SKUs are applied. Read ORDER_CHECKS_RULES.md first.
"""
from __future__ import annotations
import argparse, csv, collections, os, sys
from .rules import load_rule_set
from .checks import evaluate, duplicate_check, cracker_check, in_scope, live, sku, tags
from .peer import peer_outliers
from . import sheet as sheetmod

DEFAULT_RULESET = os.path.expanduser(r"~\Downloads\ALLFULFILLMENTS_RuleSet_OrderMatching.xlsx")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="order_checks")
    ap.add_argument("--tag", required=True,
                    help="production tag for the run - RMFG_YYYYMMDD, or a bare tag like 8_24")
    ap.add_argument("--ship", help="_SHIP_ week tag (enables check 6a)")
    ap.add_argument("--sheet", help="pick-list xlsx (enables sheet checks)")
    ap.add_argument("--sheet-tab")
    ap.add_argument("--ruleset", default=DEFAULT_RULESET)
    ap.add_argument("--since", default=None, help="created_at_min (default: 3 weeks back)")
    ap.add_argument("--cache", help="json cache path for the Shopify pull")
    ap.add_argument("--out", default=".", help="directory for CSV output")
    a = ap.parse_args(argv)

    if not a.since:
        import datetime as dt
        a.since = (dt.date.today() - dt.timedelta(days=21)).isoformat() + "T00:00:00-04:00"

    rs = load_rule_set(a.ruleset)
    from .fetch import fetch_cohort
    want = [a.tag] + ([a.ship] if a.ship else [])
    print(f"fetching {want} since {a.since[:10]}")
    shop = fetch_cohort(want, a.since, cache=a.cache)

    cohort = {k: o for k, o in shop.items() if a.tag in tags(o)}
    if not cohort:
        print(f"WARNING: no order carries {a.tag!r}. Tags seen on this pull: "
              f"{sorted({t for o in shop.values() for t in tags(o) if t.startswith(('RMFG_','_SHIP_','8_','9_'))})[:12]}")
    sheet = sheetmod.load_sheet(a.sheet, a.sheet_tab) if a.sheet else None
    scope_ids = set(sheet) if sheet else set(cohort)
    orders = [o for k, o in shop.items() if k in scope_ids]
    print(f"{a.tag}: {len(cohort)} tagged · scope {len(orders)}"
          f"{' (sheet)' if sheet else ''}\n")

    peers = peer_outliers(orders)
    verdicts, rows, dups, crackers = collections.Counter(), [], [], []
    for o in orders:
        ok, why = in_scope(o)
        if not ok:
            verdicts["EXCLUDED:" + why] += 1
            continue
        r = evaluate(o, rs)
        verdicts[r["verdict"]] += 1
        cr = cracker_check(o)
        if cr:
            c0 = o.get("customer") or {}
            crackers.append({"order": o["name"], "verdict": cr, "box": r.get("box", ""),
                             "ships": r.get("actual", ""), "expected": r.get("expected", ""),
                             "customer": f"{c0.get('first_name','')} {c0.get('last_name','')}".strip(),
                             "email": o.get("email", "")})
        p = peers.get(o["name"])
        rule_hit = r["verdict"] in ("SHORT", "OVER", "UNBUILT", "NO_BOX")
        if rule_hit or p:
            c = o.get("customer") or {}
            sev = ("HIGH" if rule_hit and p and abs(p["delta"]) >= 3 else
                   "MED" if rule_hit else "LOW")
            rows.append({"order": o["name"], "severity": sev,
                         "signal": "BOTH" if rule_hit and p else ("RULE" if rule_hit else "PEER"),
                         "verdict": r["verdict"], "detail": r.get("detail", ""),
                         "box": r.get("box", p["box"] if p else ""),
                         "ships": r.get("actual", p["ships"] if p else ""),
                         "expected": r.get("expected", ""),
                         "peer_mode": p["mode"] if p else "", "peer_delta": p["delta"] if p else "",
                         "peer_note": p["note"] if p else "",
                         "paid_allowance": r.get("allowance", ""),
                         "children": r.get("children", ""), "parents": r.get("parents", ""),
                         "customer": f"{c.get('first_name','')} {c.get('last_name','')}".strip(),
                         "email": o.get("email", "")})
        for s, par in duplicate_check(o):
            dups.append({"order": o["name"], "repeat_sku": s, "parent": par,
                         "customer": f"{c.get('first_name','')} {c.get('last_name','')}".strip()})

    rank = {"HIGH": 0, "MED": 1, "LOW": 2}
    rows.sort(key=lambda r: (rank[r["severity"]], -abs(r["peer_delta"] or 0), r["order"]))

    def dump(name, data):
        if not data:
            return
        p = os.path.join(a.out, name)
        with open(p, "w", newline="", encoding="utf8") as fh:
            w = csv.DictWriter(fh, list(data[0].keys())); w.writeheader(); w.writerows(data)
        print(f"  -> {p} ({len(data)})")

    print("CHECK 1  " + " · ".join(f"{k} {v}" for k, v in verdicts.most_common()))
    print(f"CHECK 8  duplicate + typed parent: {len(dups)}")
    print(f"CRACKER  CEX-CR slot not delivering a cracker: {len(crackers)}")
    dump(f"check1_{a.tag}.csv", rows)
    dump(f"check8_{a.tag}.csv", dups)
    dump(f"cracker_{a.tag}.csv", crackers)

    if sheet:
        diff = sheetmod.compare(sheet, shop)
        guide = sheetmod.missing_guide(sheet, shop)
        miss = sheetmod.not_on_sheet(sheet, shop, a.tag)
        tagbad, unpulled = (sheetmod.tag_mismatch(sheet, shop, a.tag, a.ship)
                            if a.ship else ([], []))
        print(f"SHEET    count differs {len(diff)} · no guide {len(guide)} · "
              f"tagged-not-on-sheet {len(miss)} · tag mismatch {len(tagbad)}")
        if unpulled:
            print(f"         ({len(unpulled)} sheet orders older than --since {a.since[:10]} "
                  f"were not pulled - widen --since to check their tags)")
        dump(f"sheet_diff_{a.tag}.csv", diff)
        dump(f"sheet_guide_{a.tag}.csv", guide)
        dump(f"sheet_missing_{a.tag}.csv", miss)
        dump(f"sheet_tags_{a.tag}.csv", tagbad)
        dump(f"sheet_notpulled_{a.tag}.csv", unpulled)

    print(f"\nactionable: {sum(1 for r in rows if r['severity'] in ('HIGH','MED'))}")
    for r in rows[:25]:
        if r["severity"] in ("HIGH", "MED"):
            print(f"  {r['order']:<9}{r['severity']:<5}{r['verdict']:<8}{r['box'][:22]:<23}"
                  f"{str(r['ships'])+'/'+str(r['expected']):>8}  {r['detail'][:24]:<25}{r['peer_note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
