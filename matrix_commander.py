"""
Matrix Commander — Phase 1: Validate + Inventory Check + Shortage Report.

Standalone CLI tool that validates weekly production matrix XLSX files
and generates inventory cross-check/shortage reports.

Usage:
    python matrix_commander.py validate <xlsx_path>
    python matrix_commander.py check <xlsx_path> --inventory <csv_or_json>
    python matrix_commander.py full <xlsx_path> --inventory <csv_or_json>
"""

import argparse
import csv
import io
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import openpyxl

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Import NAME_TO_SKU from the MCP constants (single source of truth)
# ---------------------------------------------------------------------------
_CONSTANTS_DIR = Path(__file__).parent / "AppyHourMCP" / "tools"
sys.path.insert(0, str(_CONSTANTS_DIR))
from constants import NAME_TO_SKU, FOOD_PREFIXES  # noqa: E402

sys.path.pop(0)

# Reverse mapping: SKU → canonical product name
SKU_TO_NAME: dict[str, str] = {v: k for k, v in NAME_TO_SKU.items()}

# ---------------------------------------------------------------------------
# Substitution families — cheeses that can swap for each other
# ---------------------------------------------------------------------------
SUBSTITUTION_FAMILIES: dict[str, list[str]] = {
    "Brie": ["CH-TTBRIE", "CH-TIP", "CH-EBRIE", "CH-PBRIE", "CH-GPBRIE"],
    "Alpine / Semi-hard": ["CH-BARI"],
}

# Build reverse: SKU → family name
_SKU_TO_FAMILY: dict[str, str] = {}
for _fam, _skus in SUBSTITUTION_FAMILIES.items():
    for _s in _skus:
        _SKU_TO_FAMILY[_s] = _fam

# Non-pickable prefixes — not food, skip in demand counts
SKIP_PREFIXES = ("AHB-", "BL-", "PK-", "TR-", "EX-", "PR-CJAM", "CEX-E")

# Settings JSON path (inventory + curation config)
SETTINGS_PATH = Path(__file__).parent / "InventoryReorder" / "dist" / "inventory_reorder_settings.json"

# MFG translations CSV (exported from RMFG Translator portal)
MFG_TRANSLATIONS_PATH = Path(__file__).parent / "mfg_translations.csv"


# ═══════════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class OrderRow:
    """One row from the production matrix."""

    order_id: str
    name: str
    distribution_type: str
    total: str
    phone: str
    email: str
    address: str
    address2: str
    city: str
    state: str
    zip_code: str
    tags: str
    notes: str
    production_day: str
    assignments: dict[str, int]  # sku → qty


@dataclass
class CheckResult:
    """Result of a single validation check."""

    name: str
    passed: bool
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class ShortageItem:
    """One SKU that is short."""

    sku: str
    product_name: str
    demand: int
    available: int
    shortage: int
    family: str
    swap_candidates: list[tuple[str, int]]  # (sku, available_qty)


# ═══════════════════════════════════════════════════════════════════════════
# XLSX parser
# ═══════════════════════════════════════════════════════════════════════════


def parse_matrix(xlsx_path: str | Path) -> tuple[list[OrderRow], list[str], dict[str, str]]:
    """Parse the production matrix XLSX.

    Returns:
        (orders, product_columns, unmapped_names)
        - orders: list of OrderRow
        - product_columns: list of product column headers found
        - unmapped_names: {product_name: fallback_sku} for names not in NAME_TO_SKU
    """
    wb = openpyxl.load_workbook(str(xlsx_path), data_only=True, read_only=True)
    ws = wb["Access_LIVE"]

    # Read headers
    headers: list[str] = []
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        headers.append(str(cell.value or ""))

    # Identify product columns (index, product_name, sku)
    product_cols: list[tuple[int, str, str]] = []
    product_column_names: list[str] = []
    unmapped: dict[str, str] = {}

    for idx, hdr in enumerate(headers):
        if hdr.startswith("AHB") and ": " in hdr:
            prod_name = hdr.split(": ", 1)[1]
            product_column_names.append(prod_name)
            sku = NAME_TO_SKU.get(prod_name)
            if sku is None:
                fallback = f"??-{prod_name[:20]}"
                unmapped[prod_name] = fallback
                sku = fallback
            product_cols.append((idx, prod_name, sku))

    # Check for duplicate column names
    seen_cols: dict[str, int] = {}
    duplicate_cols: list[str] = []
    for _, prod_name, _ in product_cols:
        seen_cols[prod_name] = seen_cols.get(prod_name, 0) + 1
    for name, count in seen_cols.items():
        if count > 1:
            duplicate_cols.append(f"{name} (×{count})")

    # Parse data rows
    orders: list[OrderRow] = []
    for row in ws.iter_rows(min_row=2):
        cells = [cell.value for cell in row]
        if len(cells) < 14:
            continue
        order_id = str(cells[0] or "").strip()
        if not order_id:
            continue

        assignments: dict[str, int] = {}
        for col_idx, _, sku in product_cols:
            if col_idx < len(cells):
                val = cells[col_idx]
                if val is not None and str(val).strip() not in ("", "0", "None"):
                    try:
                        qty = int(float(str(val)))
                        if qty > 0:
                            assignments[sku] = assignments.get(sku, 0) + qty
                    except (ValueError, TypeError):
                        pass

        orders.append(
            OrderRow(
                order_id=order_id,
                name=str(cells[1] or ""),
                distribution_type=str(cells[2] or ""),
                total=str(cells[3] or ""),
                phone=str(cells[4] or ""),
                email=str(cells[5] or ""),
                address=str(cells[6] or ""),
                address2=str(cells[7] or ""),
                city=str(cells[8] or ""),
                state=str(cells[9] or ""),
                zip_code=str(cells[10] or ""),
                tags=str(cells[11] or ""),
                notes=str(cells[12] or ""),
                production_day=str(cells[13] or ""),
                assignments=assignments,
            )
        )

    wb.close()
    return orders, product_column_names, unmapped


# ═══════════════════════════════════════════════════════════════════════════
# Inventory loading
# ═══════════════════════════════════════════════════════════════════════════


def load_mfg_translations(csv_path: str | Path | None = None) -> dict[str, str]:
    """Load MFG translations: SKU -> MFG Name.

    CSV format (no header): SKU,"AHB (S_REG): Product Name"
    Exported from https://translator.robbinsmfginc.com/
    """
    path = Path(csv_path) if csv_path else MFG_TRANSLATIONS_PATH
    if not path.exists():
        return {}
    translations: dict[str, str] = {}
    with open(str(path), newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                sku = row[0].strip()
                mfg_name = row[1].strip()
                if sku:
                    translations[sku] = mfg_name
    return translations


def load_inventory_csv(csv_path: str | Path) -> dict[str, float]:
    """Load inventory from a simple CSV: sku,available_qty."""
    inventory: dict[str, float] = {}
    with open(str(csv_path), newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row.get("sku", "").strip()
            qty_str = row.get("available_qty", "0").strip()
            if sku:
                try:
                    inventory[sku] = float(qty_str)
                except ValueError:
                    inventory[sku] = 0.0
    return inventory


def load_inventory_settings() -> dict[str, float]:
    """Load inventory from the fulfillment app settings JSON."""
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        settings = json.load(f)
    raw = settings.get("inventory", {})
    return {sku: data.get("qty", 0.0) for sku, data in raw.items()}


def load_settings_config() -> dict:
    """Load PR-CJAM, CEX-EC, and splits config from settings."""
    if not SETTINGS_PATH.exists():
        return {}
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════
# Validation checks
# ═══════════════════════════════════════════════════════════════════════════


def check_numeric_order_ids(orders: list[OrderRow]) -> CheckResult:
    """Verify all OrderIDs are numeric (RMFG requirement)."""
    bad: list[str] = []
    for o in orders:
        cleaned = o.order_id.replace(",", "").replace(".", "").strip()
        if not cleaned.isdigit():
            bad.append(f"  #{o.order_id} ({o.name})")
    if bad:
        return CheckResult(
            "Numeric OrderIDs",
            False,
            f"{len(bad)} non-numeric OrderIDs found",
            bad[:20],
        )
    return CheckResult("Numeric OrderIDs", True, f"All {len(orders)} OrderIDs numeric")


def check_zip_leading_zeroes(orders: list[OrderRow]) -> CheckResult:
    """Verify zip codes preserve leading zeroes (5-digit minimum)."""
    bad: list[str] = []
    for o in orders:
        z = o.zip_code.strip()
        if not z:
            continue
        # Strip to digits only for check
        digits = z.split("-")[0]  # handle ZIP+4
        if digits.isdigit() and len(digits) < 5:
            bad.append(f"  #{o.order_id}: zip={z} ({o.city}, {o.state})")
    if bad:
        return CheckResult(
            "Zip Leading Zeroes",
            False,
            f"{len(bad)} zips missing leading zeroes",
            bad[:20],
        )
    return CheckResult("Zip Leading Zeroes", True, "All zips properly formatted")


def check_duplicate_columns(product_columns: list[str]) -> CheckResult:
    """Check for duplicate product column names."""
    counts = Counter(product_columns)
    dupes = [(name, cnt) for name, cnt in counts.items() if cnt > 1]
    if dupes:
        details = [f"  {name} (×{cnt})" for name, cnt in dupes]
        return CheckResult(
            "No Duplicate Columns",
            False,
            f"{len(dupes)} duplicate column(s) found",
            details,
        )
    return CheckResult(
        "No Duplicate Columns",
        True,
        f"{len(product_columns)} unique product columns",
    )


def check_production_day(orders: list[OrderRow]) -> CheckResult:
    """Verify ProductionDay is set to SAT or TUE for all orders."""
    valid_days = {"SAT", "TUE", "WED"}
    missing: list[str] = []
    invalid: list[str] = []
    day_counts: dict[str, int] = defaultdict(int)

    for o in orders:
        day = o.production_day.strip().upper()
        if not day:
            missing.append(f"  #{o.order_id} ({o.name})")
        elif day not in valid_days:
            invalid.append(f"  #{o.order_id}: '{o.production_day}'")
        else:
            day_counts[day] += 1

    if missing or invalid:
        details = []
        if missing:
            details.append(f"Missing ({len(missing)}):")
            details.extend(missing[:10])
        if invalid:
            details.append(f"Invalid ({len(invalid)}):")
            details.extend(invalid[:10])
        return CheckResult(
            "ProductionDay",
            False,
            f"{len(missing)} missing, {len(invalid)} invalid",
            details,
        )

    summary = ", ".join(f"{d}: {c}" for d, c in sorted(day_counts.items()))
    return CheckResult("ProductionDay", True, f"All set ({summary})")


def check_sku_mappings(unmapped: dict[str, str]) -> CheckResult:
    """Verify all product names map to known SKUs."""
    if unmapped:
        details = [f"  '{name}' → {fb}" for name, fb in sorted(unmapped.items())]
        return CheckResult(
            "SKU Mappings",
            False,
            f"{len(unmapped)} unmapped product name(s) — not in NAME_TO_SKU",
            details,
        )
    return CheckResult("SKU Mappings", True, "All product names mapped to SKUs")


def check_mfg_onboarding(
    orders: list[OrderRow],
    mfg_translations: dict[str, str],
) -> CheckResult:
    """Verify all allocated SKUs are onboarded at RMFG.

    Compares SKUs found in the matrix against the MFG translations export.
    Any SKU with demand that isn't in the translations file won't appear
    in the RMFG pick/pack output.
    """
    if not mfg_translations:
        return CheckResult(
            "MFG Onboarding",
            False,
            "No MFG translations file found — cannot validate. Export from https://translator.robbinsmfginc.com/",
        )

    # Collect all SKUs with actual demand
    demand_skus: set[str] = set()
    for o in orders:
        for sku in o.assignments:
            if any(sku.startswith(p) for p in ("CH-", "MT-", "AC-", "PK-")):
                demand_skus.add(sku)

    mfg_skus = set(mfg_translations.keys())
    missing = sorted(demand_skus - mfg_skus)

    if missing:
        details = []
        # Calculate demand for each missing SKU
        missing_demand: dict[str, int] = {}
        for o in orders:
            for sku, qty in o.assignments.items():
                if sku in missing:
                    missing_demand[sku] = missing_demand.get(sku, 0) + qty
        for sku in missing:
            name = SKU_TO_NAME.get(sku, "???")
            demand = missing_demand.get(sku, 0)
            details.append(f"  {sku} ({name}) — demand: {demand}")
        return CheckResult(
            "MFG Onboarding",
            False,
            f"{len(missing)} SKU(s) NOT onboarded at RMFG — must add before submission",
            details,
        )

    return CheckResult(
        "MFG Onboarding",
        True,
        f"All {len(demand_skus)} food/packaging SKUs onboarded at RMFG",
    )


def check_cexec_cheese_counts(
    orders: list[OrderRow],
    cex_ec_config: dict[str, str],
    cexec_splits: dict[str, dict[str, float]],
) -> CheckResult:
    """Verify CEX-EC parent line items have corresponding extra cheese allocated.

    For each order with a CEX-EC-{curation} in its tags or implied by assignments,
    check that the expected extra cheese SKU is present in the matrix assignments.
    """
    issues: list[str] = []
    checked = 0

    for o in orders:
        # Find CEX-EC curations from tags
        tags = [t.strip() for t in o.tags.split(",")]
        cexec_curations: list[str] = []
        for tag in tags:
            tag_upper = tag.upper()
            if tag_upper.startswith("CEXEC") or tag_upper.startswith("CEX-EC"):
                # Extract curation from tag like CEXEC.3.10.FIX or CEX-EC-MONG
                for cur in cex_ec_config:
                    if cur in tag_upper:
                        cexec_curations.append(cur)

        # Also check assignments for CEX-EC SKUs
        for sku in o.assignments:
            if sku.startswith("CEX-EC-"):
                cur = sku.replace("CEX-EC-", "")
                if cur not in cexec_curations:
                    cexec_curations.append(cur)

        if not cexec_curations:
            continue

        checked += 1
        assigned_cheeses = {s for s in o.assignments if s.startswith("CH-")}

        for cur in cexec_curations:
            expected_cheese = cex_ec_config.get(cur)
            if not expected_cheese:
                # Check splits
                splits = cexec_splits.get(cur, {})
                if splits:
                    for split_sku in splits:
                        if split_sku not in assigned_cheeses:
                            issues.append(f"  #{o.order_id}: CEX-EC-{cur} missing split cheese {split_sku}")
                continue
            if expected_cheese not in assigned_cheeses:
                # Check splits as fallback
                splits = cexec_splits.get(cur, {})
                split_present = any(s in assigned_cheeses for s in splits)
                if not split_present:
                    issues.append(
                        f"  #{o.order_id}: CEX-EC-{cur} expects {expected_cheese}, "
                        f"has [{', '.join(sorted(assigned_cheeses))}]"
                    )

    if issues:
        return CheckResult(
            "CEX-EC Cheese Allocation",
            False,
            f"{len(issues)} orders with missing CEX-EC cheese (checked {checked})",
            issues[:20],
        )
    return CheckResult(
        "CEX-EC Cheese Allocation",
        True,
        f"All {checked} CEX-EC orders have expected cheese allocated",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Inventory cross-check & shortage report
# ═══════════════════════════════════════════════════════════════════════════


def compute_demand(orders: list[OrderRow]) -> dict[str, int]:
    """Sum total demand per SKU across all orders."""
    demand: dict[str, int] = defaultdict(int)
    for o in orders:
        for sku, qty in o.assignments.items():
            demand[sku] += qty
    return dict(demand)


def find_shortages(
    demand: dict[str, int],
    inventory: dict[str, float],
) -> list[ShortageItem]:
    """Compare demand vs inventory, return shortage items with swap suggestions."""
    shortages: list[ShortageItem] = []

    # Only check food SKUs
    food_demand = {sku: qty for sku, qty in demand.items() if any(sku.startswith(p) for p in ("CH-", "MT-", "AC-"))}

    for sku, qty_needed in sorted(food_demand.items()):
        available = inventory.get(sku, 0.0)
        if qty_needed > available:
            shortage = qty_needed - int(available)
            family = _SKU_TO_FAMILY.get(sku, "")

            # Find swap candidates from same family
            candidates: list[tuple[str, int]] = []
            if family:
                family_skus = SUBSTITUTION_FAMILIES.get(family, [])
                for alt_sku in family_skus:
                    if alt_sku == sku:
                        continue
                    alt_avail = inventory.get(alt_sku, 0.0)
                    alt_demand = food_demand.get(alt_sku, 0)
                    surplus = int(alt_avail) - alt_demand
                    if surplus > 0:
                        candidates.append((alt_sku, surplus))
                candidates.sort(key=lambda x: x[1], reverse=True)

            shortages.append(
                ShortageItem(
                    sku=sku,
                    product_name=SKU_TO_NAME.get(sku, sku),
                    demand=qty_needed,
                    available=int(available),
                    shortage=shortage,
                    family=family,
                    swap_candidates=candidates,
                )
            )

    return shortages


# ═══════════════════════════════════════════════════════════════════════════
# Output formatting
# ═══════════════════════════════════════════════════════════════════════════

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _check_icon(passed: bool) -> str:
    return f"{_GREEN}PASS{_RESET}" if passed else f"{_RED}FAIL{_RESET}"


def print_validation_report(results: list[CheckResult], order_count: int) -> bool:
    """Print validation results. Returns True if all passed."""
    print(f"\n{_BOLD}{'=' * 60}{_RESET}")
    print(f"{_BOLD}  MATRIX COMMANDER — Validation Report{_RESET}")
    print(f"{_BOLD}{'=' * 60}{_RESET}")
    print(f"  Orders parsed: {order_count}\n")

    all_passed = True
    for r in results:
        icon = _check_icon(r.passed)
        print(f"  [{icon}] {r.name}: {r.message}")
        if not r.passed:
            all_passed = False
            for d in r.details:
                print(f"       {d}")
            print()

    print(f"{_BOLD}{'=' * 60}{_RESET}")
    if all_passed:
        print(f"  {_GREEN}All checks passed.{_RESET}")
    else:
        failed = sum(1 for r in results if not r.passed)
        print(f"  {_RED}{failed} check(s) failed.{_RESET}")
    print(f"{_BOLD}{'=' * 60}{_RESET}\n")
    return all_passed


def print_inventory_report(
    demand: dict[str, int],
    inventory: dict[str, float],
    shortages: list[ShortageItem],
) -> None:
    """Print inventory cross-check and shortage report."""
    food_demand = {sku: qty for sku, qty in demand.items() if any(sku.startswith(p) for p in ("CH-", "MT-", "AC-"))}

    print(f"\n{_BOLD}{'=' * 60}{_RESET}")
    print(f"{_BOLD}  MATRIX COMMANDER — Inventory Report{_RESET}")
    print(f"{_BOLD}{'=' * 60}{_RESET}")
    print(f"  SKUs in demand: {len(food_demand)}")
    print(f"  SKUs in inventory: {len(inventory)}")
    print()

    # Full demand table
    print(f"  {_BOLD}{'SKU':<14} {'Demand':>7} {'Avail':>7} {'Net':>7}  Status{_RESET}")
    print(f"  {'─' * 52}")

    for sku in sorted(food_demand.keys()):
        qty = food_demand[sku]
        avail = int(inventory.get(sku, 0))
        net = avail - qty
        if net < 0:
            status = f"{_RED}SHORT {abs(net)}{_RESET}"
        elif net < 20:
            status = f"{_YELLOW}LOW{_RESET}"
        else:
            status = f"{_GREEN}OK (+{net}){_RESET}"
        print(f"  {sku:<14} {qty:>7} {avail:>7} {net:>7}  {status}")

    # Missing from inventory
    missing_inv = [s for s in food_demand if s not in inventory]
    if missing_inv:
        print(f"\n  {_YELLOW}SKUs with demand but NOT in inventory:{_RESET}")
        for s in sorted(missing_inv):
            print(f"    {s} (demand: {food_demand[s]})")

    # Shortage details with swap recommendations
    if shortages:
        print(f"\n{_BOLD}{'=' * 60}{_RESET}")
        print(f"{_BOLD}  SHORTAGES — {len(shortages)} SKU(s){_RESET}")
        print(f"{_BOLD}{'=' * 60}{_RESET}")

        for s in shortages:
            print(f"\n  {_RED}{s.sku}{_RESET} ({s.product_name})")
            print(f"    Demand: {s.demand}  Available: {s.available}  Short: {s.shortage}")
            if s.family:
                print(f"    Family: {s.family}")
            if s.swap_candidates:
                print(f"    {_CYAN}Swap candidates:{_RESET}")
                for alt_sku, surplus in s.swap_candidates:
                    alt_name = SKU_TO_NAME.get(alt_sku, alt_sku)
                    print(f"      → {alt_sku} ({alt_name}): {surplus} surplus")
            elif s.family:
                print(f"    {_YELLOW}No swap candidates with surplus in {s.family} family{_RESET}")
            else:
                print(f"    {_YELLOW}No substitution family defined for this SKU{_RESET}")
    else:
        print(f"\n  {_GREEN}No shortages detected.{_RESET}")

    print(f"\n{_BOLD}{'=' * 60}{_RESET}\n")


def print_demand_summary(demand: dict[str, int]) -> None:
    """Print demand summary CSV-like output for cross-checking."""
    food_demand = {sku: qty for sku, qty in demand.items() if any(sku.startswith(p) for p in ("CH-", "MT-", "AC-"))}
    print(f"\n{_BOLD}  Demand Summary (food SKUs only){_RESET}")
    print(f"  {'SKU':<14} {'Name':<45} {'Qty':>5}")
    print(f"  {'─' * 66}")
    for sku in sorted(food_demand.keys()):
        name = SKU_TO_NAME.get(sku, "???")
        print(f"  {sku:<14} {name:<45} {food_demand[sku]:>5}")
    total = sum(food_demand.values())
    print(f"  {'─' * 66}")
    print(f"  {'TOTAL':<14} {'':<45} {total:>5}")


# ═══════════════════════════════════════════════════════════════════════════
# CLI commands
# ═══════════════════════════════════════════════════════════════════════════


def cmd_validate(xlsx_path: str) -> bool:
    """Run all validation checks on the XLSX."""
    print(f"  Loading {Path(xlsx_path).name}...")
    orders, product_columns, unmapped = parse_matrix(xlsx_path)

    # Load settings for CEX-EC config
    settings = load_settings_config()
    cex_ec = settings.get("cex_ec", {})
    cexec_splits = settings.get("cexec_splits", {})

    # Load MFG translations for onboarding check
    mfg_translations = load_mfg_translations()

    results = [
        check_numeric_order_ids(orders),
        check_zip_leading_zeroes(orders),
        check_duplicate_columns(product_columns),
        check_production_day(orders),
        check_sku_mappings(unmapped),
        check_mfg_onboarding(orders, mfg_translations),
        check_cexec_cheese_counts(orders, cex_ec, cexec_splits),
    ]

    all_passed = print_validation_report(results, len(orders))

    # Also print demand summary
    demand = compute_demand(orders)
    print_demand_summary(demand)

    return all_passed


def cmd_check(xlsx_path: str, inventory_path: Optional[str] = None) -> bool:
    """Run inventory cross-check and shortage report."""
    print(f"  Loading {Path(xlsx_path).name}...")
    orders, _, _ = parse_matrix(xlsx_path)

    # Load inventory
    if inventory_path:
        p = Path(inventory_path)
        if p.suffix == ".json":
            print(f"  Loading inventory from {p.name} (JSON)...")
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            if "inventory" in raw:
                inventory = {sku: d.get("qty", 0) for sku, d in raw["inventory"].items()}
            else:
                inventory = {k: v for k, v in raw.items()}
        else:
            print(f"  Loading inventory from {p.name} (CSV)...")
            inventory = load_inventory_csv(p)
    else:
        print("  Loading inventory from settings JSON...")
        inventory = load_inventory_settings()

    if not inventory:
        print(f"  {_RED}No inventory data loaded! Provide --inventory or check settings.{_RESET}")
        return False

    print(f"  Loaded {len(inventory)} SKUs from inventory source.")

    demand = compute_demand(orders)
    shortages = find_shortages(demand, inventory)
    print_inventory_report(demand, inventory, shortages)

    return len(shortages) == 0


def cmd_full(xlsx_path: str, inventory_path: Optional[str] = None) -> bool:
    """Run full Phase 1: validate + inventory check."""
    print(f"\n{_BOLD}{'#' * 60}{_RESET}")
    print(f"{_BOLD}  MATRIX COMMANDER — Full Phase 1 Pipeline{_RESET}")
    print(f"{_BOLD}{'#' * 60}{_RESET}\n")

    # Step 1: Validate
    print(f"  {_CYAN}[Step 1/2] Validation{_RESET}")
    print(f"  Loading {Path(xlsx_path).name}...")
    orders, product_columns, unmapped = parse_matrix(xlsx_path)

    settings = load_settings_config()
    cex_ec = settings.get("cex_ec", {})
    cexec_splits = settings.get("cexec_splits", {})
    mfg_translations = load_mfg_translations()

    results = [
        check_numeric_order_ids(orders),
        check_zip_leading_zeroes(orders),
        check_duplicate_columns(product_columns),
        check_production_day(orders),
        check_sku_mappings(unmapped),
        check_mfg_onboarding(orders, mfg_translations),
        check_cexec_cheese_counts(orders, cex_ec, cexec_splits),
    ]

    validation_passed = print_validation_report(results, len(orders))

    # Step 2: Inventory check
    print(f"  {_CYAN}[Step 2/2] Inventory Cross-Check{_RESET}")

    if inventory_path:
        p = Path(inventory_path)
        if p.suffix == ".json":
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            if "inventory" in raw:
                inventory = {sku: d.get("qty", 0) for sku, d in raw["inventory"].items()}
            else:
                inventory = {k: v for k, v in raw.items()}
        else:
            inventory = load_inventory_csv(p)
    else:
        inventory = load_inventory_settings()

    if inventory:
        print(f"  Loaded {len(inventory)} SKUs from inventory source.")
        demand = compute_demand(orders)
        shortages = find_shortages(demand, inventory)
        print_inventory_report(demand, inventory, shortages)
        inventory_ok = len(shortages) == 0
    else:
        print(f"  {_YELLOW}No inventory data — skipping cross-check.{_RESET}")
        demand = compute_demand(orders)
        print_demand_summary(demand)
        inventory_ok = True

    # Final summary
    print(f"\n{_BOLD}{'#' * 60}{_RESET}")
    if validation_passed and inventory_ok:
        print(f"  {_GREEN}READY — All checks passed, no shortages.{_RESET}")
    elif validation_passed and not inventory_ok:
        print(f"  {_YELLOW}REVIEW — Validation passed but shortages found.{_RESET}")
    else:
        print(f"  {_RED}BLOCKED — Validation failures must be resolved.{_RESET}")
    print(f"{_BOLD}{'#' * 60}{_RESET}\n")

    return validation_passed and inventory_ok


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="matrix_commander",
        description="Matrix Commander — Validate production matrix & check inventory.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    p_val = sub.add_parser("validate", help="Run QC validation checks on XLSX")
    p_val.add_argument("xlsx", help="Path to AHB_WeeklyProductionQuery XLSX file")

    # check
    p_chk = sub.add_parser("check", help="Run inventory cross-check & shortage report")
    p_chk.add_argument("xlsx", help="Path to AHB_WeeklyProductionQuery XLSX file")
    p_chk.add_argument("--inventory", "-i", help="Inventory CSV (sku,available_qty) or JSON path")

    # full
    p_full = sub.add_parser("full", help="Run full pipeline: validate + inventory check")
    p_full.add_argument("xlsx", help="Path to AHB_WeeklyProductionQuery XLSX file")
    p_full.add_argument("--inventory", "-i", help="Inventory CSV (sku,available_qty) or JSON path")

    args = parser.parse_args()

    if args.command == "validate":
        ok = cmd_validate(args.xlsx)
    elif args.command == "check":
        ok = cmd_check(args.xlsx, getattr(args, "inventory", None))
    elif args.command == "full":
        ok = cmd_full(args.xlsx, getattr(args, "inventory", None))
    else:
        parser.print_help()
        return

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
