"""Core analysis engine for shipping data.

Produces structured analysis results from ingested shipment data:
- Cost per shipment by carrier/hub/state
- Transit performance (avg, distribution, 3+ day rate)
- Misrouting detection (wrong hub for territory)
- Weather normalization
- Zip-level drill-down for edge cases
"""

import json
from collections import defaultdict
from datetime import date
from typing import List, Dict, Any, Optional


def load_shipments(filepath: str) -> List[dict]:
    """Load shipments from ingested JSON."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    return data['shipments']


def cost_analysis(shipments: List[dict],
                  group_by: str = 'state',
                  filters: Optional[Dict] = None) -> Dict[str, Any]:
    """Analyze cost per shipment grouped by a field.

    Args:
        shipments: List of shipment dicts
        group_by: Field to group by (state, hub, carrier, zip, service)
        filters: Optional dict of {field: value} to filter shipments
    """
    filtered = shipments
    if filters:
        for k, v in filters.items():
            if isinstance(v, list):
                filtered = [s for s in filtered if s.get(k) in v]
            else:
                filtered = [s for s in filtered if s.get(k) == v]

    groups = defaultdict(list)
    for s in filtered:
        key = s.get(group_by, 'Unknown')
        groups[key].append(s)

    results = {}
    for key, rows in sorted(groups.items()):
        costs = [r['cost'] for r in rows]
        results[key] = {
            'count': len(rows),
            'total_cost': sum(costs),
            'avg_cost': sum(costs) / len(costs) if costs else 0,
            'min_cost': min(costs) if costs else 0,
            'max_cost': max(costs) if costs else 0,
        }
    return results


def transit_analysis(shipments: List[dict],
                     group_by: str = 'state',
                     filters: Optional[Dict] = None,
                     exclude_dates: Optional[List[str]] = None) -> Dict[str, Any]:
    """Analyze transit time performance.

    Args:
        shipments: List of shipment dicts
        group_by: Field to group by
        filters: Optional filters
        exclude_dates: List of ISO date strings to exclude (storm days)
    """
    filtered = shipments
    if filters:
        for k, v in filters.items():
            if isinstance(v, list):
                filtered = [s for s in filtered if s.get(k) in v]
            else:
                filtered = [s for s in filtered if s.get(k) == v]

    if exclude_dates:
        exclude_set = set(exclude_dates)
        filtered = [s for s in filtered if s.get('ship_date') not in exclude_set]

    # Only include rows with transit data
    filtered = [s for s in filtered if s.get('transit_days') is not None]

    groups = defaultdict(list)
    for s in filtered:
        key = s.get(group_by, 'Unknown')
        groups[key].append(s)

    results = {}
    for key, rows in sorted(groups.items()):
        times = [r['transit_days'] for r in rows]
        if not times:
            continue
        n = len(times)
        results[key] = {
            'count': n,
            'avg_transit': sum(times) / n,
            'pct_1day': sum(1 for t in times if t <= 1) / n * 100,
            'pct_2day': sum(1 for t in times if t == 2) / n * 100,
            'pct_3day': sum(1 for t in times if t == 3) / n * 100,
            'pct_4plus': sum(1 for t in times if t >= 4) / n * 100,
            'max_transit': max(times),
        }
    return results


def misroute_analysis(shipments: List[dict],
                      territories: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """Detect shipments routed through the wrong hub for their state.

    Args:
        shipments: List of shipment dicts
        territories: Dict mapping hub name to list of state abbreviations
    """
    # Build reverse lookup: state -> expected hub
    state_to_hub = {}
    for hub, states in territories.items():
        for state in states:
            state_to_hub[state] = hub

    misroutes = []
    by_state = defaultdict(lambda: defaultdict(list))
    for s in shipments:
        if s.get('transit_days') is not None:
            by_state[s['state']][s['hub']].append(s)

    for state in sorted(by_state.keys()):
        expected = state_to_hub.get(state, 'Unknown')
        hub_data = by_state[state]
        wrong = {h: rows for h, rows in hub_data.items() if h != expected}
        right = hub_data.get(expected, [])

        if not wrong:
            continue

        wrong_count = sum(len(rows) for rows in wrong.values())
        total = wrong_count + len(right)
        if wrong_count == 0:
            continue

        right_avg_cost = sum(r['cost'] for r in right) / len(right) if right else 0
        wrong_all = [r for rows in wrong.values() for r in rows]
        wrong_avg_cost = sum(r['cost'] for r in wrong_all) / len(wrong_all)
        wrong_avg_transit = sum(r['transit_days'] for r in wrong_all) / len(wrong_all)

        misroutes.append({
            'state': state,
            'expected_hub': expected,
            'wrong_count': wrong_count,
            'total_count': total,
            'misroute_pct': wrong_count / total * 100,
            'expected_avg_cost': right_avg_cost,
            'wrong_avg_cost': wrong_avg_cost,
            'cost_spread': wrong_avg_cost - right_avg_cost,
            'wrong_avg_transit': wrong_avg_transit,
            'hub_breakdown': {h: len(rows) for h, rows in hub_data.items()},
        })

    return sorted(misroutes, key=lambda x: -x['wrong_count'])


def zip_level_analysis(shipments: List[dict],
                       state: str,
                       min_volume: int = 3) -> Dict[str, Any]:
    """Drill down to zip-code level for a specific state.

    Args:
        state: 2-letter state code
        min_volume: Minimum shipments to include a zip
    """
    state_rows = [s for s in shipments if s['state'] == state and s.get('transit_days') is not None]

    by_zip = defaultdict(list)
    for s in state_rows:
        by_zip[s['zip']].append(s)

    results = {}
    for zip_code, rows in sorted(by_zip.items()):
        if len(rows) < min_volume:
            continue
        times = [r['transit_days'] for r in rows]
        costs = [r['cost'] for r in rows]
        hubs = defaultdict(int)
        carriers = defaultdict(int)
        for r in rows:
            hubs[r['hub']] += 1
            carriers[r['carrier']] += 1

        results[zip_code] = {
            'city': rows[0]['city'],
            'count': len(rows),
            'avg_transit': sum(times) / len(times),
            'avg_cost': sum(costs) / len(costs),
            'pct_3plus': sum(1 for t in times if t >= 3) / len(times) * 100,
            'max_transit': max(times),
            'hubs': dict(hubs),
            'carriers': dict(carriers),
        }

    return results


def weekly_summary(shipments: List[dict]) -> List[Dict[str, Any]]:
    """Generate week-by-week summary for trend analysis."""
    by_week = defaultdict(list)
    for s in shipments:
        if s.get('ship_date'):
            # Group by ISO week
            d = date.fromisoformat(s['ship_date'])
            week_key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
            by_week[week_key].append(s)

    results = []
    for week in sorted(by_week.keys()):
        rows = by_week[week]
        times = [r['transit_days'] for r in rows if r.get('transit_days') is not None]
        costs = [r['cost'] for r in rows]
        results.append({
            'week': week,
            'total_ships': len(rows),
            'with_transit': len(times),
            'avg_cost': sum(costs) / len(costs) if costs else 0,
            'avg_transit': sum(times) / len(times) if times else None,
            'pct_3plus': sum(1 for t in times if t >= 3) / len(times) * 100 if times else None,
            'pct_4plus': sum(1 for t in times if t >= 4) / len(times) * 100 if times else None,
        })

    return results
