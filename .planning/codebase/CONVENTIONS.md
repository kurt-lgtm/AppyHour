# Coding Conventions

**Analysis Date:** 2026-04-04

## Naming Patterns

**Files:**
- Lowercase with underscores: `cut_order_generator.py`, `inventory_reorder.py`, `thermal.py`
- Module files in subdirectories follow same pattern: `tools/google_sheets.py`, `tools/shipping.py`
- Test files: `test_*.py` (e.g., `test_error_detection.py`, `test_reorder.py`)

**Functions:**
- Lowercase with underscores: `calculate_reorder_point()`, `decompose_bundles()`, `analyze_order()`
- Pure helper functions use imperative names: `is_pickable()`, `normalize_sku()`, `apply_churn_rate()`
- Private/internal functions prefixed with underscore: `_load_shipments()`, `_get_analyze()`

**Variables:**
- Lowercase with underscores: `daily_usage`, `reorder_point`, `bundle_map`
- Constants uppercase: `HEAT_CAPACITY`, `TARGET_TEMP_DEFAULT`, `GEL_CONFIGS`, `PICKABLE_PREFIXES`
- Dictionary/dict keys use lowercase: `config["btu"]`, `result["risk"]`, `settings.get("recharge_api_token")`

**Types:**
- Classes use PascalCase when present: `CostAnalysisInput`, `TransitAnalysisInput` (Pydantic models in `AppyHourMCP/tools/`)
- Enum members uppercase: `GroupByChoice.STATE`, `GroupByChoice.CARRIER`

**Boolean Functions:**
- Prefix with `is_` or `compute_`: `is_pickable()`, `is_on_time()`, `compute_reorder_status()`, `compute_wheel_supply()`

## Code Style

**Formatting:**
- Line length: 120 characters (configured in `pyproject.toml` → `tool.ruff.line-length`)
- Indentation: 4 spaces
- Use `from __future__ import annotations` for forward-compatible type hints

**Linting:**
- Tool: `ruff` (configured in `pyproject.toml`)
- Enabled rules: E, W, F, I (isort), UP (pyupgrade), B (flake8-bugbear), S (security), SIM (simplify)
- Ignored: E501 (line length handled by formatter), S101/S105/S106 (test and security noise)
- Per-file: tests/ directory ignores S101, S106, E402

**Type Checking:**
- Tool: `pyright` (basic mode)
- Python version: 3.10+
- Type hints expected on function signatures, especially public APIs
- Lazy-load types with `types.ModuleType | None` (see `tools/shipping.py` line 21)

## Import Organization

**Order:**
1. Standard library (sys, json, time, pathlib, datetime, collections, enum)
2. Third-party (requests, pydantic, openpyxl, pyyaml)
3. Local/relative imports (from appyhour, from cut_order_generator, from utils)

**Example pattern** (from `tools/shipping.py`):
```python
import json
import time
import re
import types
from pathlib import Path
from datetime import datetime, timedelta

from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum

import requests

from utils import format_error, to_json, SHIPPING_DIR, GELCALC_DIR, get_inventory_settings
```

**Path Aliases:**
- Not used; relative imports and explicit sys.path manipulation preferred (see `conftest.py` lines 8-11)
- Root path management: `BASE = os.path.dirname(os.path.abspath(__file__))`

## Error Handling

**Pattern: Return empty/safe values on missing data**
```python
# From build_cut_order_xlsx.py line 56-58
try:
    inventory = load_inventory_csv(INV_CSV)
except (KeyError, ValueError):
    inventory = {}  # Graceful fallback
```

**Pattern: Use dict.get() with defaults**
```python
# From shipping.py line 47
def gel_pack_recommendation(..., box: dict | None = None, ...):
    box = box or DEFAULT_BOX  # Default mutable argument
```

**Pattern: Specific exception handling (not bare except)**
```python
# From build_cut_order_xlsx.py line 70-73
try:
    inventory[sku] = int(float(row[avail_col] or 0))
except (ValueError, IndexError):
    pass  # Skip invalid rows, don't crash
```

**Pattern: Optional fields with None checks**
```python
# From thermal.py line 47
if target_temp is None:
    target_temp = TARGET_TEMP_DEFAULT
# From shipping.py line 92
if temp is not None and temp > threshold_temp:
    return False
```

**Logging:**
- Tool: `print()` for CLI scripts (e.g., `build_cut_order_xlsx.py` lines 53, 76, 89, 102)
- No centralized logging library; simple print statements for diagnostic output
- Error messages sent as returned strings in MCP tools: `return format_error(str(e))`

## Comments

**When to Comment:**
- Module docstring at top of file describing purpose (required)
- Function docstring explaining what it returns and key logic (for public APIs)
- Inline comments for non-obvious calculations or domain logic (e.g., `# 5 lbs * 10 wheels * 2.67 = 133.5 slices`)
- Section comments with `# ── [Section Name] ────...` separator (from `thermal.py` line 9)

**JSDoc/TSDoc:**
- Python docstrings used (triple quotes), not elaborate TypeDoc format
- Docstrings include description + return type + example domain context
- Example from `thermal.py` lines 65-82:
```python
def analyze_order(
    outside_temp: float,
    transit_type: str,
    ...
) -> dict:
    """Analyze gel pack needs for an order.

    If origin_temp is provided, first half of transit uses origin_temp,
    second half uses outside_temp (destination).
    """
```

## Function Design

**Size:**
- Small, focused functions (most under 30 lines)
- Complex business logic extracted to pure functions in appyhour/ (e.g., `thermal.py`, `reorder.py`, `shipping.py`)
- GUI code and business logic strictly separated

**Parameters:**
- Positional for required, keyword for optional
- Use `dict | None = None` pattern with fallback, not `**kwargs`
- Type hints on all parameters (enforced by pyright basic mode)

**Return Values:**
- Return dict for complex results (e.g., `analyze_order()` returns dict with 13+ keys)
- Return list/tuples for collections
- Return str for status/enums (e.g., `"OUT_OF_STOCK"`, `"CRITICAL"`)
- Return bool for flags
- None for void operations or missing data (handled with `dict.get(key, default)`)

## Module Design

**Exports:**
- Pure functions in `appyhour/` are public (no underscore prefix)
- MCP tools use Pydantic models for input validation: `class CostAnalysisInput(BaseModel)`
- Internal helpers prefixed with underscore: `_load_shipments()`, `_get_analyze()`

**Barrel Files:**
- Not used; modules imported directly or via explicit imports

**Architecture Layers:**
- **Pure Logic Layer** (`appyhour/thermal.py`, `appyhour/reorder.py`, `appyhour/shipping.py`): No API/GUI dependencies, testable directly
- **Integration Layer** (`AppyHourMCP/tools/`): Pydantic models, MCP registration, API calls
- **CLI Layer** (`InventoryReorder/build_cut_order_xlsx.py`): Direct imports from integration, print diagnostics
- **Test Layer** (`tests/`): Tests pure functions and helpers, mocks API calls

## Constants and Defaults

**Physical Constants** (from `thermal.py`):
```python
HEAT_CAPACITY = 35.7  # BTU/F
TARGET_TEMP_DEFAULT = 50.0  # F
MELT_EFFICIENCY = 0.90
GEL_CONFIGS = [...]  # List of dicts with structure
```

**Default Dicts** (from `shipping.py`):
```python
DEFAULT_BOX = {"l": 13, "w": 10, "h": 10}
DEFAULT_INSULATION = {"r_per_inch": 3.5, "thickness": 1.5, "r_air_film": 0.365}
DEFAULT_HUB_HOURS = {"1-Day": 4, "2-Day": 6, "3-Day": 8}
```

**Pattern: Mutable defaults in functions**
```python
def function(..., box: dict | None = None):
    box = box or DEFAULT_BOX  # Never use dict | None = {} in signature
```

## Pydantic Models

**Location:** `AppyHourMCP/tools/` (e.g., `shipping.py` lines 66-95)

**Pattern:**
```python
from pydantic import BaseModel, Field, ConfigDict, field_validator

class CostAnalysisInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    group_by: GroupByChoice = Field(GroupByChoice.STATE, description="...")
    
    @field_validator("group_by")
    @classmethod
    def validate_group_by(cls, v: str) -> str:
        allowed = {"state", "carrier", "hub"}
        if v not in allowed:
            raise ValueError(f"group_by must be one of: {', '.join(allowed)}")
        return v
```

---

*Convention analysis: 2026-04-04*
