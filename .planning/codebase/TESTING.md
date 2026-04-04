# Testing Patterns

**Analysis Date:** 2026-04-04

## Test Framework

**Runner:**
- pytest (version >=8.0 in `pyproject.toml` line 26)
- Config: `pyproject.toml` → `tool.pytest.ini_options` (lines 32-35)
- Test paths: `tests/` directory
- Python path: `.` (allows importing from root)

**Assertion Library:**
- pytest's built-in `assert` statements
- `pytest.approx()` for floating-point comparisons

**Run Commands:**
```bash
pytest                    # Run all tests in tests/ directory
pytest --cov              # Run with coverage report (pytest-cov)
pytest tests/test_*.py    # Run specific test file
pytest -v                 # Verbose output (default with -v in addopts)
pytest --tb=short         # Short traceback format (configured default)
```

## Test File Organization

**Location:**
- Co-located in `tests/` directory at root level
- Mirrors module structure: `appyhour/thermal.py` → `tests/test_thermal.py`
- Additional tests: `tests/test_error_detection.py`, `tests/test_routing_tags.py`, `tests/test_weekly_cycle_e2e.py`

**Naming:**
- `test_*.py` prefix (pytest auto-discovery)
- Module under test name in filename: `test_cut_order_helpers.py`, `test_reorder.py`, `test_shipping.py`

**Structure:**
```
tests/
├── conftest.py                          # Shared fixtures (root sys.path setup)
├── test_cut_order_helpers.py            # Pure function tests
├── test_error_detection.py              # Error detection logic
├── test_reorder.py                      # Reorder calculations
├── test_shipping.py                     # Shipping analytics
├── test_thermal.py                      # Thermal analysis
├── test_routing_tags.py                 # Routing/tag logic
└── test_weekly_cycle_e2e.py             # End-to-end fulfillment cycle
```

## Test Structure

**Suite Organization** (from `tests/test_cut_order_helpers.py`):
```python
class TestNormalizeSku:
    def test_uppercases_sku(self):
        assert normalize_sku("ch-mcpc") == "CH-MCPC"
    
    def test_applies_equiv_mapping(self):
        assert normalize_sku("ch-brie") == "CH-EBRIE"

class TestIsPickable:
    @pytest.mark.parametrize("sku", ["CH-MCPC", "CH-BLR", "MT-LONZ", "AC-DTCH"])
    def test_food_items_are_pickable(self, sku):
        assert is_pickable(sku) is True
```

**Patterns:**
- Group tests by function name using `class Test[FunctionName]:`
- One test per behavior (not one test per function)
- Use `@pytest.mark.parametrize()` for multiple input scenarios
- Docstring-style test names are descriptive

## Fixtures

**Root conftest.py** (`conftest.py` lines 1-12):
```python
"""Root conftest — shared fixtures for AppyHour test suite."""

import sys
from pathlib import Path

# Ensure subpackages are importable without install
ROOT = Path(__file__).parent
for subdir in ("InventoryReorder", "GelPackCalculator", "ShippingReports", "AppyHourMCP"):
    p = ROOT / subdir
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))
```

**Pattern: Module mocking at import time** (from `test_error_detection.py` lines 7-44):
```python
import json
from unittest.mock import mock_open, patch

# Mock settings before importing the module
_MOCK_SETTINGS = {
    "shopify_store_url": "test-store",
    "shopify_access_token": "test-token",
    "curation_recipes": {...},
    ...
}

@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Patch the settings file read so find_new_errors can import."""
    pass  # Settings are patched at module level below

# Patch the open() call and import the module
with patch("builtins.open", mock_open(read_data=json.dumps(_MOCK_SETTINGS))):
    import find_new_errors as fne
```

## Mocking

**Framework:** 
- `unittest.mock` (standard library)
- `pytest.mark.parametrize()` for input variants instead of fixtures

**Patterns:**

1. **Patch module-level imports** (from `test_error_detection.py`):
```python
with patch("builtins.open", mock_open(read_data=json.dumps(_MOCK_SETTINGS))):
    import find_new_errors as fne
```

2. **Parametrize repeated test cases** (from `test_cut_order_helpers.py` lines 34-49):
```python
@pytest.mark.parametrize("sku", ["CH-MCPC", "CH-BLR", "MT-LONZ", "AC-DTCH"])
def test_food_items_are_pickable(self, sku):
    assert is_pickable(sku) is True

@pytest.mark.parametrize("sku,reason", [
    ("AHB-MED", "box SKU"),
    ("AHB-MCUST-MONG", "custom box SKU"),
    ("BL-BASIC", "bulk item"),
    ...
])
def test_non_pickable_prefixes(self, sku, reason):
    assert is_pickable(sku) is False, f"Expected {sku} ({reason}) to be non-pickable"
```

**What to Mock:**
- Settings/config JSON files (use `mock_open` + `patch`)
- API calls (requests module) when testing logic that depends on external data
- File I/O (when testing data transformation)

**What NOT to Mock:**
- Pure utility functions (test them directly with assert)
- Domain model functions (thermal, reorder, shipping calculations)
- Standard library functions (`dict.get`, `list.append`, etc.)

## Fixtures and Factories

**Test Data:**
- Literal dicts/lists in test methods: `bundle_map = {"AHB-MED": [("CH-MCPC", 1), ("MT-LONZ", 1)]}`
- Mock settings dict at module level (from `test_error_detection.py` lines 23-33):
```python
_MOCK_SETTINGS = {
    "shopify_store_url": "test-store",
    "curation_recipes": {
        "MONG": [("CH-BLR", 1), ("CH-WWDI", 1), ("MT-LONZ", 1), ("AC-DTCH", 1), ("CH-MCPC", 1)],
        "MDT": [("CH-MCPC", 1), ("CH-MSMG", 1), ("MT-TUSC", 1), ("AC-PRPE", 1), ("CH-TTBRIE", 1)],
    },
}
```

**Location:**
- Module-level constants: `_MOCK_SETTINGS`, `bundle_map = {...}`
- No separate fixtures/ directory; data lives inline in test methods

## Coverage

**Requirements:** 
- Target: 80%+ coverage (mentioned in CLAUDE.md)
- Tool: pytest-cov (version >=5.0 in `pyproject.toml`)

**View Coverage:**
```bash
pytest --cov=appyhour --cov-report=term-missing
pytest --cov=appyhour --cov-report=html  # Generates htmlcov/
```

## Test Types

**Unit Tests:**
- Scope: Single pure function
- Approach: Assert on return value, no external dependencies
- Examples: `test_reorder.py`, `test_thermal.py`, `test_shipping.py` — all test pure functions from `appyhour/`
- Typical: 3-5 lines per test

**Integration Tests:**
- Scope: Multiple functions or module + mocked API
- Approach: Mock external services, test data flow through module
- Example: `test_error_detection.py` — mocks settings, imports module, tests `analyze_order()` orchestrator
- Typical: 10-20 lines per test

**E2E Tests:**
- Scope: Full fulfillment cycle or order flow
- Approach: Mock Shopify/Recharge APIs, test weekly workflow
- Framework: pytest (not Playwright; that's for web app)
- Location: `tests/test_weekly_cycle_e2e.py` (not yet fully explored, but structured like other tests)

## Common Patterns

**Async Testing:**
- Not currently used (AppyHour is synchronous)
- If needed: `@pytest.mark.asyncio` decorator with `async def test_*()`

**Error Testing** (from `test_cut_order_helpers.py` lines 24-25, 51-55):
```python
def test_returns_falsy_input_unchanged(self):
    assert normalize_sku(None) is None
    assert normalize_sku("") == ""

def test_empty_string_is_not_pickable(self):
    assert is_pickable("") is False

def test_whitespace_only_is_not_pickable(self):
    assert is_pickable("   ") is False
```

**Approximate Comparisons** (from `test_reorder.py` lines 18, 21, 53):
```python
def test_basic_calculation(self):
    # 10 units/day * 14 days lead + 20 safety = 160
    assert calculate_reorder_point(10, 14, 20) == pytest.approx(160.0)

def test_five_percent_churn(self):
    assert apply_churn_rate(100, 5) == pytest.approx(95.0)
```

**Boundary Testing** (from `test_shipping.py` lines 49-68):
```python
def test_within_service_level(self):
    assert is_on_time(2, "2-Day") is True

def test_exceeds_service_level(self):
    assert is_on_time(3, "2-Day") is False

def test_exact_match(self):
    assert is_on_time(1, "1-Day") is True

def test_unknown_service_defaults_3day(self):
    assert is_on_time(3, "Unknown") is True
    assert is_on_time(4, "Unknown") is False
```

## Test Running in CI/CD

**pytest Configuration** (from `pyproject.toml` lines 32-35):
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-v --tb=short"
```

**Default Options:**
- `-v`: Verbose output (shows each test name)
- `--tb=short`: Short traceback format (less noise)

**Typical CI command:**
```bash
pytest --cov=appyhour --cov-report=term-missing
```

---

*Testing analysis: 2026-04-04*
