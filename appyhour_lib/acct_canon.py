"""Canonical carrier-account identity for `shipments.acct`.

🔴 THE BURN THIS FILE EXISTS TO END (2026-08-20):
`shipments.acct` held FIVE spellings for TWO real FedEx accounts —

    203738113  (5,427 rows)  and  -113  (5,770)   are the SAME account (ours, "113")
    206137911  (1,003 rows)  and  -911  (10,150)  are the SAME account (RMFG's, "911")
    ... plus 3,550 rows with acct IS NULL

UPS repeats it: `0000C411H4` (419) vs `''` (9,029) vs NULL (5,080). Any per-account cost, hub, or
service-mix attribution silently splits across the duplicate keys and understates BOTH halves.
Nothing errors; the totals just quietly disagree with the invoices.

Rules (SSOT: ShipRouting/INVOICE_INGEST_RULES.md §1):
  - Every write to `shipments.acct` goes through `canon()`. Never write a raw string from a file,
    a filename, or a spreadsheet cell.
  - NEVER invent an account. Unresolvable -> UNKNOWN, never guessed from a sibling row, a date
    range, or a hub (~/.claude/rules/never-fabricate.md).
  - A negative-suffix form (`-113`, `-911`) is a legacy artifact, never a distinct account.
  - Extend ACCOUNTS here AND in INVOICE_INGEST_RULES.md §1 in the same commit.

Kurt's ruling 2026-08-20: the routing engine's cost basis is BOTH FedEx accounts UNIONED. A cost
query filtered to one account is wrong unless it is explicitly a per-account report.
"""
from __future__ import annotations

import re

UNKNOWN = "unknown"


class Account:
    """One real carrier account: its canonical key, its owner, and every alias seen in the wild."""

    __slots__ = ("carrier", "canonical", "owner", "aliases")

    def __init__(self, carrier: str, canonical: str, owner: str, aliases: tuple):
        self.carrier = carrier
        self.canonical = canonical
        self.owner = owner
        self.aliases = aliases

    def __repr__(self):
        return f"Account({self.carrier} {self.canonical} {self.owner!r})"


# ── THE AUTHORITY. Extend here + INVOICE_INGEST_RULES.md §1, same commit. ──────────────────────
ACCOUNTS = (
    Account("FedEx", "203738113", "ours", ("113", "-113", "acct113", "203738113")),
    Account("FedEx", "206137911", "rmfg", ("911", "-911", "acct911", "206137911")),
    Account("UPS", "0000C411H4", "ours", ("C411H4", "0000C411H4", "000000C411H4")),
)

_BY_CARRIER = {}
for _a in ACCOUNTS:
    _BY_CARRIER.setdefault(_a.carrier.lower(), []).append(_a)


# 🔴 SOLE-ACCOUNT CARRIERS — a KURT-DECLARED fact, not an inference.
# Kurt 2026-08-20, asked directly whether the 14,890 unattributed UPS rows are ours:
# "yes its only our account". The owner of the accounts is the authority on which accounts exist,
# so resolving an unattributed UPS row to that account is evidence, not a guess.
#
# This is DELIBERATELY separate from canon(): canon() stays evidence-only and will never widen on
# its own. A caller must ask for sole-account resolution explicitly and label it as its own evidence
# class, so a future reader can tell "the file said so" apart from "we only have one".
#
# 🔴 Adding a second account for a carrier listed here MUST remove it from this set in the same
# commit — otherwise every unattributed row silently lands on whichever account sorts first, and the
# split we just spent a migration fixing comes back invisibly. `sole_account()` fails closed on that.
SOLE_ACCOUNT_CARRIERS = frozenset({"ups"})


def sole_account(carrier: str) -> str:
    """The carrier's only account, when Kurt has declared it has exactly one. Else UNKNOWN.

    Fails closed: if the carrier is no longer single-account, this returns UNKNOWN rather than
    picking one.
    """
    key = (carrier or "").strip().lower()
    if key not in SOLE_ACCOUNT_CARRIERS:
        return UNKNOWN
    cands = _BY_CARRIER.get(key, [])
    return cands[0].canonical if len(cands) == 1 else UNKNOWN


def _clean(raw) -> str:
    """Strip the encodings that produced the duplicate keys.

    Handles the float-ified spreadsheet form ('203738113.0'), surrounding quotes/whitespace, and
    the legacy negative-suffix form ('-113'). Returns '' for anything empty.
    """
    if raw is None:
        return ""
    s = str(raw).strip().strip('"').strip("'")
    if not s or s.lower() in ("none", "nan", "null"):
        return ""
    # openpyxl hands back numeric account numbers as floats: '203738113.0'
    if re.fullmatch(r"-?\d+\.0+", s):
        s = s.split(".")[0]
    return s


def canon(carrier: str, raw, *, filename: str = "") -> str:
    """Resolve a raw account value to its canonical key, or UNKNOWN.

    `filename` is consulted ONLY as a last resort and ONLY for an explicit `acctNNN` marker — four
    rows landed NULL from `FedEx_invoice_acct911_2026-05-06_12_50.CSV`, where the account was in
    the filename and never read into the column. That is evidence from the source, not a guess.

    Never returns a value that is not either a declared canonical key or UNKNOWN.
    """
    cands = _BY_CARRIER.get((carrier or "").strip().lower(), [])
    if not cands:
        return UNKNOWN

    s = _clean(raw)
    if s:
        for a in cands:
            if s == a.canonical:
                return a.canonical
        for a in cands:
            for alias in a.aliases:
                if s == alias or s.lstrip("-") == alias.lstrip("-"):
                    return a.canonical
        # UPS invoice numbers embed the account: 000000C411H4336 -> C411H4
        for a in cands:
            if a.carrier == "UPS" and a.canonical.lstrip("0") and a.canonical.lstrip("0") in s:
                return a.canonical
        # A digit-suffix match ('-113' style) against a canonical's tail, when unambiguous.
        tail = s.lstrip("-")
        if tail.isdigit():
            hits = [a for a in cands if a.canonical.endswith(tail)]
            if len(hits) == 1:
                return hits[0].canonical

    if filename:
        m = re.search(r"acct(\d+)", filename, re.IGNORECASE)
        if m:
            hits = [a for a in cands if a.canonical.endswith(m.group(1))]
            if len(hits) == 1:
                return hits[0].canonical

    return UNKNOWN


def owner(carrier: str, canonical: str) -> str:
    """'ours' | 'rmfg' | '' — who the account belongs to."""
    for a in _BY_CARRIER.get((carrier or "").strip().lower(), []):
        if a.canonical == canonical:
            return a.owner
    return ""


def canonical_keys(carrier: str = "") -> set:
    """Every declared canonical key, optionally for one carrier. The set an invariant asserts against."""
    if carrier:
        return {a.canonical for a in _BY_CARRIER.get(carrier.strip().lower(), [])}
    return {a.canonical for a in ACCOUNTS}


def is_declared(carrier: str, value) -> bool:
    """True when `value` is ALREADY a declared canonical key for this carrier."""
    return _clean(value) in canonical_keys(carrier)
