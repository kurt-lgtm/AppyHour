"""Curation Manager — CRUD for curations, time scoping, addon bundles, SKU dedup.

Data model stored in settings JSON under the "curations_v2" key:

    {
        "curations_v2": {
            "MONG": {
                "type": "rotation",          # rotation | monthly
                "label": "Mongolio",
                "recipe": [["CH-BLR", 1], ["CH-MAFT", 1], ["MT-SOP", 1], ...],
                "pr_cjam": {"cheese": "CH-XXX", "jam": "AC-XXX"},
                "cex_ec": "CH-XXX",
                "cexec_splits": {"CH-A": 60, "CH-B": 40},
                "addons": ["BL-EXTRA-CHEESE"],
                "active": true
            },
            "MS-APR2026": {
                "type": "monthly",
                "label": "Meat Selections",
                "applies_to": ["AHB-MED", "AHB-LGE", "AHB-CMED"],
                "effective_start": "2026-04-01",
                "effective_end": "2026-04-30",
                "recipe": [["CH-BLR", 1], ...],
                "pr_cjam": {"cheese": "CH-XXX", "jam": "AC-XXX"},
                "cex_ec": "CH-XXX",
                "cexec_splits": {},
                "addons": [],
                "active": true
            }
        },
        "addon_bundles": {
            "BL-EXTRA-CHEESE": {
                "name": "Extra Cheese Bundle",
                "children": [["CH-BLR", 1], ["CH-MCPC", 1]]
            }
        }
    }

Backward compatibility:
    - On first load, migrates old curation_recipes/pr_cjam/cex_ec into curations_v2
    - Old keys preserved so tkinter app keeps working
    - Writes back to both old and new format on save
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass, field


# ── Data classes ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class CurationRecipeItem:
    sku: str
    qty: int = 1


@dataclass
class Curation:
    key: str
    curation_type: str  # "rotation" or "monthly"
    label: str
    recipe: list[tuple[str, int]]
    pr_cjam: dict  # {"cheese": "CH-...", "jam": "AC-..."}
    cex_ec: str
    cexec_splits: dict  # {"CH-A": 60, "CH-B": 40}
    addons: list[str]  # addon bundle keys (BL-*)
    active: bool = True
    # Monthly-only fields
    applies_to: list[str] = field(default_factory=list)  # ["AHB-MED", "AHB-LGE", ...]
    effective_start: str = ""  # ISO date
    effective_end: str = ""    # ISO date

    def to_dict(self) -> dict:
        d = {
            "type": self.curation_type,
            "label": self.label,
            "recipe": [list(item) for item in self.recipe],
            "pr_cjam": dict(self.pr_cjam),
            "cex_ec": self.cex_ec,
            "cexec_splits": dict(self.cexec_splits),
            "addons": list(self.addons),
            "active": self.active,
        }
        if self.curation_type == "monthly":
            d["applies_to"] = list(self.applies_to)
            d["effective_start"] = self.effective_start
            d["effective_end"] = self.effective_end
        return d

    @classmethod
    def from_dict(cls, key: str, data: dict) -> Curation:
        recipe_raw = data.get("recipe", [])
        recipe = [(r[0], int(r[1])) if isinstance(r, (list, tuple)) else (r, 1) for r in recipe_raw]
        return cls(
            key=key,
            curation_type=data.get("type", "rotation"),
            label=data.get("label", key),
            recipe=recipe,
            pr_cjam=data.get("pr_cjam", {"cheese": "", "jam": ""}),
            cex_ec=data.get("cex_ec", ""),
            cexec_splits=data.get("cexec_splits", {}),
            addons=data.get("addons", []),
            active=data.get("active", True),
            applies_to=data.get("applies_to", []),
            effective_start=data.get("effective_start", ""),
            effective_end=data.get("effective_end", ""),
        )


@dataclass
class AddonBundle:
    key: str
    name: str
    children: list[tuple[str, int]]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "children": [list(item) for item in self.children],
        }

    @classmethod
    def from_dict(cls, key: str, data: dict) -> AddonBundle:
        children_raw = data.get("children", [])
        children = [(c[0], int(c[1])) if isinstance(c, (list, tuple)) else (c, 1) for c in children_raw]
        return cls(key=key, name=data.get("name", key), children=children)


# ── Migration from old format ─────────────────────────────────────────

DEFAULT_CURATION_ORDER = ["MONG", "MDT", "OWC", "SPN", "ALPN", "ISUN", "HHIGH"]


def migrate_from_legacy(settings: dict) -> dict:
    """Migrate old curation_recipes/pr_cjam/cex_ec into curations_v2.

    Only runs if curations_v2 doesn't exist yet. Non-destructive — old keys stay.
    """
    if "curations_v2" in settings:
        return settings

    old_recipes = settings.get("curation_recipes", {})
    old_pr_cjam = settings.get("pr_cjam", {})
    old_cex_ec = settings.get("cex_ec", {})
    old_splits = settings.get("cexec_splits", {})

    curations_v2 = {}

    # Migrate rotation curations
    all_keys = set(old_recipes.keys()) | set(old_pr_cjam.keys()) | set(old_cex_ec.keys())
    for key in all_keys:
        recipe = old_recipes.get(key, [])
        pr = old_pr_cjam.get(key, {"cheese": "", "jam": ""})
        if isinstance(pr, str):
            pr = {"cheese": pr, "jam": ""}
        ec = old_cex_ec.get(key, "")
        splits = old_splits.get(key, {})

        curations_v2[key] = {
            "type": "rotation",
            "label": key,
            "recipe": recipe,
            "pr_cjam": pr,
            "cex_ec": ec,
            "cexec_splits": splits,
            "addons": [],
            "active": True,
        }

    # Migrate monthly box recipes as monthly curations
    old_monthly = settings.get("monthly_box_recipes", {})
    for month_key, box_types in old_monthly.items():
        for box_type, slots in box_types.items():
            cur_key = f"{month_key}-{box_type}"
            recipe = [(slot[1], slot[2]) if len(slot) >= 3 else (slot[1], 1) for slot in slots if slot[1]]
            curations_v2[cur_key] = {
                "type": "monthly",
                "label": f"{month_key} {box_type}",
                "applies_to": [box_type],
                "effective_start": "",
                "effective_end": "",
                "recipe": recipe,
                "pr_cjam": {"cheese": "", "jam": ""},
                "cex_ec": "",
                "cexec_splits": {},
                "addons": [],
                "active": True,
            }

    settings["curations_v2"] = curations_v2

    # Migrate bundle_map as addon_bundles
    if "addon_bundles" not in settings:
        old_bundles = settings.get("bundle_map", {})
        addon_bundles = {}
        for bkey, children in old_bundles.items():
            if bkey.startswith("BL-") or bkey.startswith("AHB-"):
                addon_bundles[bkey] = {
                    "name": bkey,
                    "children": children,
                }
        if addon_bundles:
            settings["addon_bundles"] = addon_bundles

    return settings


# ── Write-back to old format (keeps tkinter app working) ──────────────

def sync_to_legacy(settings: dict) -> dict:
    """Write curations_v2 back to old format keys for backward compatibility."""
    curations = settings.get("curations_v2", {})
    recipes = {}
    pr_cjam = {}
    cex_ec = {}
    cexec_splits = {}

    for key, data in curations.items():
        if data.get("type") == "rotation":
            recipes[key] = data.get("recipe", [])
            pr_cjam[key] = data.get("pr_cjam", {"cheese": "", "jam": ""})
            cex_ec[key] = data.get("cex_ec", "")
            if data.get("cexec_splits"):
                cexec_splits[key] = data["cexec_splits"]

    settings["curation_recipes"] = recipes
    settings["pr_cjam"] = pr_cjam
    settings["cex_ec"] = cex_ec
    if cexec_splits:
        settings["cexec_splits"] = cexec_splits

    return settings


# ── CRUD operations ───────────────────────────────────────────────────

def list_curations(settings: dict) -> list[dict]:
    """Return all curations as a list of dicts with their key included."""
    curations = settings.get("curations_v2", {})
    result = []
    for key, data in curations.items():
        entry = {"key": key, **data}
        result.append(entry)
    return result


def get_curation(settings: dict, key: str) -> dict | None:
    curations = settings.get("curations_v2", {})
    data = curations.get(key)
    if data is None:
        return None
    return {"key": key, **data}


def upsert_curation(settings: dict, key: str, data: dict) -> dict:
    """Create or update a curation. Returns the updated settings."""
    curations = settings.setdefault("curations_v2", {})

    # Validate required fields
    curation_type = data.get("type", "rotation")
    curations[key] = {
        "type": curation_type,
        "label": data.get("label", key),
        "recipe": data.get("recipe", []),
        "pr_cjam": data.get("pr_cjam", {"cheese": "", "jam": ""}),
        "cex_ec": data.get("cex_ec", ""),
        "cexec_splits": data.get("cexec_splits", {}),
        "addons": data.get("addons", []),
        "active": data.get("active", True),
    }

    if curation_type == "monthly":
        curations[key]["applies_to"] = data.get("applies_to", [])
        curations[key]["effective_start"] = data.get("effective_start", "")
        curations[key]["effective_end"] = data.get("effective_end", "")

    # Sync back to legacy format
    settings = sync_to_legacy(settings)
    return settings


def delete_curation(settings: dict, key: str) -> dict:
    """Delete a curation. Returns updated settings."""
    curations = settings.get("curations_v2", {})
    curations.pop(key, None)
    settings = sync_to_legacy(settings)
    return settings


def duplicate_curation(settings: dict, source_key: str, new_key: str) -> dict:
    """Duplicate a curation under a new key."""
    curations = settings.get("curations_v2", {})
    source = curations.get(source_key)
    if source is None:
        return settings

    import copy
    curations[new_key] = copy.deepcopy(source)
    curations[new_key]["label"] = f"{source.get('label', source_key)} (Copy)"
    settings = sync_to_legacy(settings)
    return settings


# ── Addon bundle CRUD ─────────────────────────────────────────────────

def list_addon_bundles(settings: dict) -> list[dict]:
    bundles = settings.get("addon_bundles", {})
    return [{"key": k, **v} for k, v in bundles.items()]


def upsert_addon_bundle(settings: dict, key: str, data: dict) -> dict:
    bundles = settings.setdefault("addon_bundles", {})
    bundles[key] = {
        "name": data.get("name", key),
        "children": data.get("children", []),
    }
    return settings


def delete_addon_bundle(settings: dict, key: str) -> dict:
    bundles = settings.get("addon_bundles", {})
    bundles.pop(key, None)
    # Remove from any curations that reference it
    for cur in settings.get("curations_v2", {}).values():
        addons = cur.get("addons", [])
        if key in addons:
            addons.remove(key)
    return settings


# ── Demand calculation with SKU dedup ──────────────────────────────────

def compute_demand_for_curation(
    curation_data: dict,
    box_count: int,
    addon_bundles: dict,
) -> dict[str, int]:
    """Compute total SKU demand for a curation at a given box count.

    Deduplication: if a SKU appears in both recipe and addon bundles,
    the quantities are summed (not double-counted at the item level —
    they represent separate line items that both need fulfillment).
    The dedup happens at the cut-order level where we merge across
    all curations.
    """
    demand: dict[str, int] = defaultdict(int)

    # Base recipe
    for item in curation_data.get("recipe", []):
        sku = item[0] if isinstance(item, (list, tuple)) else item
        qty = int(item[1]) if isinstance(item, (list, tuple)) and len(item) > 1 else 1
        demand[sku] += qty * box_count

    # PR-CJAM
    pr = curation_data.get("pr_cjam", {})
    if isinstance(pr, dict):
        if pr.get("cheese"):
            demand[pr["cheese"]] += box_count
        if pr.get("jam"):
            demand[pr["jam"]] += box_count

    # CEX-EC (applied to ~40% of large boxes by default)
    cex_ec = curation_data.get("cex_ec", "")
    splits = curation_data.get("cexec_splits", {})
    if cex_ec and not splits:
        demand[cex_ec] += box_count
    elif splits:
        for sku, pct in splits.items():
            demand[sku] += int(box_count * float(pct) / 100)

    # Addon bundles
    for addon_key in curation_data.get("addons", []):
        bundle = addon_bundles.get(addon_key, {})
        for child in bundle.get("children", []):
            sku = child[0] if isinstance(child, (list, tuple)) else child
            qty = int(child[1]) if isinstance(child, (list, tuple)) and len(child) > 1 else 1
            demand[sku] += qty * box_count

    return dict(demand)


def compute_total_demand(
    settings: dict,
    box_counts: dict[str, int],
    target_date: str | None = None,
) -> dict[str, int]:
    """Compute total demand across all active curations with SKU dedup.

    Args:
        settings: Full settings dict
        box_counts: {curation_key: count} or auto-derived from subscriptions
        target_date: ISO date string to filter monthly curations by effective period

    Returns:
        {sku: total_demand} with duplicates merged (max qty per source, summed across curations)
    """
    curations = settings.get("curations_v2", {})
    addon_bundles = settings.get("addon_bundles", {})
    total: dict[str, int] = defaultdict(int)

    for key, data in curations.items():
        if not data.get("active", True):
            continue

        # Filter monthly curations by date
        if data.get("type") == "monthly" and target_date:
            start = data.get("effective_start", "")
            end = data.get("effective_end", "")
            if start and target_date < start:
                continue
            if end and target_date > end:
                continue

        count = box_counts.get(key, 0)
        if count <= 0:
            continue

        curation_demand = compute_demand_for_curation(data, count, addon_bundles)
        for sku, qty in curation_demand.items():
            total[sku] += qty

    return dict(total)


# ── Effective curation for a monthly box ───────────────────────────────

def get_effective_monthly_curation(
    settings: dict,
    box_type: str,
    target_date: str | None = None,
) -> dict | None:
    """Find the active monthly curation for a box type on a given date.

    E.g., for AHB-MED on 2026-04-15, returns the MS-APR2026 curation if it
    covers that date range and applies_to includes AHB-MED.
    """
    if target_date is None:
        target_date = datetime.date.today().isoformat()

    curations = settings.get("curations_v2", {})
    for key, data in curations.items():
        if data.get("type") != "monthly":
            continue
        if not data.get("active", True):
            continue
        if box_type not in data.get("applies_to", []):
            continue
        start = data.get("effective_start", "")
        end = data.get("effective_end", "")
        if start and target_date < start:
            continue
        if end and target_date > end:
            continue
        return {"key": key, **data}

    return None


# ── Validation ────────────────────────────────────────────────────────

def validate_curation(data: dict, all_curations: dict) -> list[str]:
    """Return a list of validation warnings/errors for a curation."""
    errors = []

    if not data.get("recipe"):
        errors.append("Recipe is empty")

    # Check for duplicate SKUs in recipe
    recipe_skus = [item[0] for item in data.get("recipe", []) if isinstance(item, (list, tuple))]
    seen = set()
    for sku in recipe_skus:
        if sku in seen:
            errors.append(f"Duplicate SKU in recipe: {sku}")
        seen.add(sku)

    # Check PR-CJAM uniqueness across rotation curations
    pr = data.get("pr_cjam", {})
    if isinstance(pr, dict) and pr.get("cheese"):
        cheese = pr["cheese"]
        for other_key, other in all_curations.items():
            if other.get("type") != "rotation":
                continue
            other_pr = other.get("pr_cjam", {})
            if isinstance(other_pr, dict) and other_pr.get("cheese") == cheese:
                errors.append(f"PR-CJAM cheese {cheese} already used by {other_key}")
                break

    # Monthly: check date range
    if data.get("type") == "monthly":
        if not data.get("effective_start") or not data.get("effective_end"):
            errors.append("Monthly curation requires start and end dates")
        if not data.get("applies_to"):
            errors.append("Monthly curation must apply to at least one box type")

    return errors
