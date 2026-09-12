"""Root conftest — shared fixtures for AppyHour test suite."""

import sys
from pathlib import Path

# Ensure subpackages are importable without install
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from appyhour_lib.paths import gelpack_root  # noqa: E402

# GelPackCalculator is a SIBLING repo since 2026-09-12 (R-35 phase 1) — resolved via gelpack_root(),
# never as a subdir of this checkout.
for p in (ROOT / "InventoryReorder", gelpack_root(), ROOT / "ShippingReports", ROOT / "AppyHourMCP"):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))
