"""Storm-normalized transit time analysis per hub per state.

Auto-detects weather outlier windows from shipment data, excludes them,
and computes reliable transit baselines. Compares against current
GelPackCalculator routing profile TNTs and flags discrepancies.

Standalone utility — does not modify reports or routing configs.
Outputs analysis to console and optional JSON for review.

Usage:
    python -m reports.normalize_transit
    python -m reports.normalize_transit --hub Dallas --profile "Optimized Tuesday"
    python -m reports.normalize_transit --output output/transit_audit.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Set, Tuple


ABBR_TO_STATE = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
    'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
    'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii',
    'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
    'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine',
    'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota',
    'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska',
    'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico',
    'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio',
    'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island',
    'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas',
    'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
}
STATE_TO_ABBR = {v: k for k, v in ABBR_TO_STATE.items()}


def load_shipments(filepath: str) -> List[dict]:
    with open(filepath, 'r') as f:
        data = json.load(f)
    return data['shipments']


def detect_storm_dates(shipments: List[dict],
                       z_threshold: float = 2.0,
                       min_daily_volume: int = 10) -> Set[str]:
    """Auto-detect storm/weather outlier dates from transit data.

    Groups shipments by ship_date, computes daily average transit time,
    and flags dates where the average exceeds z_threshold standard
    deviations above the global mean. Adjacent flagged dates are merged
    into contiguous storm windows.

    Returns set of ISO date strings to exclude.
    """
    by_date = defaultdict(list)
    for s in shipments:
        if s.get('transit_days') is not None and s.get('ship_date'):
            by_date[s['ship_date']].append(s['transit_days'])

    # Compute daily averages for dates with enough volume
    daily_avgs = {}
    for dt, times in by_date.items():
        if len(times) >= min_daily_volume:
            daily_avgs[dt] = sum(times) / len(times)

    if len(daily_avgs) < 5:
        return set()

    vals = list(daily_avgs.values())
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = variance ** 0.5

    if std < 0.1:
        return set()

    threshold = mean + z_threshold * std

    storm_dates = set()
    for dt, avg in daily_avgs.items():
        if avg > threshold:
            storm_dates.add(dt)

    # Also flag low-volume dates adjacent to storm dates (spillover delays)
    all_dates_with_transit = sorted(by_date.keys())
    for dt in all_dates_with_transit:
        if dt in storm_dates:
            continue
        if dt not in daily_avgs:
            # Low volume day — check if adjacent to a storm date
            try:
                d = date.fromisoformat(dt)
            except (ValueError, TypeError):
                continue
            for offset in (-1, 1):
                neighbor = date.fromordinal(d.toordinal() + offset).isoformat()
                if neighbor in storm_dates:
                    times = by_date[dt]
                    avg = sum(times) / len(times)
                    if avg > mean + 1.0 * std:
                        storm_dates.add(dt)
                    break

    return storm_dates


def _recency_weight(ship_date_str: str, latest: date,
                     half_life_days: int = 30) -> float:
    """Exponential decay weight based on shipment age.

    Shipments from today get weight 1.0. Weight halves every
    half_life_days. A 60-day-old shipment with 30-day half-life
    gets weight 0.25.
    """
    try:
        d = date.fromisoformat(ship_date_str)
    except (ValueError, TypeError):
        return 0.5  # fallback for missing dates
    age = (latest - d).days
    if age < 0:
        age = 0
    return 0.5 ** (age / half_life_days)


def _weighted_median(values: List[float], weights: List[float]) -> float:
    """Compute weighted median from parallel value/weight lists."""
    pairs = sorted(zip(values, weights))
    total_w = sum(w for _, w in pairs)
    cumulative = 0.0
    for val, w in pairs:
        cumulative += w
        if cumulative >= total_w / 2:
            return val
    return pairs[-1][0]


def compute_transit_stats(shipments: List[dict],
                          storm_dates: Set[str],
                          hub_filter: Optional[str] = None,
                          dow_filter: Optional[str] = None,
                          half_life_days: int = 30) -> Dict[str, dict]:
    """Compute transit stats per state, excluding storm dates.

    Uses recency-weighted median: recent shipments carry more weight
    than older ones (exponential decay with configurable half-life).

    Returns dict keyed by full state name with stats:
      n, avg, weighted_median, p90, min, max, recommended_tnt
    """
    filtered = [
        s for s in shipments
        if s.get('transit_days') is not None
        and s['transit_days'] > 0
        and s.get('ship_date') not in storm_dates
    ]
    if hub_filter:
        filtered = [s for s in filtered if s.get('hub') == hub_filter]
    if dow_filter:
        filtered = [s for s in filtered if s.get('ship_dow') == dow_filter]

    # Find latest ship date for recency reference
    all_dates = [s['ship_date'] for s in filtered if s.get('ship_date')]
    if not all_dates:
        return {}
    latest = date.fromisoformat(max(all_dates))

    by_state = defaultdict(list)
    for s in filtered:
        state_full = ABBR_TO_STATE.get(s['state'], s['state'])
        w = _recency_weight(s.get('ship_date', ''), latest, half_life_days)
        by_state[state_full].append((s['transit_days'], w))

    results = {}
    for state, pairs in sorted(by_state.items()):
        n = len(pairs)
        times = [t for t, _ in pairs]
        weights = [w for _, w in pairs]
        ts = sorted(times)
        raw_median = ts[n // 2]
        w_median = _weighted_median(times, weights)
        p90 = ts[int(n * 0.9)] if n >= 5 else max(ts)

        w_avg = sum(t * w for t, w in pairs) / sum(weights)

        # Recommend based on weighted median
        if w_median <= 1:
            rec = 1
        elif w_median <= 2:
            rec = 2
        else:
            rec = 3

        results[state] = {
            'n': n,
            'avg': round(sum(ts) / n, 2),
            'weighted_avg': round(w_avg, 2),
            'median': raw_median,
            'weighted_median': int(w_median),
            'p90': p90,
            'min': min(ts),
            'max': max(ts),
            'recommended_tnt': rec,
        }

    return results


def load_profile_tnt(settings_path: str,
                     profile_name: str) -> Optional[Dict[str, int]]:
    """Load TNT config from gel_calc_shopify_settings.json."""
    try:
        with open(settings_path, 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    tnt_configs = data.get('transit_type_configs', {})
    profile = tnt_configs.get(profile_name)
    if not profile:
        return None

    result = {}
    for state, val in profile.items():
        if isinstance(val, str) and '-Day' in val:
            result[state] = int(val.split('-')[0])
    return result


def load_profile_tags(settings_path: str,
                      profile_name: str) -> Optional[Dict[str, List[str]]]:
    """Load routing tags from gel_calc_shopify_settings.json."""
    try:
        with open(settings_path, 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    tag_configs = data.get('routing_tag_configs', {})
    return tag_configs.get(profile_name)


def diff_profile(stats: Dict[str, dict],
                 profile_tnt: Dict[str, int],
                 profile_tags: Optional[Dict[str, List[str]]] = None
                 ) -> List[dict]:
    """Compare computed stats against profile TNT, return discrepancies."""
    diffs = []
    for state, current_tnt in sorted(profile_tnt.items()):
        st = stats.get(state)
        has_2day_tag = False
        if profile_tags:
            tags = profile_tags.get(state, [])
            has_2day_tag = any('2Day' in t or '2 Day' in t for t in tags)

        if st is None:
            continue

        rec = st['recommended_tnt']

        # If state has FedEx 2Day tag, actual delivery is 2-Day regardless
        # of ground transit. Only flag if current TNT > 2 (overtreating)
        # or if ground says 1-Day (tag is unnecessary).
        effective_rec = rec
        if has_2day_tag and rec > 2:
            effective_rec = 2  # FedEx 2Day overrides ground

        if current_tnt != effective_rec:
            diffs.append({
                'state': state,
                'current_tnt': current_tnt,
                'recommended_tnt': effective_rec,
                'ground_median': st['median'],
                'weighted_median': st['weighted_median'],
                'has_2day_tag': has_2day_tag,
                'n': st['n'],
                'avg': st['avg'],
                'weighted_avg': st['weighted_avg'],
                'p90': st['p90'],
            })

    return diffs


def format_storm_report(storm_dates: Set[str],
                        shipments: List[dict]) -> str:
    """Format detected storm windows for display."""
    if not storm_dates:
        return "  No storm dates detected."

    # Group into contiguous windows
    sorted_dates = sorted(storm_dates)
    windows = []
    window_start = sorted_dates[0]
    window_end = sorted_dates[0]

    for dt in sorted_dates[1:]:
        d = date.fromisoformat(dt)
        prev = date.fromisoformat(window_end)
        if (d - prev).days <= 2:  # Allow 1-day gap
            window_end = dt
        else:
            windows.append((window_start, window_end))
            window_start = dt
            window_end = dt
    windows.append((window_start, window_end))

    # Count affected shipments per window
    by_date = defaultdict(int)
    for s in shipments:
        if s.get('ship_date') in storm_dates and s.get('transit_days') is not None:
            by_date[s['ship_date']] += 1

    lines = []
    for start, end in windows:
        n = sum(by_date.get(d, 0) for d in sorted_dates
                if start <= d <= end)
        if start == end:
            lines.append(f"  {start} ({n} shipments affected)")
        else:
            lines.append(f"  {start} .. {end} ({n} shipments affected)")
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Storm-normalized transit analysis vs routing profile')
    parser.add_argument('--input', default='output/shipments.json',
                        help='Ingested shipments JSON (default: output/shipments.json)')
    parser.add_argument('--settings',
                        default='../GelPackCalculator/gel_calc_shopify_settings.json',
                        help='GelPackCalculator settings JSON')
    parser.add_argument('--hub', default=None,
                        help='Filter to a specific hub (Dallas, Nashville, Anaheim)')
    parser.add_argument('--dow', default=None,
                        help='Filter to ship day-of-week (Tuesday, Monday, etc.)')
    parser.add_argument('--profile', default=None,
                        help='Profile name to compare against (e.g., "Optimized Tuesday")')
    parser.add_argument('--z-threshold', type=float, default=2.0,
                        help='Z-score threshold for storm detection (default: 2.0)')
    parser.add_argument('--half-life', type=int, default=30,
                        help='Recency half-life in days (default: 30). '
                             'Recent shipments weigh more in median/avg.')
    parser.add_argument('--output', default=None,
                        help='Optional JSON output path')
    args = parser.parse_args()

    # Load shipments
    if not os.path.isfile(args.input):
        print(f"Error: {args.input} not found. Run 'python ingest.py' first.")
        sys.exit(1)

    print("Loading shipments...")
    all_shipments = load_shipments(args.input)
    print(f"  Total: {len(all_shipments)}")

    # Detect storm dates
    print(f"\nDetecting storm windows (z-threshold={args.z_threshold})...")
    storm_dates = detect_storm_dates(all_shipments, z_threshold=args.z_threshold)
    print(format_storm_report(storm_dates, all_shipments))

    n_excluded = sum(1 for s in all_shipments
                     if s.get('ship_date') in storm_dates
                     and s.get('transit_days') is not None)
    print(f"  Excluding {n_excluded} shipments from {len(storm_dates)} storm dates")

    # Compute stats
    filter_desc = []
    if args.hub:
        filter_desc.append(f"hub={args.hub}")
    if args.dow:
        filter_desc.append(f"dow={args.dow}")
    filter_label = f" ({', '.join(filter_desc)})" if filter_desc else ""

    print(f"\nComputing storm-normalized transit stats{filter_label}...")
    print(f"  Recency half-life: {args.half_life} days")
    stats = compute_transit_stats(all_shipments, storm_dates,
                                  hub_filter=args.hub, dow_filter=args.dow,
                                  half_life_days=args.half_life)

    # Print stats table
    print(f"\n{'State':<22} {'N':>5} {'Avg':>5} {'WAvg':>5} {'Med':>4} "
          f"{'WMed':>5} {'P90':>4} {'Min':>4} {'Max':>4} {'Rec':>4}")
    print('-' * 78)
    for state in sorted(stats.keys()):
        st = stats[state]
        print(f"{state:<22} {st['n']:>5} {st['avg']:>5.1f} "
              f"{st['weighted_avg']:>5.1f} {st['median']:>4} "
              f"{st['weighted_median']:>5} {st['p90']:>4} "
              f"{st['min']:>4} {st['max']:>4} "
              f"{st['recommended_tnt']:>3}-Day")

    # Compare against profile
    if args.profile:
        print(f"\nComparing against profile: {args.profile}")
        profile_tnt = load_profile_tnt(args.settings, args.profile)
        profile_tags = load_profile_tags(args.settings, args.profile)

        if profile_tnt is None:
            print(f"  Error: profile '{args.profile}' not found in {args.settings}")
        else:
            diffs = diff_profile(stats, profile_tnt, profile_tags)
            if not diffs:
                print("  No discrepancies found.")
            else:
                print(f"\n  {len(diffs)} discrepancies:")
                print(f"  {'State':<22} {'Current':>8} {'Rec':>6} "
                      f"{'Med':>4} {'WMed':>5} {'Tag':>4} "
                      f"{'N':>5} {'WAvg':>5}")
                print(f"  {'-' * 65}")
                for d in diffs:
                    tag = 'yes' if d['has_2day_tag'] else 'no'
                    print(f"  {d['state']:<22} {d['current_tnt']}-Day"
                          f"  {d['recommended_tnt']}-Day"
                          f"  {d['ground_median']:>3}"
                          f"  {d['weighted_median']:>4}"
                          f"  {tag:>4}"
                          f"  {d['n']:>5} {d['weighted_avg']:>5.1f}")

            # States in profile but no invoice data
            no_data = [s for s in sorted(profile_tnt.keys()) if s not in stats]
            if no_data:
                print(f"\n  {len(no_data)} states with no shipment data "
                      f"(keeping current TNT):")
                for s in no_data:
                    print(f"    {s}: {profile_tnt[s]}-Day")

    # JSON output
    if args.output:
        output_data = {
            'generated': datetime.now().isoformat(),
            'filters': {
                'hub': args.hub,
                'dow': args.dow,
                'z_threshold': args.z_threshold,
                'half_life_days': args.half_life,
            },
            'storm_dates_detected': sorted(storm_dates),
            'n_excluded': n_excluded,
            'stats': stats,
        }
        if args.profile:
            output_data['profile'] = args.profile
            if profile_tnt:
                output_data['diffs'] = diffs
                output_data['no_data_states'] = no_data

        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(args.output, 'w') as f:
            json.dump(output_data, f, indent=2)
        print(f"\nOutput: {args.output}")


if __name__ == '__main__':
    main()
