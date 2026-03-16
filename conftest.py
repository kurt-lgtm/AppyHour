"""Root conftest — shared fixtures for AppyHour test suite."""

import sys
from pathlib import Path

# Ensure subpackages are importable without install
ROOT = Path(__file__).parent
for subdir in ("InventoryReorder", "GelPackCalculator", "ShippingReports", "AppyHourMCP"):
    p = ROOT / subdir
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))
