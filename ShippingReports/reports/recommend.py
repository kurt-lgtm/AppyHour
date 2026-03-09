"""Generate zip-level routing recommendations from shipment data.

Analyzes ingested shipments to identify zips that need routing overrides:
- force_2day: Ground transit consistently fails (4+ days, or API already forces 2Day)
- block_hub: Misrouted to wrong hub with transit/cost penalty
- transit_warning: Chronic 3-day zips that risk thermal failure in warm months

Output: zip_overrides.json consumed by GelPackCalculator for per-order routing.

Usage:
    python -m reports.recommend [--input PATH] [--output PATH] [--min-volume N]
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Any, Optional


# Storm dates to exclude from baseline analysis
DEFAULT_STORM_DATES = {
    '2025-12-02', '2025-12-08',
    '2026-01-27', '2026-01-28', '2026-01-29',
    '2026-02-02', '2026-02-03', '2026-02-04',
}

# Territory assignments for misroute detection
TERRITORIES = {
    'Nashville': {'AL', 'CT', 'DC', 'DE', 'FL', 'GA', 'IL', 'IN', 'KY', 'MA',
                  'MD', 'ME', 'MI', 'MO', 'MS', 'NC', 'NH', 'NJ', 'NY', 'OH',
                  'PA', 'RI', 'SC', 'TN', 'VA', 'WV', 'WI'},
    'Anaheim': {'AZ', 'CA', 'CO', 'ID', 'NM', 'NV', 'OR', 'UT', 'WA'},
    'Dallas': {'AR', 'IA', 'KS', 'LA', 'MN', 'ND', 'NE', 'OK', 'SD', 'TX'},
}

STATE_TO_HUB = {}
for _hub, _states in TERRITORIES.items():
    for _st in _states:
        STATE_TO_HUB[_st] = _hub

# States already handled at state level (don't generate zip overrides)
STATE_LEVEL_HANDLED = {'AK', 'HI', 'MN'}


def load_shipments(filepath: str) -> List[dict]:
    with open(filepath, 'r') as f:
        data = json.load(f)
    return data['shipments']


def _normalize(shipments: List[dict], storm_dates: set) -> List[dict]:
    """Filter to shipments with transit data, excluding storm dates."""
    return [
        s for s in shipments
        if s.get('transit_days') is not None
        and s.get('ship_date') not in storm_dates
    ]


def find_force_2day_zips(shipments: List[dict],
                         min_volume: int = 2,
                         min_pct_4plus: float = 50.0) -> Dict[str, dict]:
    """Find zips where ground transit consistently fails (4+ days).

    These subscribers need FedEx 2Day every cycle.
    """
    by_zip = defaultdict(list)
    for s in shipments:
        if s['state'] not in STATE_LEVEL_HANDLED:
            by_zip[s['zip']].append(s)

    results = {}
    for zip_code, rows in by_zip.items():
        if len(rows) < min_volume:
            continue

        # For states with a local hub (e.g., CA→Anaheim, NY→Nashville),
        # only flag zips where failures happen from the LOCAL hub.
        # Dallas overflow on Tue/Mon is expected and handled by Tuesday routing.
        state = rows[0]['state']
        expected_hub = STATE_TO_HUB.get(state)
        has_local_hub = expected_hub and expected_hub != 'Dallas'

        if has_local_hub:
            # For states with a local hub, evaluate both:
            # 1. Monday ships (Saturday fulfillment, all hubs available)
            # 2. Ships from the local hub on any day
            # Exclude Tuesday-only Dallas ships (expected, no other hub available)
            eval_rows = [r for r in rows
                         if r['hub'] == expected_hub or r['ship_dow'] == 'Monday']
            if len(eval_rows) < min_volume:
                continue
        else:
            eval_rows = rows

        n = len(eval_rows)
        n_4plus = sum(1 for r in eval_rows if r['transit_days'] >= 4)
        pct_4plus = n_4plus / n * 100
        avg_transit = sum(r['transit_days'] for r in eval_rows) / n

        if pct_4plus >= min_pct_4plus:
            hubs = list(set(r['hub'] for r in eval_rows))
            results[zip_code] = {
                'city': eval_rows[0]['city'],
                'state': state,
                'sample_size': n,
                'avg_transit': round(avg_transit, 1),
                'pct_4plus': round(pct_4plus, 1),
                'pct_3plus': round(sum(1 for r in eval_rows if r['transit_days'] >= 3) / n * 100, 1),
                'hubs_seen': hubs,
                'last_ship': max(r['ship_date'] for r in eval_rows if r.get('ship_date')),
            }
    return results


def find_api_forced_2day_zips(all_shipments: List[dict],
                              min_volume: int = 2) -> Dict[str, dict]:
    """Find zips where the API forces FedEx 2Day on Saturday (ground TNT too slow).

    Saturday fulfillment ships Monday. For states with a local hub, Tuesday 2Day
    from Dallas is expected. But Monday 2Day means the API had all hubs available
    and still chose 2Day — that zip genuinely needs it.
    For Dallas-territory states, all DOWs count since Dallas is the only hub.
    """
    dallas_territory = TERRITORIES.get('Dallas', set())

    fedex_2day = [
        s for s in all_shipments
        if ('2Day' in s.get('service', '') or '2 Day' in s.get('service', ''))
    ]

    by_zip = defaultdict(list)
    for s in fedex_2day:
        if s['state'] in STATE_LEVEL_HANDLED:
            continue
        if s['state'] in dallas_territory:
            # Dallas territory — any DOW counts
            by_zip[s['zip']].append(s)
        elif s.get('ship_dow') == 'Monday':
            # Non-Dallas territory, Monday = Saturday fulfillment, all hubs were available
            by_zip[s['zip']].append(s)

    results = {}
    for zip_code, rows in by_zip.items():
        if len(rows) < min_volume:
            continue
        results[zip_code] = {
            'city': rows[0]['city'],
            'state': rows[0]['state'],
            'sample_size': len(rows),
            'avg_cost': round(sum(r['cost'] for r in rows) / len(rows), 2),
            'last_ship': max(r['ship_date'] for r in rows if r.get('ship_date')),
        }
    return results


def find_misrouted_zips(shipments: List[dict],
                        min_volume: int = 3,
                        min_misroute_pct: float = 40.0) -> Dict[str, dict]:
    """Find zips consistently shipped from wrong hub with transit penalty.

    Only flags zips where misrouting causes measurable transit degradation.
    """
    by_zip = defaultdict(lambda: defaultdict(list))
    for s in shipments:
        if s['state'] not in STATE_LEVEL_HANDLED:
            by_zip[s['zip']][s['hub']].append(s)

    results = {}
    for zip_code, hub_data in by_zip.items():
        total = sum(len(rows) for rows in hub_data.values())
        if total < min_volume:
            continue

        state = next(iter(next(iter(hub_data.values()))))['state']
        expected_hub = STATE_TO_HUB.get(state)
        if not expected_hub:
            continue

        wrong_hubs = {h: rows for h, rows in hub_data.items() if h != expected_hub}
        wrong_count = sum(len(rows) for rows in wrong_hubs.values())
        if wrong_count == 0:
            continue

        misroute_pct = wrong_count / total * 100
        if misroute_pct < min_misroute_pct:
            continue

        # Check if misrouting causes transit penalty
        right_rows = hub_data.get(expected_hub, [])
        wrong_rows = [r for rows in wrong_hubs.values() for r in rows]

        right_avg = sum(r['transit_days'] for r in right_rows) / len(right_rows) if right_rows else None
        wrong_avg = sum(r['transit_days'] for r in wrong_rows) / len(wrong_rows)

        # Only flag if wrong hub is slower (or we have no right-hub data to compare)
        if right_avg is not None and wrong_avg <= right_avg:
            continue

        city = wrong_rows[0]['city']
        wrong_hub_names = list(wrong_hubs.keys())

        # Check if these are Tuesday-only (expected, not actionable on Saturday)
        wrong_dows = [r['ship_dow'] for r in wrong_rows]
        tue_pct = sum(1 for d in wrong_dows if d in ('Tuesday', 'Monday')) / len(wrong_dows) * 100
        # If >80% are Tue/Mon, this is capacity overflow not a routing problem
        if tue_pct > 80:
            continue

        results[zip_code] = {
            'city': city,
            'state': state,
            'expected_hub': expected_hub,
            'wrong_hubs': wrong_hub_names,
            'sample_size': total,
            'misroute_count': wrong_count,
            'misroute_pct': round(misroute_pct, 1),
            'wrong_avg_transit': round(wrong_avg, 1),
            'right_avg_transit': round(right_avg, 1) if right_avg else None,
            'transit_penalty': round(wrong_avg - right_avg, 1) if right_avg else None,
            'last_ship': max(r['ship_date'] for r in wrong_rows if r.get('ship_date')),
        }

    return results


def find_chronic_3day_zips(shipments: List[dict],
                           min_volume: int = 3,
                           min_pct_3plus: float = 50.0) -> Dict[str, dict]:
    """Find zips where ground is technically within 3-day but borderline.

    These zips will fail thermally in warm months. Not force_2day (they usually
    arrive), but need extra ice or monitoring.
    """
    by_zip = defaultdict(list)
    for s in shipments:
        if s['state'] not in STATE_LEVEL_HANDLED:
            by_zip[s['zip']].append(s)

    results = {}
    for zip_code, rows in by_zip.items():
        if len(rows) < min_volume:
            continue

        # Same as force_2day: for states with a local hub, use Monday + local hub data
        state = rows[0]['state']
        expected_hub = STATE_TO_HUB.get(state)
        has_local_hub = expected_hub and expected_hub != 'Dallas'

        if has_local_hub:
            eval_rows = [r for r in rows
                         if r['hub'] == expected_hub or r['ship_dow'] == 'Monday']
            if len(eval_rows) < min_volume:
                continue
        else:
            eval_rows = rows

        n = len(eval_rows)
        n_3plus = sum(1 for r in eval_rows if r['transit_days'] >= 3)
        n_4plus = sum(1 for r in eval_rows if r['transit_days'] >= 4)
        pct_3plus = n_3plus / n * 100
        pct_4plus = n_4plus / n * 100

        # Skip if already qualifies as force_2day
        if pct_4plus >= 50:
            continue

        if pct_3plus >= min_pct_3plus:
            avg_transit = sum(r['transit_days'] for r in eval_rows) / n
            hubs = list(set(r['hub'] for r in eval_rows))
            results[zip_code] = {
                'city': eval_rows[0]['city'],
                'state': state,
                'sample_size': n,
                'avg_transit': round(avg_transit, 1),
                'pct_3plus': round(pct_3plus, 1),
                'pct_4plus': round(pct_4plus, 1),
                'hubs_seen': hubs,
                'last_ship': max(r['ship_date'] for r in eval_rows if r.get('ship_date')),
            }
    return results


def build_zip_overrides(force_2day: Dict[str, dict],
                        api_forced: Dict[str, dict],
                        misrouted: Dict[str, dict],
                        chronic_3day: Dict[str, dict]) -> Dict[str, dict]:
    """Merge all findings into a single zip_overrides dict.

    Priority: force_2day > api_forced > misrouted > chronic_3day
    A zip can have multiple flags but only one primary action.
    """
    overrides = {}

    # Force 2Day (ground is broken)
    for zip_code, info in force_2day.items():
        overrides[zip_code] = {
            'city': info['city'],
            'state': info['state'],
            'action': 'force_2day',
            'tags': ['!FedEx 2Day OneRate - Dallas_AHB!'],
            'transit_override': '2-Day',
            'reason': (f"Ground avg {info['avg_transit']}d, "
                       f"{info['pct_4plus']}% at 4+d (n={info['sample_size']})"),
            'confidence': 'high' if info['sample_size'] >= 5 else 'medium',
            'sample_size': info['sample_size'],
            'last_seen': info['last_ship'],
            'source': 'transit_analysis',
        }

    # API-forced 2Day (API already knows ground doesn't work)
    for zip_code, info in api_forced.items():
        if zip_code in overrides:
            # Already flagged by transit analysis — add API confirmation
            overrides[zip_code]['reason'] += f"; API also forced 2Day {info['sample_size']}x"
            overrides[zip_code]['confidence'] = 'high'
            continue
        overrides[zip_code] = {
            'city': info['city'],
            'state': info['state'],
            'action': 'force_2day',
            'tags': ['!FedEx 2Day OneRate - Dallas_AHB!'],
            'transit_override': '2-Day',
            'reason': f"API forced FedEx 2Day {info['sample_size']}x (ground TNT exceeds 3d)",
            'confidence': 'high' if info['sample_size'] >= 3 else 'medium',
            'sample_size': info['sample_size'],
            'last_seen': info['last_ship'],
            'source': 'api_behavior',
        }

    # Misrouted zips (block wrong hub)
    for zip_code, info in misrouted.items():
        if zip_code in overrides:
            continue  # 2Day already handles it
        # Build !NO tags for the wrong hubs
        tags = []
        for wrong_hub in info['wrong_hubs']:
            tags.append(f"!NO OnTrac - {wrong_hub}_AHB!")
        overrides[zip_code] = {
            'city': info['city'],
            'state': info['state'],
            'action': 'block_hub',
            'tags': tags,
            'transit_override': None,
            'reason': (f"{info['misroute_pct']}% misrouted to {', '.join(info['wrong_hubs'])} "
                       f"(avg {info['wrong_avg_transit']}d vs {info['right_avg_transit']}d from "
                       f"{info['expected_hub']}, n={info['sample_size']})"),
            'confidence': 'high' if info['sample_size'] >= 5 else 'medium',
            'sample_size': info['sample_size'],
            'last_seen': info['last_ship'],
            'source': 'misroute_analysis',
        }

    # Chronic 3-day (thermal risk, extra ice needed)
    for zip_code, info in chronic_3day.items():
        if zip_code in overrides:
            continue
        overrides[zip_code] = {
            'city': info['city'],
            'state': info['state'],
            'action': 'transit_warning',
            'tags': [],  # No routing tag — just flag for extra ice
            'transit_override': '3-Day',  # Force 3-Day transit time for thermal calc
            'reason': (f"Chronic 3-day: {info['pct_3plus']}% at 3+d, "
                       f"avg {info['avg_transit']}d (n={info['sample_size']})"),
            'confidence': 'high' if info['sample_size'] >= 5 else 'medium',
            'sample_size': info['sample_size'],
            'last_seen': info['last_ship'],
            'source': 'transit_analysis',
        }

    return overrides


def generate_report(overrides: Dict[str, dict]) -> str:
    """Generate human-readable summary of recommendations."""
    lines = []
    lines.append("=" * 70)
    lines.append("ZIP-LEVEL ROUTING RECOMMENDATIONS")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 70)

    by_action = defaultdict(list)
    for zip_code, info in overrides.items():
        by_action[info['action']].append((zip_code, info))

    for action, label in [
        ('force_2day', 'FORCE FEDEX 2DAY (ground broken)'),
        ('block_hub', 'BLOCK WRONG HUB (misrouted)'),
        ('transit_warning', 'TRANSIT WARNING (chronic 3-day, extra ice)'),
    ]:
        items = by_action.get(action, [])
        if not items:
            continue
        lines.append(f"\n--- {label}: {len(items)} zips ---")
        by_state = defaultdict(list)
        for zip_code, info in items:
            by_state[info['state']].append((zip_code, info))
        for state in sorted(by_state.keys()):
            state_items = by_state[state]
            lines.append(f"\n  {state} ({len(state_items)} zips):")
            for zip_code, info in sorted(state_items, key=lambda x: x[0]):
                tags_str = ', '.join(info['tags']) if info['tags'] else '(none)'
                lines.append(f"    {zip_code} {info['city']}: {info['reason']}")
                lines.append(f"      Tags: {tags_str}  Confidence: {info['confidence']}")

    total = len(overrides)
    lines.append(f"\n{'=' * 70}")
    lines.append(f"TOTAL: {total} zip overrides")
    lines.append(f"  force_2day: {len(by_action.get('force_2day', []))}")
    lines.append(f"  block_hub: {len(by_action.get('block_hub', []))}")
    lines.append(f"  transit_warning: {len(by_action.get('transit_warning', []))}")
    lines.append(f"{'=' * 70}")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Generate zip-level routing recommendations')
    parser.add_argument('--input', default='output/shipments.json',
                        help='Ingested shipments JSON')
    parser.add_argument('--output', default='output/zip_overrides.json',
                        help='Output zip overrides JSON')
    parser.add_argument('--min-volume', type=int, default=2,
                        help='Minimum shipments to a zip to consider (default: 2)')
    parser.add_argument('--report', default='output/zip_recommendations.txt',
                        help='Human-readable report output')
    args = parser.parse_args()

    print("Loading shipments...")
    all_shipments = load_shipments(args.input)
    normalized = _normalize(all_shipments, DEFAULT_STORM_DATES)
    print(f"  Total: {len(all_shipments)}, Normalized (with transit, no storms): {len(normalized)}")

    print("\nAnalyzing force-2Day zips (ground consistently fails)...")
    force_2day = find_force_2day_zips(normalized, min_volume=args.min_volume)
    print(f"  Found: {len(force_2day)}")

    print("Analyzing API-forced 2Day zips (API already knows ground fails)...")
    api_forced = find_api_forced_2day_zips(all_shipments, min_volume=args.min_volume)
    print(f"  Found: {len(api_forced)}")

    print("Analyzing misrouted zips (wrong hub with transit penalty)...")
    misrouted = find_misrouted_zips(normalized, min_volume=args.min_volume)
    print(f"  Found: {len(misrouted)}")

    print("Analyzing chronic 3-day zips (thermal risk)...")
    chronic = find_chronic_3day_zips(normalized, min_volume=args.min_volume)
    print(f"  Found: {len(chronic)}")

    print("\nBuilding zip overrides...")
    overrides = build_zip_overrides(force_2day, api_forced, misrouted, chronic)
    print(f"  Total overrides: {len(overrides)}")

    # Write JSON
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    output_data = {
        'generated': datetime.now().isoformat(),
        'version': 1,
        'storm_dates_excluded': sorted(DEFAULT_STORM_DATES),
        'min_volume': args.min_volume,
        'source_shipments': len(all_shipments),
        'normalized_shipments': len(normalized),
        'summary': {
            'total_overrides': len(overrides),
            'force_2day': sum(1 for v in overrides.values() if v['action'] == 'force_2day'),
            'block_hub': sum(1 for v in overrides.values() if v['action'] == 'block_hub'),
            'transit_warning': sum(1 for v in overrides.values() if v['action'] == 'transit_warning'),
        },
        'overrides': overrides,
    }
    with open(args.output, 'w') as f:
        json.dump(output_data, f, indent=2)
    print(f"  Written: {args.output}")

    # Write report
    report = generate_report(overrides)
    with open(args.report, 'w') as f:
        f.write(report)
    print(f"  Report: {args.report}")
    print(f"\n{report}")


if __name__ == '__main__':
    main()
