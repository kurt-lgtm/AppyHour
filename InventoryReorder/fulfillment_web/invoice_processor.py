"""
Invoice Processor — RMFG Production Invoice PDF parsing, SKU matching,
Gmail IMAP polling, and reconciliation engine.
"""
from __future__ import annotations

import re
import imaplib
import email
import difflib
from email.header import decode_header
from datetime import datetime, date


# ── PDF Parsing ──────────────────────────────────────────────────────

def extract_invoice_id(filename: str) -> str:
    """Extract 'AHB_00254' from 'AHB_00254_Product Production Breakdown.pdf'."""
    m = re.match(r'(AHB_\d+)', filename)
    return m.group(1) if m else filename.replace('.pdf', '')


def parse_production_invoice(pdf_bytes: bytes) -> dict:
    """Parse an RMFG production invoice PDF into structured data."""
    import pdfplumber

    result = {
        "full_mfg": [],
        "meals": [],
        "label_only": [],
        "full_mfg_totals": {"cases": 0, "yield": 0, "charge": 0.0},
        "meals_totals": {"cases": 0, "yield": 0, "charge": 0.0},
        "label_only_totals": {"cases": 0, "yield": 0, "charge": 0.0},
        "total_production_charge": 0.0,
        "mfg_date": None,
        "parse_method": "table",
    }

    try:
        import io
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as e:
        result["error"] = f"Failed to open PDF: {e}"
        result["parse_method"] = "error"
        return result

    for page in pdf.pages:
        # Try table extraction first
        tables = page.extract_tables()
        if tables and len(tables) > 0:
            result = _parse_from_tables(tables, result)
        else:
            # Fallback: text + regex
            text = page.extract_text() or ""
            result = _parse_from_text(text, result)
            result["parse_method"] = "text"

    pdf.close()
    return result


def _parse_dollar(val: str) -> float:
    """Parse '$3,853.81' or '$ 3 ,853.81' into float.
    pdfplumber often inserts spaces between digits."""
    if not val:
        return 0.0
    # Remove $, commas, spaces (pdfplumber artifacts)
    cleaned = re.sub(r'[$,]', '', str(val))
    # Remove spaces between digits/dots: "3 853.81" -> "3853.81"
    cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)
    cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)  # second pass for "3 8 53"
    cleaned = cleaned.strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def _parse_int(val) -> int:
    """Parse '1,635' or '1 ,635' or '7 3' into int.
    pdfplumber often splits digits with spaces."""
    if val is None:
        return 0
    cleaned = re.sub(r'[,]', '', str(val))
    # Remove spaces between digits: "7 3" -> "73", "1 96" -> "196"
    cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)
    cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)  # second pass
    cleaned = cleaned.strip()
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return 0


def _parse_date(val: str) -> str | None:
    """Parse '3/1/2026' into '2026-03-01'."""
    if not val:
        return None
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(val.strip(), fmt).date().isoformat()
        except (ValueError, TypeError):
            continue
    return None


def _parse_from_tables(tables: list, result: dict) -> dict:
    """Parse structured table data from pdfplumber tables.

    The PDF typically produces 7 tables:
      0: FULL MFG PRODUCTION LOG (line items)
      1: MEALS PRODUCTION LOG (usually empty)
      2: LABEL ONLY PRODUCTION LOG (line items)
      3: PACKAGING TOTALS (summary, skip if we have table 0)
      4: MEALS TOTALS row
      5: LABEL ONLY items + LABEL ONLY TOTALS row
      6: TOTAL PRODUCTION CHARGE
    """
    mfg_date_found = None

    for table in tables:
        if not table:
            continue
        rows = [[str(c).strip() if c else '' for c in row] for row in table]
        if not rows:
            continue

        # Identify table by first row header
        header_text = ' '.join(rows[0]).upper().strip()

        if 'FULL MFG PRODUCTION LOG' in header_text:
            for row in rows[2:]:  # skip header + column names
                item = _parse_table_line(row, 'full_mfg', has_cases=True)
                if item and item.get("product_name"):
                    if item.get("mfg_date") and not mfg_date_found:
                        mfg_date_found = item["mfg_date"]
                    result["full_mfg"].append(item)

        elif 'MEALS PRODUCTION LOG' in header_text:
            for row in rows[2:]:
                item = _parse_table_line(row, 'meals', has_cases=True)
                if item and item.get("product_name"):
                    if item.get("mfg_date") and not mfg_date_found:
                        mfg_date_found = item["mfg_date"]
                    result["meals"].append(item)

        elif 'LABEL ONLY PRODUCTION LOG' in header_text:
            for row in rows[2:]:
                item = _parse_table_line(row, 'label_only', has_cases=False)
                if item and item.get("product_name"):
                    if item.get("mfg_date") and not mfg_date_found:
                        mfg_date_found = item["mfg_date"]
                    result["label_only"].append(item)

        elif 'PACKAGING TOTALS' in header_text:
            # Skip packaging totals — it duplicates full_mfg items
            # But grab the FULL MFG TOTALS row if present
            for row in rows:
                text = ' '.join(row)
                if 'FULL MFG TOTALS' in text.upper():
                    result["full_mfg_totals"] = _extract_totals_row(row)

        elif 'TOTAL PRODUCTION CHARGE' in header_text:
            # Table 6: just the total
            text = ' '.join(rows[-1] if len(rows) > 1 else rows[0])
            result["total_production_charge"] = _extract_total_charge(text)

        else:
            # Check ALL rows for TOTALS or TOTAL PRODUCTION CHARGE
            for row in rows:
                text = ' '.join(row).upper()
                if 'FULL MFG TOTALS' in text:
                    result["full_mfg_totals"] = _extract_totals_row(row)
                elif 'MEALS TOTALS' in text:
                    result["meals_totals"] = _extract_totals_row(row)
                elif 'LABEL ONLY TOTALS' in text:
                    result["label_only_totals"] = _extract_totals_row(row, label_only=True)
                elif 'TOTAL PRODUCTION CHARGE' in text:
                    result["total_production_charge"] = _extract_total_charge(' '.join(row))

    result["mfg_date"] = mfg_date_found
    return result


def _parse_table_line(row: list, section: str, has_cases: bool = True) -> dict | None:
    """Parse a line item from a known table structure.
    Full MFG / Meals: [date, product, case_packouts_empty, cases, yield]
    Label Only: [date, product, empty, empty, yield]
    """
    cells = [c for c in row if c is not None]
    # Skip all-empty rows
    if all(not c.strip() or c.strip() == '-' for c in cells):
        return None

    item = {"section": section, "product_name": "", "case_packouts": 0,
            "total_yield": 0, "mfg_date": None}

    numbers_found = []
    for cell in cells:
        cell = cell.strip()
        if not cell or cell == '-':
            continue

        # Date?
        dt = _parse_date(cell)
        if dt:
            item["mfg_date"] = dt
            continue

        # Number? (after removing pdfplumber space artifacts)
        val = _parse_int(cell)
        if val > 0:
            numbers_found.append(val)
            continue

        # Product name
        if not item["product_name"] and not cell.startswith('$'):
            item["product_name"] = cell

    # Assign numbers based on how many were found
    if has_cases and len(numbers_found) >= 2:
        # Two numbers: first = cases, second = yield
        item["case_packouts"] = numbers_found[-2]
        item["total_yield"] = numbers_found[-1]
    elif has_cases and len(numbers_found) == 1:
        # Single number: it's the yield (cases unknown)
        item["total_yield"] = numbers_found[0]
    elif not has_cases and numbers_found:
        # Label only: last number is yield
        item["total_yield"] = numbers_found[-1]

    return item if item["product_name"] else None


def _extract_totals_row(row: list, label_only: bool = False) -> dict:
    """Extract totals from a structured row like ['FULL MFG TOTALS COUNT', '1 37', '3 ,973', '$ 3 ,853.81'].
    For label_only, there's no cases column — first number is yield."""
    numbers = []
    charge = 0.0

    for cell in row:
        if not cell:
            continue
        cell = str(cell).strip()
        if not cell or cell == '-':
            continue
        # Skip the label cell
        if 'TOTALS' in cell.upper() or 'COUNT' in cell.upper():
            continue
        # Dollar amount?
        if '$' in cell:
            charge = _parse_dollar(cell)
            continue
        # Integer
        val = _parse_int(cell)
        if val > 0:
            numbers.append(val)

    if label_only:
        # Label only: no cases, just yield
        yld = numbers[0] if numbers else 0
        return {"cases": 0, "yield": yld, "charge": charge}
    else:
        cases = numbers[0] if len(numbers) >= 1 else 0
        yld = numbers[1] if len(numbers) >= 2 else 0
        return {"cases": cases, "yield": yld, "charge": charge}


def _extract_totals(row: list, text: str) -> dict:
    """Extract totals from a TOTALS row (text fallback path)."""
    numbers = re.findall(r'[\d,]+\.?\d*', text)
    dollar = re.search(r'\$\s*([\d,.\s]+)', text)
    cases = 0
    yld = 0
    charge = 0.0

    if dollar:
        charge = _parse_dollar(dollar.group(1))

    # Filter out numbers that are part of the dollar amount
    int_numbers = []
    for n in numbers:
        val = _parse_int(n)
        if val > 0 and n not in (dollar.group(1) if dollar else ''):
            int_numbers.append(val)

    if len(int_numbers) >= 2:
        cases = int_numbers[0]
        yld = int_numbers[1]
    elif len(int_numbers) == 1:
        yld = int_numbers[0]

    return {"cases": cases, "yield": yld, "charge": charge}


def _extract_total_charge(text: str) -> float:
    """Extract total production charge dollar amount.
    Handles pdfplumber spaces: '$ 5 ,315.41'"""
    m = re.search(r'\$\s*([\d,.\s]+)', text)
    return _parse_dollar(m.group(1)) if m else 0.0


def _parse_line_item(row: list, section: str) -> dict | None:
    """Parse a single line item row."""
    # Filter out empty/dash cells
    cells = [c for c in row if c and c.strip() not in ('-', '')]
    if not cells:
        return None

    item = {"section": section, "product_name": "", "case_packouts": 0,
            "total_yield": 0, "mfg_date": None}

    # Try to find date, product name, and numbers
    for cell in cells:
        dt = _parse_date(cell)
        if dt:
            item["mfg_date"] = dt
            continue

        # Check if it's a number
        cleaned = re.sub(r'[,\s]', '', cell)
        try:
            val = int(float(cleaned))
            if val > 0:
                if section == 'label_only':
                    # Label only: no case packouts, just yield
                    item["total_yield"] = val
                elif item["case_packouts"] == 0 and val < 100:
                    item["case_packouts"] = val
                else:
                    item["total_yield"] = val
            continue
        except (ValueError, TypeError):
            pass

        # Must be the product name
        if not item["product_name"] and not cell.startswith('$'):
            item["product_name"] = cell.strip()

    return item if item["product_name"] else None


def _parse_from_text(text: str, result: dict) -> dict:
    """Fallback: parse from raw text using regex."""
    lines = text.split('\n')
    section = None
    mfg_date_found = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if 'FULL MFG PRODUCTION LOG' in stripped:
            section = 'full_mfg'
            continue
        if 'MEALS PRODUCTION LOG' in stripped:
            section = 'meals'
            continue
        if 'LABEL ONLY PRODUCTION LOG' in stripped:
            section = 'label_only'
            continue
        if 'PACKAGING TOTALS' in stripped:
            section = 'packaging'
            continue

        if 'FULL MFG TOTALS' in stripped:
            result["full_mfg_totals"] = _extract_totals([], stripped)
            section = None
            continue
        if 'MEALS TOTALS' in stripped:
            result["meals_totals"] = _extract_totals([], stripped)
            section = None
            continue
        if 'LABEL ONLY TOTALS' in stripped:
            result["label_only_totals"] = _extract_totals([], stripped)
            section = None
            continue
        if 'TOTAL PRODUCTION CHARGE' in stripped:
            result["total_production_charge"] = _extract_total_charge(stripped)
            continue

        # Try to parse as a line item (date product cases yield)
        m = re.match(
            r'(\d{1,2}/\d{1,2}/\d{2,4})?\s*(.+?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$',
            stripped
        )
        if m and section in ('full_mfg', 'meals'):
            dt = _parse_date(m.group(1)) if m.group(1) else None
            if dt and not mfg_date_found:
                mfg_date_found = dt
            result[section].append({
                "section": section,
                "product_name": m.group(2).strip(),
                "case_packouts": _parse_int(m.group(3)),
                "total_yield": _parse_int(m.group(4)),
                "mfg_date": dt,
            })
            continue

        # Label only: product yield (no case packouts)
        m2 = re.match(r'(\d{1,2}/\d{1,2}/\d{2,4})?\s*(.+?)\s+(\d[\d,]*)\s*$', stripped)
        if m2 and section == 'label_only':
            dt = _parse_date(m2.group(1)) if m2.group(1) else None
            if dt and not mfg_date_found:
                mfg_date_found = dt
            result[section].append({
                "section": section,
                "product_name": m2.group(2).strip(),
                "case_packouts": 0,
                "total_yield": _parse_int(m2.group(3)),
                "mfg_date": dt,
            })

    result["mfg_date"] = mfg_date_found
    return result


# ── SKU Matching ─────────────────────────────────────────────────────

# Seed translations for known RMFG product names
SEED_TRANSLATIONS = {
    "Alpha Tolman": "CH-ALPHA",
    "Triple Cream": "CH-BRIE",
    "Sottocenere": "CH-SOT",
    "Manchego Aurora": "CH-MAU3",
    "Prairie Breeze": "CH-BRZ",
    "Wooly Black Garlic": "CH-WBG",
    "Wooly Diablo": "CH-WDBL",
    "Ubriaco Pinot Rose": "CH-UBR",
    "Spanish Smoking Goat": "CH-SGC",
    "Alp Blossom": "CH-ALPB",
    "Shadow Blossom": "CH-SHBL",
    "Honey Clover Gouda": "CH-HCG",
    "McCall's Irish Porter": "CH-MIP",
    "Barista": "CH-BAR",
    "Lemon, Feta & Olive": "AC-LFO",
    "Tome Preovence": "CH-TOPR",
    "Toma Provence": "CH-TOPR",
    "Honey & Herb Prosciutto": "MT-JAHH",
    "Smoked Speck": "MT-ASPK",
    "Applewood Speck": "MT-ASPK",
    "Prosciutto Piccante": "MT-PP",
    "Sun-Dried Figs": "AC-SDF",
    "Chocolate Pretzels": "AC-MCP",
    "Marcona Almonds": "AC-MARC",
    "Fiddlehead": "CH-IPAC",
    "Piri Piri": "AC-PPCM",
    "Pradera": "CH-PRAD",
    "Pichin Tomme": "CH-LOU",
}


def build_auto_translations(inventory: dict) -> dict:
    """Build normalized product name → SKU from inventory names."""
    translations = {}
    prefixes_to_strip = [
        "Cheese Slice, ", "Accompaniment, ", "Meat, ",
        "Cheese, ", "Acc, ",
    ]

    for sku, info in inventory.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name", "")
        if not name:
            continue
        # Add full name
        translations[name.lower()] = sku
        # Add stripped prefix versions
        for prefix in prefixes_to_strip:
            if name.startswith(prefix):
                stripped = name[len(prefix):]
                translations[stripped.lower()] = sku


    return translations


def match_product_to_sku(product_name: str, sku_translations: dict,
                         inventory: dict) -> tuple[str, float, str]:
    """
    Match a product name to a SKU.
    Returns: (sku, confidence, method)
    """
    if not product_name:
        return ("", 0.0, "empty")

    # Step 1: Exact match in sku_translations (user-saved mappings)
    if product_name in sku_translations:
        return (sku_translations[product_name], 1.0, "exact")

    # Step 1b: Seed translations
    if product_name in SEED_TRANSLATIONS:
        return (SEED_TRANSLATIONS[product_name], 1.0, "seed")

    # Step 2: Case-insensitive match in sku_translations
    lower_name = product_name.lower()
    for key, sku in sku_translations.items():
        if key.lower() == lower_name:
            return (sku, 1.0, "exact_ci")

    # Step 3: Auto-translations from inventory names
    auto_trans = build_auto_translations(inventory)
    if lower_name in auto_trans:
        return (auto_trans[lower_name], 0.95, "auto")

    # Step 4: Fuzzy match against all known names
    all_names = {}
    all_names.update({k.lower(): v for k, v in sku_translations.items()})
    all_names.update({k.lower(): v for k, v in SEED_TRANSLATIONS.items()})
    all_names.update(auto_trans)

    if all_names:
        matches = difflib.get_close_matches(lower_name, all_names.keys(),
                                            n=1, cutoff=0.65)
        if matches:
            matched_name = matches[0]
            ratio = difflib.SequenceMatcher(None, lower_name, matched_name).ratio()
            return (all_names[matched_name], round(ratio, 2), "fuzzy")

    return ("", 0.0, "unmatched")


def get_match_candidates(product_name: str, sku_translations: dict,
                         inventory: dict, top_n: int = 8) -> list[dict]:
    """
    Return ranked SKU candidates for an unmatched product name.
    Each entry: {sku, name, score, category}
    """
    if not product_name:
        return []

    lower_name = product_name.lower()

    # Build lookup of all SKU -> display name
    sku_display = {}
    for sku, info in inventory.items():
        if isinstance(info, dict):
            sku_display[sku] = info.get("name", sku)
        else:
            sku_display[sku] = sku

    # Build all matchable names -> SKU
    auto_trans = build_auto_translations(inventory)
    all_names = {}
    all_names.update({k.lower(): v for k, v in sku_translations.items()})
    all_names.update({k.lower(): v for k, v in SEED_TRANSLATIONS.items()})
    all_names.update(auto_trans)

    # Score every candidate
    scored = {}
    for name, sku in all_names.items():
        ratio = difflib.SequenceMatcher(None, lower_name, name).ratio()
        # Boost: substring containment
        if lower_name in name or name in lower_name:
            ratio = max(ratio, 0.75)
        # Boost: first word match
        first_word = lower_name.split()[0] if lower_name.split() else ""
        if first_word and first_word in name:
            ratio += 0.1
        if sku not in scored or ratio > scored[sku]:
            scored[sku] = ratio

    # Sort by score descending
    ranked = sorted(scored.items(), key=lambda x: -x[1])[:top_n]

    # Infer likely category from product name
    meat_words = {"prosciutto", "speck", "salami", "ham", "bresaola", "coppa", "piri"}
    acc_words = {"fig", "figs", "almond", "almonds", "pretzel", "pretzels", "olive",
                 "chocolate", "honey", "cracker", "dried", "marcona", "lemon", "cocktail"}
    words = set(lower_name.split())
    likely_prefix = None
    if words & meat_words:
        likely_prefix = "MT-"
    elif words & acc_words:
        likely_prefix = "AC-"

    results = []
    for sku, score in ranked:
        cat = "cheese" if sku.startswith("CH-") else \
              "meat" if sku.startswith("MT-") else \
              "accompaniment" if sku.startswith("AC-") else "other"
        # Boost score if category matches expectation
        display_score = score
        if likely_prefix and sku.startswith(likely_prefix):
            display_score += 0.15
        results.append({
            "sku": sku,
            "name": sku_display.get(sku, sku),
            "score": round(display_score, 2),
            "category": cat,
            "recommended": display_score >= 0.55,
        })

    # Re-sort with category boost applied
    results.sort(key=lambda x: -x["score"])
    return results[:top_n]


# ── Bulk Weight Extraction ───────────────────────────────────────────

# SKUs purchased as whole pieces — NOT wheels/blocks to be sliced.
# These appear as "Cheese Wheel" or "Cheese Block" in the inventory CSV
# but yield no slicing potential (they ship as-is).
PIECE_SKUS = frozenset({
    "CH-GPBRIE",   # Petit Garlic & Pepper Triple Cream Brie
    "CH-TTBRIE",   # Petit Truffle Triple Cream Brie
    "CH-TIP",      # Tipperary Brie
    "CH-EBRIE",    # Échiré Brie
    "CH-MAFT",     # Maffra (never assigned)
    "CH-TOPR",     # Toma Provence (purchased as pieces)
})


def extract_bulk_weights(csv_rows: list[dict]) -> dict:
    """
    Extract bulk weights from inventory CSV rows.
    Maps "Cheese Wheel, X" Quantity1/Unit1 to the matching "Cheese Slice, X" SKU.
    Returns {sku: {weight: float, unit: str, weight_lbs: float, count: int, potential_yield: int}}
    count = number of wheels/blocks on hand (from Total column).
    potential_yield = count × weight_lbs × 2.67 (slices per lb).
    """
    # Pass 1: collect wheel/block weights by cheese name
    bulk_by_name = {}
    for row in csv_rows:
        ingredient = row.get("Ingredient", "").strip()
        q1 = row.get("Quantity1", "").strip()
        u1 = row.get("Unit1", "").strip().lower()
        if not q1 or not u1:
            continue
        # Only "Cheese Wheel" and "Cheese Block" rows
        for prefix in ("Cheese Wheel, ", "Cheese Block, "):
            if ingredient.startswith(prefix):
                name = ingredient[len(prefix):].strip().lower()
                try:
                    weight = float(q1)
                except (ValueError, TypeError):
                    continue
                # Convert to lbs
                if "kg" in u1:
                    weight_lbs = weight * 2.20462
                elif "oz" in u1 and "lb" not in u1:
                    weight_lbs = weight / 16.0
                elif u1 == "wheel":
                    weight_lbs = 0  # no weight info, just count
                else:
                    weight_lbs = weight
                # Get wheel count from Total column
                try:
                    count = int(float(row.get("Total", 0) or 0))
                except (ValueError, TypeError):
                    count = 0
                # Compute this row's potential yield
                row_potential = int(count * weight_lbs * 2.67) if weight_lbs > 0 else 0
                # Accumulate if multiple rows for same cheese (e.g. different sizes)
                if name in bulk_by_name:
                    existing = bulk_by_name[name]
                    existing["count"] = existing.get("count", 0) + count
                    existing["_row_potential"] = existing.get("_row_potential", 0) + row_potential
                    # Keep the heavier weight entry as representative
                    if weight_lbs > existing.get("weight_lbs", 0):
                        existing["weight"] = weight
                        existing["unit"] = u1
                        existing["weight_lbs"] = round(weight_lbs, 2)
                else:
                    bulk_by_name[name] = {
                        "weight": weight, "unit": u1,
                        "weight_lbs": round(weight_lbs, 2),
                        "count": count,
                        "_row_potential": row_potential,
                    }
                break

    # Pass 2: map cheese names to SKUs via "Cheese Slice, X" rows
    slice_name_to_sku = {}
    for row in csv_rows:
        ingredient = row.get("Ingredient", "").strip()
        sku = row.get("Product SKU", "").strip()
        if sku and ingredient.startswith("Cheese Slice, "):
            name = ingredient[len("Cheese Slice, "):].strip().lower()
            slice_name_to_sku[name] = sku

    # Pass 3: join bulk weights to SKUs using pre-computed potential
    result = {}
    for name, wt in bulk_by_name.items():
        sku = slice_name_to_sku.get(name)
        if not sku:
            # Fuzzy match: some names differ slightly
            for sname, ssku in slice_name_to_sku.items():
                if name in sname or sname in name:
                    sku = ssku
                    break
        if sku:
            count = wt.get("count", 0)
            potential = wt.pop("_row_potential", 0)
            if sku in result:
                result[sku]["count"] = result[sku].get("count", 0) + count
                result[sku]["potential_yield"] = (
                    result[sku].get("potential_yield", 0) + potential)
                if wt.get("weight_lbs", 0) > result[sku].get("weight_lbs", 0):
                    result[sku]["weight"] = wt["weight"]
                    result[sku]["unit"] = wt["unit"]
                    result[sku]["weight_lbs"] = wt["weight_lbs"]
            else:
                result[sku] = {
                    "weight": wt["weight"], "unit": wt["unit"],
                    "weight_lbs": wt["weight_lbs"],
                    "count": count,
                    "potential_yield": potential,
                }

    # Remove piece SKUs that aren't actually wheels/blocks to slice
    for sku in PIECE_SKUS:
        result.pop(sku, None)

    return result


# ── Yield Ratio Analysis ─────────────────────────────────────────────

SLICE_PER_LB = 2.67


def compute_yield_ratios(invoices: list, bulk_weights: dict | None = None) -> dict:
    """
    Compute historical yield-per-case ratios from all invoices.
    If bulk_weights provided ({sku: {weight_lbs}}), uses actual wheel weights
    for oz/pc calculation instead of circular derivation.
    Returns {sku: {avg_ratio, weight_lbs, oz_per_pc, runs, ...}}
    """
    from collections import defaultdict

    by_sku = defaultdict(list)
    for inv in invoices:
        for li in inv.get("line_items", []):
            cases = li.get("case_packouts", 0)
            yld = li.get("total_yield", 0)
            sku = li.get("sku", "")
            if li.get("section") != "full_mfg":
                continue
            if not sku or cases <= 0 or yld <= 0:
                continue
            ratio = yld / cases
            # Filter extreme outliers (likely data errors)
            if ratio < 1 or cases > 500:
                continue
            by_sku[sku].append({
                "cases": cases,
                "yield": yld,
                "ratio": ratio,
                "invoice_id": inv.get("id"),
                "date": inv.get("mfg_date"),
            })

    if bulk_weights is None:
        bulk_weights = {}

    result = {}
    for sku, entries in by_sku.items():
        ratios = [e["ratio"] for e in entries]
        total_cases = sum(e["cases"] for e in entries)
        total_yield = sum(e["yield"] for e in entries)
        avg_ratio = total_yield / total_cases if total_cases else 0

        # Use actual bulk weight if available, otherwise estimate from ratio
        bw = bulk_weights.get(sku)
        if bw and bw.get("weight_lbs"):
            weight_lbs = bw["weight_lbs"]
            weight_source = "inventory"
        else:
            weight_lbs = avg_ratio / SLICE_PER_LB
            weight_source = "estimated"

        # oz per piece = (wheel weight in oz) / (pieces per wheel)
        oz_per_pc = (weight_lbs * 16) / avg_ratio if avg_ratio else 0

        result[sku] = {
            "avg_ratio": round(avg_ratio, 1),
            "weight_lbs": round(weight_lbs, 1),
            "weight_source": weight_source,
            "oz_per_pc": round(oz_per_pc, 2),
            "runs": len(entries),
            "min_ratio": round(min(ratios), 1),
            "max_ratio": round(max(ratios), 1),
            "total_cases": total_cases,
            "total_yield": total_yield,
        }

    return result


def annotate_invoice_yields(invoice: dict, yield_ratios: dict) -> list:
    """
    Annotate each line item with expected yield based on historical ratios.
    Returns list of annotations: [{sku, cases, actual, expected, variance, variance_pct}]
    """
    annotations = []
    for li in invoice.get("line_items", []):
        sku = li.get("sku", "")
        cases = li.get("case_packouts", 0)
        actual = li.get("total_yield", 0)
        if li.get("section") != "full_mfg" or not sku or cases <= 0:
            continue

        ratio_data = yield_ratios.get(sku)
        if not ratio_data or ratio_data["runs"] < 2:
            annotations.append({
                "sku": sku, "cases": cases, "actual": actual,
                "expected": None, "variance": None, "variance_pct": None,
                "est_weight": None, "note": "insufficient history",
            })
            continue

        expected = round(cases * ratio_data["avg_ratio"])
        variance = actual - expected
        variance_pct = round((variance / expected) * 100, 1) if expected else 0

        annotations.append({
            "sku": sku,
            "cases": cases,
            "actual": actual,
            "expected": expected,
            "variance": variance,
            "variance_pct": variance_pct,
            "oz_per_pc": ratio_data["oz_per_pc"],
            "weight_lbs": ratio_data.get("weight_lbs"),
            "weight_source": ratio_data.get("weight_source", "estimated"),
            "avg_ratio": ratio_data["avg_ratio"],
            "note": "over" if variance > 0 else "under" if variance < 0 else "exact",
        })

    return annotations


# ── Gmail IMAP Polling ───────────────────────────────────────────────

def gmail_connect(user: str, password: str,
                  host: str = "imap.gmail.com",
                  port: int = 993) -> imaplib.IMAP4_SSL:
    """Connect to Gmail IMAP."""
    conn = imaplib.IMAP4_SSL(host, port)
    conn.login(user, password)
    return conn


def search_rmfg_invoices(conn: imaplib.IMAP4_SSL,
                         subject_filter: str = "Production Breakdown",
                         processed_ids: list | None = None
                         ) -> list[dict]:
    """
    Search inbox for RMFG production invoice emails.
    Returns list of {msg_id, date, subject, attachments: [{filename, pdf_bytes}]}
    """
    if processed_ids is None:
        processed_ids = []

    conn.select("INBOX")
    status, data = conn.search(None, f'SUBJECT "{subject_filter}"')

    if status != "OK":
        return []

    msg_ids = data[0].split()
    results = []

    for msg_id in msg_ids:
        msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)

        # Fetch message
        status, msg_data = conn.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Get Message-ID header for dedup
        message_id = msg.get("Message-ID", msg_id_str)
        if message_id in processed_ids:
            continue

        # Parse date
        date_str = msg.get("Date", "")
        try:
            from email.utils import parsedate_to_datetime
            msg_date = parsedate_to_datetime(date_str).date().isoformat()
        except Exception:
            msg_date = date.today().isoformat()

        # Parse subject
        subject_raw = msg.get("Subject", "")
        subject_decoded = ""
        for part, enc in decode_header(subject_raw):
            if isinstance(part, bytes):
                subject_decoded += part.decode(enc or 'utf-8', errors='replace')
            else:
                subject_decoded += part

        # Extract PDF attachments
        attachments = []
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            filename = part.get_filename()
            if not filename:
                continue
            # Only process "Product Production Breakdown" PDFs
            if (filename.upper().startswith('AHB') and
                filename.lower().endswith('.pdf') and
                'breakdown' in filename.lower()):
                pdf_bytes = part.get_payload(decode=True)
                if pdf_bytes:
                    attachments.append({
                        "filename": filename,
                        "pdf_bytes": pdf_bytes,
                    })

        if attachments:
            results.append({
                "msg_id": message_id,
                "date": msg_date,
                "subject": subject_decoded,
                "attachments": attachments,
            })

    return results


# ── Reconciliation Engine ────────────────────────────────────────────

def reconcile_invoice_with_pos(invoice: dict, open_pos: list) -> tuple[list, list]:
    """
    Match invoice line items against open POs.
    Returns: (matches, closeable_po_indices)
    """
    matches = []
    closeable = []

    for item in invoice.get("line_items", []):
        sku = item.get("sku", "")
        if not sku:
            continue

        actual_yield = item.get("total_yield", 0)

        # Find matching open PO
        for i, po in enumerate(open_pos):
            if (po.get("sku") == sku and
                po.get("type", "").upper() in ("MFG", "PRODUCTION") and
                po.get("status", "").lower() in ("open", "ordered")):

                po_qty = int(po.get("qty", 0))
                variance = actual_yield - po_qty
                variance_pct = round((variance / po_qty * 100), 2) if po_qty else 0

                matches.append({
                    "sku": sku,
                    "po_qty": po_qty,
                    "actual_yield": actual_yield,
                    "variance": variance,
                    "variance_pct": variance_pct,
                    "po_index": i,
                })
                closeable.append(i)
                break
        else:
            # No matching PO found — still record as yield data
            matches.append({
                "sku": sku,
                "po_qty": 0,
                "actual_yield": actual_yield,
                "variance": actual_yield,
                "variance_pct": 0,
                "po_index": None,
            })

    return matches, closeable


def apply_reconciliation(invoice_id: str, settings: dict) -> dict:
    """
    Apply reconciliation: close POs, log yield history, calculate costs.
    Returns summary of actions taken.
    """
    invoices = settings.get("production_invoices", [])
    invoice = None
    for inv in invoices:
        if inv.get("id") == invoice_id:
            invoice = inv
            break

    if not invoice:
        return {"error": f"Invoice {invoice_id} not found"}

    open_pos = settings.get("open_pos", [])
    matches, closeable = reconcile_invoice_with_pos(invoice, open_pos)

    # Store matches on the invoice
    invoice["po_matches"] = matches
    actions = {"closed_pos": 0, "yield_entries": 0, "cost_entries": 0}

    # Close matched POs
    for idx in closeable:
        if 0 <= idx < len(open_pos):
            open_pos[idx]["status"] = "Received"
            actions["closed_pos"] += 1

    # Append to production_yield_history
    yield_history = settings.setdefault("production_yield_history", [])
    for match in matches:
        if match["po_qty"] > 0:
            yield_history.append({
                "date": invoice.get("mfg_date", invoice.get("received_date")),
                "sku": match["sku"],
                "expected": match["po_qty"],
                "actual": match["actual_yield"],
                "factor": round(match["actual_yield"] / max(1, match["po_qty"]), 3),
            })
            actions["yield_entries"] += 1

    # Flag yield discrepancies (> 10% variance)
    discrepancies = settings.setdefault("yield_discrepancies", [])
    for match in matches:
        if abs(match.get("variance_pct", 0)) > 10:
            discrepancies.append({
                "date": invoice.get("mfg_date", invoice.get("received_date")),
                "sku": match["sku"],
                "type": "production_invoice",
                "expected_qty": match["po_qty"],
                "actual_qty": match["actual_yield"],
                "variance": match["variance"],
                "yield_date": invoice.get("mfg_date"),
                "snapshot_date": invoice.get("received_date"),
                "status": "new",
            })

    # Calculate per-unit costs and append to cost history
    cost_history = settings.setdefault("production_cost_history", [])
    for section_key in ("full_mfg", "meals", "label_only"):
        charge_key = f"{section_key}_charge"
        section_charge = invoice.get(charge_key, 0)
        section_items = [li for li in invoice.get("line_items", [])
                         if li.get("section") == section_key]
        section_total_yield = sum(li.get("total_yield", 0) for li in section_items)

        if section_total_yield > 0 and section_charge > 0:
            cost_per_unit = section_charge / section_total_yield
            for li in section_items:
                item_yield = li.get("total_yield", 0)
                estimated_cost = round(cost_per_unit * item_yield, 2)
                li["estimated_cost"] = estimated_cost

                if li.get("sku"):
                    cost_history.append({
                        "date": invoice.get("mfg_date", invoice.get("received_date")),
                        "sku": li["sku"],
                        "invoice_id": invoice_id,
                        "yield": item_yield,
                        "estimated_cost": estimated_cost,
                    })
                    actions["cost_entries"] += 1

    # Update invoice status
    unmatched = [li for li in invoice.get("line_items", []) if not li.get("sku")]
    if not unmatched:
        invoice["status"] = "matched"
    elif len(unmatched) < len(invoice.get("line_items", [])):
        invoice["status"] = "partial"
    else:
        invoice["status"] = "pending"

    return actions
