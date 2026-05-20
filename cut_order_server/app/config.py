"""Constants — curations, prefixes, AHB-X family."""
from __future__ import annotations

# SKU prefixes we cut for (per user spec: TR-, MT-, AC-, CH-)
PICKABLE_PREFIXES: tuple[str, ...] = ("CH-", "MT-", "AC-", "TR-")

# Curation codes (sorted desc by length when matching, hyphen-anchored)
KNOWN_CURATIONS = {
    "MONG", "MDT", "OWC", "SPN", "ALPN", "ISUN", "HHIGH",
    "NMS", "BYO", "SS", "GEN", "MS",
}

MONTHLY_PATTERNS = {"AHB-MED", "AHB-LGE", "AHB-CMED"}

# AHB-X specialty box family — manual-entry-only in demand UI
AHB_X_SKUS: tuple[str, ...] = (
    "AHB-XBRCH", "AHB-XCUR", "AHB-XFALL", "AHB-XGRZ", "AHB-XHWN",
    "AHB-XMAS", "AHB-XMOM", "AHB-XSPR", "AHB-XTG", "AHB-XVAL",
)

# AHB- addon SKUs to ignore when finding the primary box
ADDON_AHB_PREFIXES: tuple[str, ...] = ("AHB-CUR-", "AHB-BVAL")

# PAR floor for cut order
PAR_MIN = 30

# BL-* current active seasonal/addon SKUs (used as UI pre-population list)
BL_SEED_SKUS: tuple[str, ...] = (
    "BL-BLR4", "BL-SDB", "BL-3JAM", "BL-STPD", "BL-APRL",
    "BL-RACL", "BL-SIF", "BL-SIC", "BL-IRE", "BL-SPAT",
    "AHB-BVAL",
)
