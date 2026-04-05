"""Curation constraint checking — +/-2 adjacency rule for cheese uniqueness.

Ensures PR-CJAM and CEX-EC cheese assignments don't overlap with cheeses
in neighboring curations (within 2 positions in CURATION_ORDER).
"""

from __future__ import annotations

CURATION_ORDER = ["MONG", "MDT", "OWC", "SPN", "ALPN", "ISUN", "HHIGH"]

# SKUs excluded from PR-CJAM and CEX-EC assignment candidates
ASSIGNMENT_EXCLUDE = frozenset({"CH-MAFT"})


def check_adjacency(
    curation: str,
    prcjam_cheese: str,
    cexec_cheese: str,
    recipes: dict,
    pr_cjam: dict,
    cex_ec: dict,
) -> str:
    """Check +/-2 adjacency constraint for a curation's cheese assignments.

    Returns "OK" or "CONFLICT: PR+EC" describing which slots conflict.
    """
    if curation not in CURATION_ORDER:
        return "OK"

    idx = CURATION_ORDER.index(curation)
    nearby: set[str] = set()

    for offset in (-2, -1, 1, 2):
        ni = idx + offset
        if 0 <= ni < len(CURATION_ORDER):
            nb = CURATION_ORDER[ni]
            # Recipe cheeses
            for item in recipes.get(nb, []):
                sku = item[0] if isinstance(item, (list, tuple)) else item
                if isinstance(sku, str) and sku.startswith("CH-"):
                    nearby.add(sku)
            # PR-CJAM cheese
            n_pr = pr_cjam.get(nb, {})
            if isinstance(n_pr, dict) and n_pr.get("cheese"):
                nearby.add(n_pr["cheese"])
            # CEX-EC cheese
            n_ec = cex_ec.get(nb, "")
            if n_ec:
                nearby.add(n_ec)

    violations = []
    if prcjam_cheese and prcjam_cheese in nearby:
        violations.append("PR")
    if cexec_cheese and cexec_cheese in nearby:
        violations.append("EC")

    return "CONFLICT: " + "+".join(violations) if violations else "OK"


def find_available_cheeses(
    curation: str,
    slot: str,
    all_cheeses: list[str],
    recipes: dict,
    pr_cjam: dict,
    cex_ec: dict,
) -> list[dict]:
    """Return cheeses that pass the adjacency constraint for a given slot.

    Each result: {"sku": str, "status": "OK" | "CONFLICT: ..."}
    """
    results = []
    for sku in all_cheeses:
        if sku in ASSIGNMENT_EXCLUDE:
            continue
        test_pr = sku if slot == "prcjam" else (
            pr_cjam.get(curation, {}).get("cheese", "")
            if isinstance(pr_cjam.get(curation), dict) else ""
        )
        test_ec = sku if slot == "cexec" else cex_ec.get(curation, "")
        status = check_adjacency(curation, test_pr, test_ec, recipes, pr_cjam, cex_ec)
        results.append({"sku": sku, "status": status})
    return results
