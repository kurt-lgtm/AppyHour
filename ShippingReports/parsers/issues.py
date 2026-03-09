"""Parser for customer issue CSV exports (Issue & Resolution Guide)."""

import csv
import os
from typing import List
from .common import CustomerIssue, parse_date_flexible


# Issue types we care about for shipping analysis
SHIPPING_ISSUE_KEYWORDS = {
    'lost_in_transit': ['lost in transit', 'lost package', 'never arrived', 'not delivered'],
    'misdelivered': ['misdelivered', 'wrong address', 'delivered to wrong'],
    'damaged': ['damaged', 'melted', 'thawed', 'warm', 'temperature'],
    'delayed': ['delayed', 'late delivery', 'took too long'],
}


def classify_issue(description: str) -> str:
    """Classify an issue description into a shipping issue type."""
    desc_lower = description.lower()
    for issue_type, keywords in SHIPPING_ISSUE_KEYWORDS.items():
        if any(kw in desc_lower for kw in keywords):
            return issue_type
    return 'other'


def parse_issues_csv(filepath: str, product_value: float = 100.0) -> List[CustomerIssue]:
    """Parse the Issue & Resolution Guide CSV into CustomerIssue records.

    Args:
        filepath: Path to the CSV file
        product_value: Average product value for cost impact calculation
    """
    issues = []

    with open(filepath, 'r', encoding='latin-1') as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []

        # Auto-detect column names (they vary between exports)
        state_col = next((c for c in columns if 'state' in c.lower()), None)
        zip_col = next((c for c in columns if 'zip' in c.lower() or 'postal' in c.lower()), None)
        city_col = next((c for c in columns if 'city' in c.lower()), None)
        order_col = next((c for c in columns if 'order' in c.lower()), None)
        issue_col = next((c for c in columns if 'issue' in c.lower() or 'type' in c.lower()
                          or 'description' in c.lower() or 'reason' in c.lower()), None)
        resolution_col = next((c for c in columns if 'resolution' in c.lower()
                               or 'action' in c.lower()), None)
        date_col = next((c for c in columns if 'date' in c.lower()), None)
        carrier_col = next((c for c in columns if 'carrier' in c.lower()
                            or 'shipping' in c.lower()), None)
        tracking_col = next((c for c in columns if 'tracking' in c.lower()), None)

        for row in reader:
            issue_desc = row.get(issue_col, '') if issue_col else ''
            issue_type = classify_issue(issue_desc)

            resolution = row.get(resolution_col, '') if resolution_col else ''
            # Estimate cost impact
            res_lower = resolution.lower()
            if 'reship' in res_lower:
                cost_impact = product_value + 8.59 * 2  # product + original shipping + reship
            elif 'refund' in res_lower or 'credit' in res_lower:
                cost_impact = product_value
            else:
                cost_impact = 0.0

            issues.append(CustomerIssue(
                order_id=row.get(order_col, '').strip() if order_col else '',
                issue_type=issue_type,
                state=(row.get(state_col, '') or '').strip().upper()[:2] if state_col else '',
                zip_code=(row.get(zip_col, '') or '').strip()[:5] if zip_col else '',
                city=(row.get(city_col, '') or '').strip() if city_col else '',
                carrier=(row.get(carrier_col, '') or '').strip() if carrier_col else '',
                tracking=(row.get(tracking_col, '') or '').strip() if tracking_col else '',
                date_reported=parse_date_flexible(row.get(date_col, '')) if date_col else None,
                resolution=resolution.strip(),
                cost_impact=cost_impact,
                source_file=filepath,
            ))

    return issues
