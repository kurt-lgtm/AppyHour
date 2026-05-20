"""
Gorgias → Google Sheets sync for shipping/fulfillment operational issues.

Pulls tickets from Gorgias where the issue type (field 13282) matches
valid shipping/order categories from the Issue & Resolution Guide,
then appends them to the UPDATE_Operational Issues tab.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from utils import OPS_SHEET_ID
from tools._gorgias_internal import get_auth, _load_settings


# ── Universal-DB tee: mirror Gorgias rows into shipping.db feedback ─────
def _tee_to_shipping_db(rows: list[list[str]]) -> int:
    """Mirror new Gorgias rows into the canonical shipping.db `feedback` table.

    Universal-DB principle: every row that goes to Sheets ALSO writes to
    the SQLite source of truth so Kori's postmortem/analytics see fresh
    data without depending on a Sheets round-trip.

    Row layout (matches sync_gorgias_to_sheet output):
      [date, contact_reason, order_num, gorgias_link, carrier,
       state, fc_tag, issue_type, resolution, comment]

    Idempotency: additive UNIQUE index on (order_number, issue_type,
    date_reported, gorgias_link) — INSERT OR IGNORE skips dupes.

    Fails silently with a log warning — Sheets is primary, SQLite tee
    is supplementary.
    """
    if not rows:
        return 0
    try:
        import sqlite3
        import sys as _sys
        _AH = r"C:/Users/Work/Claude Projects/AppyHour"
        if _AH not in _sys.path:
            _sys.path.insert(0, _AH)
        from appyhour_lib.paths import db_path
        con = sqlite3.connect(str(db_path()), timeout=10)
        try:
            # Additive: extend schema if needed. The feedback table predates
            # the tee — add gorgias_link col + unique idx if missing.
            cols = {r[1] for r in con.execute("PRAGMA table_info(feedback)").fetchall()}
            if "gorgias_link" not in cols:
                con.execute("ALTER TABLE feedback ADD COLUMN gorgias_link TEXT")
            # Unique by (gorgias_link, issue_type). One ticket can carry
            # multiple distinct issues (e.g. Missing Item + Damage); we
            # want a row per issue. The earlier composite that also
            # included date_reported allowed full ticket dupes when the
            # first-customer-message timestamp drifted between sync passes.
            con.execute("DROP INDEX IF EXISTS idx_feedback_dedup")
            con.execute("DROP INDEX IF EXISTS idx_feedback_dedup_link")
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_dedup_link_issue "
                "ON feedback(gorgias_link, issue_type) "
                "WHERE gorgias_link IS NOT NULL AND gorgias_link<>''"
            )
            now = datetime.utcnow().isoformat(timespec="seconds")
            written = 0
            for r in rows:
                # Guard against short rows (defensive)
                while len(r) < 10:
                    r = r + [""]
                date_str, contact_reason, order_num, link, carrier, state, fc_tag, issue_type, resolution, comment = r[:10]
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO feedback(
                      order_number, issue_type, date_reported, notes,
                      carrier, state, resolution, fulfillment_center,
                      synced_at, gorgias_link
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        (order_num or "").strip() or None,
                        issue_type or None,
                        date_str or None,
                        (contact_reason or comment or None),
                        carrier or None,
                        state or None,
                        resolution or None,
                        fc_tag or None,
                        now,
                        link or None,
                    ),
                )
                if cur.rowcount:
                    written += 1
            con.commit()
            return written
        finally:
            con.close()
    except Exception as e:
        logger.warning("SQLite feedback tee failed: %s", e)
        return 0

logger = logging.getLogger(__name__)


# ── Rate-limit-aware Gorgias GET helper ───────────────────────────────
# Gorgias caps at ~2 req/s per token. Retries on 429 + 5xx with
# exponential backoff, honors Retry-After header, and paces calls to
# stay under the bucket.
_GORGIAS_MIN_INTERVAL = 1.2  # seconds between calls (~0.83 req/s, conservative for cursor paging)
_gorgias_last_call: list[float] = [0.0]


def _gorgias_get(
    url: str,
    *,
    auth: tuple[str, str],
    params: dict | None = None,
    timeout: int = 30,
    max_retries: int = 10,
) -> requests.Response:
    """GET a Gorgias endpoint with pacing + 429/5xx retry."""
    # Global pacing — ensure at least _GORGIAS_MIN_INTERVAL between calls
    gap = time.monotonic() - _gorgias_last_call[0]
    if gap < _GORGIAS_MIN_INTERVAL:
        time.sleep(_GORGIAS_MIN_INTERVAL - gap)

    delay = 2.0
    for attempt in range(1, max_retries + 1):
        _gorgias_last_call[0] = time.monotonic()
        try:
            resp = requests.get(url, auth=auth, params=params, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.SSLError, requests.exceptions.Timeout) as e:
            if attempt == max_retries:
                raise
            logger.warning(
                "Gorgias connection error on %s (attempt %d/%d): %s — retry in %.1fs",
                url.rsplit("/", 1)[-1], attempt, max_retries, type(e).__name__, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 120.0)
            continue
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            sleep_for = float(retry_after) if retry_after else delay
            logger.warning(
                "Gorgias 429 on %s (attempt %d/%d) — sleeping %.1fs",
                url.rsplit("/", 1)[-1], attempt, max_retries, sleep_for,
            )
            if attempt == max_retries:
                resp.raise_for_status()
            time.sleep(sleep_for)
            delay = min(delay * 2, 120.0)
            continue
        if 500 <= resp.status_code < 600 and attempt < max_retries:
            logger.warning("Gorgias %d on %s — retry in %.1fs", resp.status_code, url, delay)
            time.sleep(delay)
            delay = min(delay * 2, 120.0)
            continue
        return resp
    return resp  # pragma: no cover — loop always returns/raises

# Target Google Sheet
SPREADSHEET_ID = OPS_SHEET_ID
TAB_NAME = "UPDATE_Operational Issues"

# Gorgias custom field IDs
FIELD_ISSUE_TYPE = "13282"  # Maps to column H (Issue Type)
FIELD_RESOLUTION = "13284"  # Maps to column I (Resolution)
FIELD_CATEGORY = "58260"  # Maps to column B (Contact Reason)

# ── Valid Issue Type prefixes (column H) from original sheet ──────────
VALID_ISSUE_PREFIXES = (
    "Shipping::Delayed in transit",
    "Shipping::Lost in Transit",
    "Shipping::Damaged in transit",
    "Shipping::cannot be delivered",
    "Order::Missing item",
    "Order::Missing tasting guide",
    "Order::Substitute complaint",
    "Order::Wrong Order",
    "Order::Wrong item",
)

# Food safety issue types → go to UPDATE_Food Safety tab (not operational issues)
FOOD_SAFETY_ISSUE_PREFIXES = (
    "Order::Spoiled Item",
    "Order::Quality Complaint",
)

# Explicitly excluded issue types (pulled from Gorgias but not valid for this sheet)
EXCLUDED_ISSUE_TYPES = (
    "Shipping::Change Address",
    "Shipping::Other",
    "Order::Status",
    "Order::Edit",
    "Order::Order Cancel",
    "Order::Payment",
    "Order::Cancel",
    "Order::Missing item::Confusion about contents",
)

# ── Tag → Issue Type inference (fallback when field 13282 is wrong) ───
# Gorgias agents often set the wrong issue type dropdown but apply
# accurate tags.  These mappings let the sync recover the real issue.

# Specific reship tags map directly to issue types
TAG_ISSUE_MAP: dict[str, str] = {
    "reship - misdelivered": "Shipping::Lost in Transit/Misdelivered",
    "reship - arrived warm": "Shipping::Damaged in transit::Arrived Warm",
    "reship - delayed": "Shipping::Delayed in transit",
    "reship - damaged": "Shipping::Damaged in transit::Other",
    "reship - missing item": "Order::Missing item",
    "reship - wrong order": "Order::Wrong Order",
    "reship - wrong item": "Order::Wrong item",
}

# Gate tags — at least one must be present to trigger inference.
# Includes meta-tags (reship/order issue/shipping issue/not-received) plus
# issue-specific tags so a ticket tagged only "Arrived Warm" (without a
# meta-tag) still passes. Prevents the common CS miss where staff apply
# the specific tag but forget the umbrella tag.
OPERATIONAL_GATE_TAGS = frozenset({
    # Meta-tags
    "reship",
    "order issue",
    "shipping issue",
    "not-received",
    # Issue-specific tags (independent gates)
    "arrived warm",
    "warm delivery",
    "melted",
    "melted gel",
    "delayed",
    "delayed in transit",
    "misdelivered",
    "misdelivery",
    "lost",
    "lost in transit",
    "damaged",
    "damaged in transit",
    "gel pack leaked",
    "gel pack burst",
    "missing item",
    "missing items",
    "missing cheese",
    "missing meat",
    "missing jam",
    "wrong item",
    "wrong order",
    "substitute complaint",
    "substitution complaint",
})

# Subject/message keyword patterns (checked when tags aren't specific enough)
KEYWORD_ISSUE_MAP: list[tuple[tuple[str, ...], str]] = [
    (("arrived warm", "melted gel", "warm delivery"), "Shipping::Damaged in transit::Arrived Warm"),
    (("gel pack burst", "gel pack leak"), "Shipping::Damaged in transit::Other"),
    (("misdelivered", "misdelivery", "wrong address delivered"), "Shipping::Lost in Transit/Misdelivered"),
    (("lost in transit", "never received", "did not receive", "stolen"), "Shipping::Lost in Transit/Misdelivered"),
    (("delayed in transit", "delayed", "days in transit"), "Shipping::Delayed in transit"),
    (("cannot be delivered", "unable to deliver", "undeliverable"), "Shipping::cannot be delivered"),
    (("damaged in transit", "damaged box", "broken"), "Shipping::Damaged in transit::Other"),
    (("missing item", "missing cheese", "missing meat"), "Order::Missing item"),
    (("wrong order", "wrong box", "wrong curation"), "Order::Wrong Order"),
    (("wrong item", "incorrect item", "substitut"), "Order::Substitute complaint"),
]


def _infer_issue_type_from_tags(ticket: dict) -> str:
    """Infer issue type from ticket tags and subject when field 13282 is wrong.

    Returns a VALID_ISSUE_PREFIXES-compatible string, or "" if no match.
    """
    raw_tags = ticket.get("tags", [])
    if not isinstance(raw_tags, list):
        return ""
    tag_names = [
        (t.get("name", "") if isinstance(t, dict) else str(t)).lower()
        for t in raw_tags
    ]

    # Gate: only infer if at least one operational tag is present
    if not any(gt in tag_names for gt in OPERATIONAL_GATE_TAGS):
        # Also check for any "reship - *" tag
        if not any(t.startswith("reship -") or t.startswith("reship-") for t in tag_names):
            return ""

    # 1) Check specific reship tags first (most reliable)
    for tag in tag_names:
        tag_clean = tag.strip()
        if tag_clean in TAG_ISSUE_MAP:
            return TAG_ISSUE_MAP[tag_clean]
        # Handle variations like "reship-misdelivered" (no space)
        tag_dashed = tag_clean.replace("-", " - ").replace("  ", " ")
        if tag_dashed in TAG_ISSUE_MAP:
            return TAG_ISSUE_MAP[tag_dashed]

    # 2) Fall back to subject + tag keyword matching
    subject = (ticket.get("subject") or "").lower()
    search_text = subject + " " + " ".join(tag_names)

    for keywords, issue_type in KEYWORD_ISSUE_MAP:
        if any(kw in search_text for kw in keywords):
            return issue_type

    # 3) Generic "reship" or "not-received" without specifics
    if "not-received" in tag_names:
        return "Shipping::Lost in Transit/Misdelivered"

    # Has operational gate tags but couldn't determine specific type
    return ""


# ── Valid Resolution prefixes (column I) from original sheet ──────────
VALID_RESOLUTION_PREFIXES = (
    "Full Reship",
    "Partial Reship",
    "Reship Box",
    "Comp Item",
    "Refund Order",
    "Credit Next Box",
    "Full Refund",
    "FullReship",
)

# Excluded resolutions (not operational issue resolutions)
EXCLUDED_RESOLUTIONS = (
    "Order Updated",
    "Information Given",
    "Subscription Cancelled",
    "Subscription Updated",
    "Order Cancelled",
    "Sub & Order Canceled",
    "No action",
    "Other",
)

# ── Fulfillment center tag mapping (from Shopify routing tags) ────────
FC_TAG_MAP: dict[str, str] = {}
# GRIPCA + COG decommissioned Feb 2026. RMFG is the sole active FC.
# Ticket-tag based FC inference is disabled — caller falls back to RMFG default.


def _gorgias_auth() -> tuple[tuple[str, str], str]:
    """Delegate to shared cached auth."""
    return get_auth()


def _get_first_customer_message_date(ticket: dict, auth: tuple[str, str] | None = None, base_url: str | None = None) -> str:
    """Get the datetime of the first customer message in a ticket.

    Falls back to ticket created_datetime if messages can't be fetched.
    Returns ISO datetime string.
    """
    customer_email = (ticket.get("customer", {}).get("email", "") or "").lower()
    if auth and base_url:
        try:
            resp = _gorgias_get(
                f"{base_url}/tickets/{ticket['id']}/messages",
                auth=auth,
                params={"limit": 10, "order_by": "created_datetime:asc"},
            )
            if resp.status_code == 200:
                for m in resp.json().get("data", []):
                    msg_type = m.get("source", {}).get("type", "")
                    # Skip internal notes
                    if msg_type == "internal-note":
                        continue
                    # Check if sender is the customer (not an agent)
                    sender_email = (m.get("sender", {}).get("email", "") or "").lower()
                    from_agent = m.get("from_agent", None)
                    if from_agent is False:
                        return m.get("created_datetime", ticket.get("created_datetime", ""))
                    if customer_email and sender_email == customer_email:
                        return m.get("created_datetime", ticket.get("created_datetime", ""))
                    # If no from_agent field, check sender isn't a support address
                    if (
                        from_agent is None
                        and sender_email
                        and not sender_email.endswith("@appyhourbox.com")
                        and not sender_email.endswith("@gorgias.com")
                    ):
                        return m.get("created_datetime", ticket.get("created_datetime", ""))
        except Exception:
            pass
    return ticket.get("created_datetime", "")


def _extract_order_from_text(text: str) -> str:
    """Extract order number from text via regex.

    Only matches Shopify-style order numbers (#NNNNN or #NNNNNN).
    Avoids false positives from phone numbers, zip codes, etc.
    """
    if not text:
        return ""
    # Look for explicit order references first
    match = re.search(r"[Oo]rder\s*#?\s*(\d{4,6})\b", text)
    if match:
        return f"#{match.group(1)}"
    # Then look for standalone #NNNNN patterns (not inside URLs, phone numbers, etc.)
    match = re.search(r"(?<!\d)#(\d{4,6})\b", text)
    if match:
        return f"#{match.group(1)}"
    return ""


def _resolve_original_order(order_num: str) -> str:
    """If order_num is a reship, return the customer's original damaged order.

    The sheet must track the ORIGINAL order for carrier attribution —
    writing the reship number misattributes the failure to the replacement
    carrier instead of the one that actually caused the incident.

    Returns order_num unchanged if it's not a reship or if the original
    can't be located. Looks up Shopify by name, checks for "reship" tag,
    then fetches the customer's most recent non-reship fulfilled order.
    """
    if not order_num:
        return order_num
    shopify = _shopify_order_by_name(order_num)
    if not shopify:
        return order_num
    tags = (shopify.get("tags", "") or "").lower()
    if "reship" not in tags:
        return order_num
    email = shopify.get("email", "") or (shopify.get("customer") or {}).get("email", "")
    if not email:
        return order_num
    original = _shopify_order_by_email(email)
    if original and original.get("name"):
        return original["name"]
    return order_num


def _extract_order_number(ticket: dict, gorgias_auth: tuple[str, str] | None = None, gorgias_base: str | None = None) -> str:
    """Extract order number from ticket subject, messages, or Shopify lookup.

    Tries in order:
    1. Subject line
    2. First few message bodies
    3. Shopify order lookup by customer email
    """
    # 1. Subject
    order = _extract_order_from_text(ticket.get("subject", ""))
    if order:
        return order

    # 2. Message bodies (requires API call)
    if gorgias_auth and gorgias_base:
        try:
            resp = _gorgias_get(
                f"{gorgias_base}/tickets/{ticket['id']}/messages",
                auth=gorgias_auth,
                params={"limit": 5},
            )
            if resp.status_code == 200:
                for m in resp.json().get("data", []):
                    body = m.get("body_text", "") or ""
                    order = _extract_order_from_text(body[:1000])
                    if order:
                        return order
        except Exception:
            logger.debug("Failed to fetch messages for ticket %s", ticket.get("id"), exc_info=True)

    # 3. Shopify lookup by customer email
    customer_email = ticket.get("customer", {}).get("email", "")
    if customer_email:
        order = _shopify_latest_order(customer_email)
        if order:
            return order

    return ""


_shopify_client: object | None = None


def _get_shopify_client() -> object | None:
    """Get or create a ShopifyClient for order lookups."""
    global _shopify_client
    if _shopify_client is None:
        try:
            from gel_pack_shopify import ShopifyClient

            settings = _load_settings()
            store = settings.get("store_url", "")
            cid = settings.get("shopify_client_id", "")
            csecret = settings.get("shopify_secret", "")
            if store and cid and csecret:
                _shopify_client = ShopifyClient(store, cid, csecret)
        except Exception:
            pass
    return _shopify_client


def _shopify_latest_order(email: str) -> str:
    """Look up the customer's latest shipped/fulfilled non-reship order in Shopify."""
    try:
        client = _get_shopify_client()
        if not client:
            return ""
        for fs in ("shipped", "fulfilled"):
            resp = client._get(
                "orders.json",
                params={
                    "email": email,
                    "status": "any",
                    "fulfillment_status": fs,
                    "limit": 5,
                    "order": "created_at desc",
                    "fields": "name,tags",
                },
            )
            orders = resp.get("orders", [])
            for order in orders:
                tags = (order.get("tags", "") or "").lower()
                if "reship" not in tags:
                    return order.get("name", "")
    except Exception:
        pass
    return ""


# Gorgias view ID for the operational issues view
GORGIAS_OPS_VIEW_ID = "238613"


def _extract_gorgias_link(ticket: dict, subdomain: str = "appyhour") -> str:
    return f"https://{subdomain}.gorgias.com/app/views/{GORGIAS_OPS_VIEW_ID}/{ticket['id']}"


def _matches_valid_prefix(value: str, prefixes: tuple[str, ...]) -> bool:
    val_lower = value.lower()
    return any(val_lower.startswith(p.lower()) for p in prefixes)


def _extract_state_from_tags(ticket: dict) -> str:
    """Try to extract destination state from ticket tags or subject."""
    raw_tags = ticket.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [
        t.get("name", "") if isinstance(t, dict) else str(t)
        for t in raw_tags
    ]
    for tag in tags:
        for state in US_STATES:
            if state.lower() in tag.lower():
                return state
    return ""


def _extract_fc_tag(ticket: dict) -> str:
    """Try to extract fulfillment center from ticket tags."""
    raw_tags = ticket.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [
        (t.get("name", "") if isinstance(t, dict) else str(t)).lower()
        for t in raw_tags
    ]
    for tag in tags:
        for key, fc in FC_TAG_MAP.items():
            if key in tag:
                return fc
    return ""


US_STATES = [
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
]


def sync_gorgias_to_sheet(days_back: int = 14, dry_run: bool = False) -> dict[str, object]:
    """Pull shipping/fulfillment tickets from Gorgias and append to Google Sheet.

    Args:
        days_back: How many days back to pull tickets (default 14).
            Captures late-arriving complaints: ~90% of issues land by T+5,
            99% by T+13. 14-day lookback covers the long tail without
            sluggish performance.
        dry_run: If True, return rows without writing to sheet.

    Returns dict with summary and rows.
    """
    auth, base_url = _gorgias_auth()
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")

    # Read existing order numbers from the sheet to avoid duplicates
    from google_integration import GoogleIntegration

    settings = _load_settings()
    creds_path = settings.get("google_credentials_path", "")
    if not creds_path or not os.path.exists(creds_path):
        creds_path = str(Path(__file__).resolve().parent.parent.parent / "shipping-perfomance-review-accd39ac4b78.json")
    gclient = GoogleIntegration(creds_path)

    # Read FULL rows (A:J) so we can upsert in place when a ticket already
    # exists. Splitting sync (append) and enrich (update) used to create
    # duplicates whenever a row's link cell was blank at read-time.
    # Key by (gorgias_link, issue_type) — one ticket can carry multiple
    # distinct issues, so each (link, issue) pair is its own row.
    existing_full = gclient.read_sheet(SPREADSHEET_ID, f"'{TAB_NAME}'!A:J")
    existing_orders: set[str] = set()
    # (link, issue_type) → (1-based sheet row index, padded 10-col row values)
    link_to_row: dict[tuple[str, str], tuple[int, list[str]]] = {}
    for idx, raw in enumerate(existing_full):
        if idx == 0:
            continue  # header
        padded = list(raw) + [""] * (10 - len(raw))
        padded = padded[:10]
        if padded[2].strip():
            existing_orders.add(padded[2].strip())
        if padded[3].strip():
            link_to_row[(padded[3].strip(), padded[7].strip())] = (idx + 1, padded)
    existing_links: set[str] = {k[0] for k in link_to_row}

    # Paginate Gorgias tickets (filter by date client-side)
    since_dt = datetime.now() - timedelta(days=days_back)
    cursor = None
    new_rows = []
    upsert_updates: list[dict] = []  # batchUpdate cell-level patches
    upserted = 0
    checked = 0
    skipped_dup = 0
    skipped_tag = 0
    done = False

    for _ in range(400):  # up to 20000 tickets — pagination stops at date cutoff
        params = {
            "limit": 50,
            "order_by": "created_datetime:desc",
        }
        if cursor:
            params["cursor"] = cursor

        resp = _gorgias_get(f"{base_url}/tickets", auth=auth, params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            break

        for t in items:
            # Stop paginating once we pass the date cutoff
            created_str = t.get("created_datetime", "")
            try:
                ticket_dt = datetime.fromisoformat(created_str.replace("+00:00", "+00:00").replace("Z", "+00:00"))
                ticket_dt = ticket_dt.replace(tzinfo=None)
            except (ValueError, AttributeError):
                ticket_dt = datetime.now()
            if ticket_dt < since_dt:
                done = True
                break
            checked += 1
            cf = t.get("custom_fields", {})

            # Get issue type from field 13282
            issue_type = cf.get(FIELD_ISSUE_TYPE, {}).get("value", "")
            inferred = False
            if not issue_type or not _matches_valid_prefix(issue_type, VALID_ISSUE_PREFIXES):
                # Field is empty or set to wrong value — try tag-based inference
                issue_type = _infer_issue_type_from_tags(t)
                if not issue_type:
                    skipped_tag += 1
                    continue
                inferred = True
            # Skip explicitly excluded issue types (only for non-inferred)
            if not inferred and any(issue_type.lower().startswith(ex.lower()) for ex in EXCLUDED_ISSUE_TYPES):
                skipped_tag += 1
                continue

            # Get resolution from field 13284 (clear if excluded)
            resolution = cf.get(FIELD_RESOLUTION, {}).get("value", "")
            if resolution in EXCLUDED_RESOLUTIONS:
                resolution = ""

            # Get contact reason from field 58260
            contact_reason = cf.get(FIELD_CATEGORY, {}).get("value", "")

            # Extract order number (subject → messages → Shopify)
            order_num = _extract_order_number(t, gorgias_auth=auth, gorgias_base=base_url)

            # If the extracted order is a reship, swap to the original
            # damaged order. Carrier attribution must anchor on the order
            # that actually failed, not its replacement.
            if order_num:
                original = _resolve_original_order(order_num)
                if original and original != order_num:
                    order_num = original

            # Upsert by (gorgias_link, issue_type). A ticket can produce
            # multiple rows when it carries multiple distinct issue types.
            # Order numbers are NOT unique: same order → multiple tickets.
            gorgias_link = _extract_gorgias_link(t)
            existing_match = (
                link_to_row.get((gorgias_link, issue_type)) if gorgias_link else None
            )

            # Shopify lookup for Carrier, State, FC Tag (single pass — no enrich needed)
            import time as _time_sync
            carrier = ""
            state = _extract_state_from_tags(t)
            fc_tag = _extract_fc_tag(t)
            shopify_order = None
            if order_num:
                shopify_order = _shopify_order_by_name(order_num)
                _time_sync.sleep(0.3)
            if shopify_order:
                carrier = _extract_carrier_from_shopify(shopify_order)
                if not state:
                    state = _extract_state_from_shopify(shopify_order)
                if not fc_tag:
                    fc_tag = _extract_fc_from_shopify_tags(shopify_order)
            # Default FC to RMFG — sole active FC since Feb 2026
            if not fc_tag:
                fc_tag = "RMFG"

            # Format date as Month-DD (based on first customer message, not ticket creation)
            first_msg_dt = _get_first_customer_message_date(t, auth=auth, base_url=base_url)

            _time_sync.sleep(0.3)  # rate limit after message fetch
            try:
                dt = datetime.fromisoformat(first_msg_dt.replace("Z", "+00:00"))
                dt = dt.replace(tzinfo=None)  # strip timezone for strftime
                date_str = f"{dt.strftime('%B')}-{dt.day}"
            except (ValueError, AttributeError):
                date_str = first_msg_dt[:10] if first_msg_dt else ""

            # Build row matching CSV columns:
            # Date, Contact Reason, Order #, Gorgias Link, Carrier,
            # Destination State, Fulfillment Center Tag, Issue Type, Resolution, Comment
            row = [
                date_str,
                contact_reason,
                order_num,
                gorgias_link,
                carrier,
                state,
                fc_tag,
                issue_type,
                resolution,
                "",  # Comment
            ]

            if existing_match is not None:
                sheet_row_num, existing_padded = existing_match
                if sheet_row_num < 0:
                    # Same ticket appeared twice in this run's pagination —
                    # ignore the second hit; the first will be appended.
                    skipped_dup += 1
                    continue
                # Row already in sheet — fill any blank field with fresh data.
                # Never overwrite an existing non-blank value (preserves the
                # Comment column + manual edits).
                patched = list(existing_padded)
                changed_cols: list[int] = []
                for col_idx in range(10):
                    if not patched[col_idx].strip() and row[col_idx].strip():
                        patched[col_idx] = row[col_idx]
                        changed_cols.append(col_idx)
                if changed_cols:
                    # One update per contiguous range — cheaper than per-cell.
                    upsert_updates.append(
                        {
                            "range": f"'{TAB_NAME}'!A{sheet_row_num}:J{sheet_row_num}",
                            "values": [patched],
                        }
                    )
                    upserted += 1
                    link_to_row[(gorgias_link, issue_type)] = (sheet_row_num, patched)
                else:
                    skipped_dup += 1
                continue

            new_rows.append(row)
            if order_num:
                existing_orders.add(order_num)
            if gorgias_link:
                existing_links.add(gorgias_link)
                # Future iterations in this same run can't re-append this
                # (link, issue_type). row_num is unknown until append
                # finishes; mark with sentinel.
                link_to_row[(gorgias_link, issue_type)] = (-1, row)

        if done:
            break
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    # Apply in-place upsert patches BEFORE appending so appended rows can't
    # collide with patched ranges.
    if upsert_updates and not dry_run:
        gclient._sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": u["range"], "values": u["values"]}
                    for u in upsert_updates
                ],
            },
        ).execute()

    # Write to sheet (reverse so oldest-first, since Gorgias paginates newest-first)
    new_rows.reverse()
    append_result = None
    if new_rows and not dry_run:
        svc = gclient._sheets
        append_result = (
            svc.spreadsheets()
            .values()
            .append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{TAB_NAME}'!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="OVERWRITE",
                body={"values": new_rows},
            )
            .execute()
        )

        # Verify rows were actually written
        updated_range = append_result.get("updates", {}).get("updatedRange", "")
        updated_rows = append_result.get("updates", {}).get("updatedRows", 0)
        if updated_rows != len(new_rows):
            raise RuntimeError(
                f"Append verification failed: expected {len(new_rows)} rows, "
                f"API reported {updated_rows}. Range: {updated_range}"
            )

    # Universal-DB tee: mirror to shipping.db.feedback so Kori postmortem
    # sees fresh data without a Sheets round-trip.
    tee_written = _tee_to_shipping_db(new_rows) if new_rows else 0

    return {
        "checked": checked,
        "new_rows": len(new_rows),
        "upserted_in_place": upserted,
        "skipped_duplicate": skipped_dup,
        "skipped_invalid_tag": skipped_tag,
        "dry_run": dry_run,
        "rows": new_rows,
        "append_range": append_result.get("updates", {}).get("updatedRange", "") if append_result else None,
        "sqlite_tee_written": tee_written,
    }


def _search_gorgias_by_order(
    order_num: str,
    auth: tuple[str, str],
    base_url: str,
    customer_email: str = "",
) -> dict | None:
    """Search Gorgias for a ticket matching the given order.

    Strategy:
    1. If customer_email is provided, find the Gorgias customer by email
       and return their most recent ticket with a valid issue type.
    2. Fall back to q= text search by order number.

    Returns the first matching ticket dict, or None.
    """
    search_term = order_num.lstrip("#")
    if not search_term and not customer_email:
        return None

    # Strategy 1: Search by customer email (most reliable)
    if customer_email:
        try:
            resp = _gorgias_get(
                f"{base_url}/customers",
                auth=auth,
                params={"email": customer_email, "limit": 1},
            )
            if resp.status_code == 200:
                customers = resp.json().get("data", [])
                if customers:
                    cust_id = customers[0]["id"]
                    resp2 = _gorgias_get(
                        f"{base_url}/tickets",
                        auth=auth,
                        params={
                            "customer_id": cust_id,
                            "limit": 5,
                            "order_by": "created_datetime:desc",
                        },
                    )
                    if resp2.status_code == 200:
                        tickets = resp2.json().get("data", [])
                        # Prefer ticket with valid issue type
                        for t in tickets:
                            cf = t.get("custom_fields", {})
                            issue = cf.get(FIELD_ISSUE_TYPE, {}).get("value", "")
                            if issue and _matches_valid_prefix(issue, VALID_ISSUE_PREFIXES):
                                return t
                        # Otherwise return most recent
                        if tickets:
                            return tickets[0]
        except Exception:
            logger.warning("Gorgias customer lookup failed for %s", customer_email, exc_info=True)

    # Strategy 2: Text search by order number
    if search_term:
        try:
            resp = _gorgias_get(
                f"{base_url}/tickets",
                auth=auth,
                params={
                    "q": search_term,
                    "limit": 10,
                    "order_by": "created_datetime:desc",
                },
            )
            if resp.status_code == 200:
                for t in resp.json().get("data", []):
                    subj = t.get("subject", "")
                    if search_term in subj:
                        return t
                tickets = resp.json().get("data", [])
                if tickets:
                    return tickets[0]
        except Exception:
            logger.warning("Gorgias search failed for order %s", order_num, exc_info=True)

    return None


def _shopify_order_by_name(order_name: str) -> dict | None:
    """Look up a Shopify order by its name (e.g., '#12345').

    Returns the order dict with tags, shipping_address, fulfillments, and email.
    """
    try:
        client = _get_shopify_client()
        if not client:
            return None
        name = order_name if order_name.startswith("#") else f"#{order_name}"
        resp = requests.get(
            client._url("orders.json"),
            headers=client._headers(),
            params={
                "name": name,
                "status": "any",
                "limit": 1,
                "fields": "id,name,tags,shipping_address,fulfillments,email,customer",
            },
            timeout=30,
        )
        if resp.status_code == 200:
            orders = resp.json().get("orders", [])
            if orders:
                return orders[0]
    except Exception:
        logger.warning("Shopify order lookup failed for %s", order_name, exc_info=True)
    return None


def _shopify_order_by_email(email: str) -> dict | None:
    """Look up the most recent fulfilled non-reship Shopify order by customer email."""
    try:
        client = _get_shopify_client()
        if not client:
            return None
        for fs in ("shipped", "fulfilled"):
            resp = requests.get(
                client._url("orders.json"),
                headers=client._headers(),
                params={
                    "email": email,
                    "status": "any",
                    "fulfillment_status": fs,
                    "limit": 5,
                    "fields": "id,name,tags,shipping_address,fulfillments,email,customer",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                orders = resp.json().get("orders", [])
                for order in orders:
                    tags = (order.get("tags", "") or "").lower()
                    if "reship" not in tags:
                        return order
    except Exception:
        logger.warning("Shopify email lookup failed for %s", email, exc_info=True)
    return None


def _extract_carrier_from_shopify(order: dict) -> str:
    """Extract carrier name from Shopify order fulfillments."""
    for ful in order.get("fulfillments", []):
        company = ful.get("tracking_company", "")
        if company:
            return company
    return ""


def _extract_fc_from_shopify_tags(order: dict) -> str:
    """FC tag is always RMFG (per user 2026-05-15). GRIPCA/COG decommissioned."""
    return "RMFG"


STATE_CODE_TO_NAME = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}


def _extract_state_from_shopify(order: dict) -> str:
    """Extract destination state from Shopify order shipping address."""
    addr = order.get("shipping_address") or {}
    province = addr.get("province", "")
    if province:
        return province
    code = addr.get("province_code", "")
    if code:
        return STATE_CODE_TO_NAME.get(code.upper(), "")
    return ""


def enrich_incomplete_rows(dry_run: bool = False) -> dict[str, object]:
    """Enrich rows in UPDATE_Operational Issues that have order numbers but
    missing fields (Gorgias Link, Carrier, State, FC Tag, Issue Type, Resolution).

    Reads all rows, identifies incomplete ones, looks up data from Gorgias
    and Shopify APIs, and updates the sheet in-place.

    Args:
        dry_run: If True, return enrichment plan without writing.

    Returns dict with summary and enriched rows.
    """
    import time as _time

    auth, base_url = _gorgias_auth()
    subdomain = _load_settings().get("gorgias_subdomain", "appyhour")

    # Read all rows
    from google_integration import GoogleIntegration

    settings = _load_settings()
    creds_path = settings.get("google_credentials_path", "")
    if not creds_path or not os.path.exists(creds_path):
        creds_path = str(Path(__file__).resolve().parent.parent.parent / "shipping-perfomance-review-accd39ac4b78.json")
    gclient = GoogleIntegration(creds_path)

    all_rows = gclient.read_sheet(SPREADSHEET_ID, f"'{TAB_NAME}'!A:J")
    if not all_rows:
        return {"error": "No data found"}

    enriched = []
    updates = []  # (range, values) for batch update

    for row_idx, row in enumerate(all_rows):
        if row_idx == 0:
            continue  # skip header
        while len(row) < 10:
            row.append("")

        date_str, contact_reason, order_num, gorgias_link, carrier, state, fc_tag, issue_type, resolution, comment = (
            row[:10]
        )

        missing_fields = []
        if not gorgias_link.strip():
            missing_fields.append("gorgias_link")
        if not carrier.strip():
            missing_fields.append("carrier")
        if not state.strip():
            missing_fields.append("state")
        if not fc_tag.strip():
            missing_fields.append("fc_tag")
        if not issue_type.strip():
            missing_fields.append("issue_type")
        if not resolution.strip():
            missing_fields.append("resolution")

        if not missing_fields:
            continue

        has_order = bool(order_num and order_num.strip())
        has_link = bool(gorgias_link and gorgias_link.strip())

        # Skip rows with neither order number nor Gorgias link
        if not has_order and not has_link:
            continue

        shopify_order = None
        customer_email = ""
        ticket = None

        if has_order:
            # Look up from Shopify first (need email for Gorgias search)
            shopify_order = _shopify_order_by_name(order_num)
            _time.sleep(0.3)  # rate limit
            if shopify_order:
                customer_email = shopify_order.get("email", "") or shopify_order.get("customer", {}).get("email", "")
                # If this order is a reship, find the original order instead
                order_tags = (shopify_order.get("tags", "") or "").lower()
                if "reship" in order_tags and customer_email:
                    original_order = _shopify_order_by_email(customer_email)
                    _time.sleep(0.3)
                    if original_order:
                        shopify_order = original_order
                        # Update order number to the original
                        order_num = original_order.get("name", order_num)

        # Look up Gorgias ticket (by order or by ticket ID from link)
        if has_link and not has_order:
            # Extract ticket ID from Gorgias link and fetch directly
            tid_match = re.search(r"/(\d+)(?:[?#]|\s*$)", gorgias_link.strip())
            if tid_match:
                try:
                    resp = _gorgias_get(
                        f"{base_url}/tickets/{tid_match.group(1)}",
                        auth=auth,
                    )
                    if resp.status_code == 200:
                        ticket = resp.json()
                        # Get customer email from ticket
                        customer_email = ticket.get("customer", {}).get("email", "")
                        _time.sleep(0.3)
                except Exception:
                    pass
            # Use email to find Shopify order
            if customer_email and not shopify_order:
                shopify_order = _shopify_order_by_email(customer_email)
                _time.sleep(0.3)
        elif any(f in missing_fields for f in ("gorgias_link", "issue_type", "resolution", "state", "fc_tag")):
            ticket = _search_gorgias_by_order(
                order_num,
                auth,
                base_url,
                customer_email=customer_email,
            )
            _time.sleep(0.3)  # rate limit

        # Build enriched values
        new_values = list(row[:10])
        fields_filled = []

        # Order # (col C, index 2) — from Shopify email lookup or reship correction
        if shopify_order:
            original_name = shopify_order.get("name", "")
            current_order = new_values[2].strip()
            if not current_order and original_name:
                new_values[2] = original_name
                fields_filled.append("order_num")
            elif current_order and original_name and current_order != original_name:
                # Order was replaced (reship → original)
                new_values[2] = original_name
                fields_filled.append("order_num")

        # Gorgias Link (col D, index 3)
        if not new_values[3].strip() and ticket:
            new_values[3] = _extract_gorgias_link(ticket, subdomain)
            fields_filled.append("gorgias_link")

        # Carrier (col E, index 4) — from Shopify
        if not new_values[4].strip() and shopify_order:
            carrier_val = _extract_carrier_from_shopify(shopify_order)
            if carrier_val:
                new_values[4] = carrier_val
                fields_filled.append("carrier")

        # Destination State (col F, index 5) — Shopify first, Gorgias fallback
        if not new_values[5].strip():
            if shopify_order:
                state_val = _extract_state_from_shopify(shopify_order)
                if state_val:
                    new_values[5] = state_val
                    fields_filled.append("state")
            if not new_values[5].strip() and ticket:
                state_val = _extract_state_from_tags(ticket)
                if state_val:
                    new_values[5] = state_val
                    fields_filled.append("state")

        # FC Tag (col G, index 6) — Shopify tags first, Gorgias fallback
        if not new_values[6].strip():
            if shopify_order:
                fc_val = _extract_fc_from_shopify_tags(shopify_order)
                if fc_val:
                    new_values[6] = fc_val
                    fields_filled.append("fc_tag")
            if not new_values[6].strip() and ticket:
                fc_val = _extract_fc_tag(ticket)
                if fc_val:
                    new_values[6] = fc_val
                    fields_filled.append("fc_tag")
            # Default to RMFG if still empty
            if not new_values[6].strip():
                new_values[6] = "RMFG"
                fields_filled.append("fc_tag")

        # Issue Type (col H, index 7) — from Gorgias
        if not new_values[7].strip() and ticket:
            cf = ticket.get("custom_fields", {})
            it = cf.get(FIELD_ISSUE_TYPE, {}).get("value", "")
            if it and _matches_valid_prefix(it, VALID_ISSUE_PREFIXES):
                new_values[7] = it
                fields_filled.append("issue_type")

        # Resolution (col I, index 8) — from Gorgias
        if not new_values[8].strip() and ticket:
            cf = ticket.get("custom_fields", {})
            res = cf.get(FIELD_RESOLUTION, {}).get("value", "")
            if res and res not in EXCLUDED_RESOLUTIONS:
                new_values[8] = res
                fields_filled.append("resolution")

        if fields_filled:
            sheet_row = row_idx + 1  # 1-indexed
            enriched.append(
                {
                    "row": sheet_row,
                    "order": order_num,
                    "filled": fields_filled,
                    "values": new_values,
                }
            )
            updates.append(
                {
                    "range": f"'{TAB_NAME}'!A{sheet_row}:J{sheet_row}",
                    "values": [new_values],
                }
            )

    # Batch update the sheet
    if updates and not dry_run:
        sheets_svc = gclient._sheets
        sheets_svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": updates,
            },
        ).execute()

    return {
        "total_rows": len(all_rows) - 1,
        "rows_enriched": len(enriched),
        "dry_run": dry_run,
        "enriched": [{"row": e["row"], "order": e["order"], "filled": e["filled"]} for e in enriched[:20]],
    }


# ── Food Safety tab ──────────────────────────────────────────────────────

FOOD_SAFETY_TAB = "UPDATE_Food Safety"


def _extract_tracking_from_shopify(order: dict) -> str:
    """Extract first tracking number from Shopify order fulfillments."""
    for ful in order.get("fulfillments", []):
        num = ful.get("tracking_number", "")
        if num:
            return num
    return ""


def _fetch_first_customer_message_body(ticket: dict, auth: tuple[str, str], base_url: str) -> str:
    """Fetch the first customer message body from a Gorgias ticket.

    Returns the first 1000 chars of the first non-internal-note message body,
    or empty string on failure.
    """
    try:
        resp = _gorgias_get(
            f"{base_url}/tickets/{ticket['id']}/messages",
            auth=auth,
            params={"limit": 5, "order_by": "created_datetime:asc"},
        )
        if resp.status_code == 200:
            for m in resp.json().get("data", []):
                if m.get("source", {}).get("type") == "internal-note":
                    continue
                return (m.get("body_text", "") or "")[:1000]
    except Exception:
        logger.debug("Failed to fetch messages for ticket %s", ticket.get("id"), exc_info=True)
    return ""


def _extract_concern_from_text(subject: str, body: str, ticket: dict) -> str:
    """Extract perceived food safety concern from ticket subject + message body.

    Returns a short description like 'Mold on Farmstead Fenugreek Gouda'.
    """
    # Look for common food safety patterns in subject + body
    text = f"{subject} {body}"

    # Pattern: "Mold on <product>"
    match = re.search(r"[Mm]old\s+on\s+(.+?)(?:\.|,|\n|$)", text)
    if match:
        return f"Mold on {match.group(1).strip()}"

    # Pattern: "spoiled" / "rotten"
    match = re.search(r"(spoiled|rotten)\s+(.+?)(?:\.|,|\n|$)", text, re.IGNORECASE)
    if match:
        return f"{match.group(1).capitalize()} {match.group(2).strip()}"

    # Pattern: "expir" (expired, expires, expiry) — capture product name only
    match = re.search(
        r"(expir\w+)\s+([A-Z][a-z]\w+(?:\s+[A-Z][a-z]\w+)*)",
        text,
    )
    if match:
        return f"{match.group(1).capitalize()} {match.group(2).strip()}"
    # Simpler expired mention without product
    if re.search(r"\bexpir\w+\b", text, re.IGNORECASE):
        return "Expired item reported"

    # Pattern: "not properly sealed" / "broken seal"
    match = re.search(r"(not\s+properly\s+sealed|broken\s+seal)", text, re.IGNORECASE)
    if match:
        return match.group(1).capitalize()

    # Pattern: "defective"
    if re.search(r"\bdefective\b", text, re.IGNORECASE):
        return "Defective item"

    # Pattern: "moldy" without "on"
    match = re.search(r"moldy\s+(.+?)(?:\.|,|\n|$)", text, re.IGNORECASE)
    if match:
        return f"Mold on {match.group(1).strip()}"

    # Pattern: general "mold" mention without product (e.g., "there was mold")
    if re.search(r"\bmold\b", text, re.IGNORECASE):
        return "Mold reported"

    # Pattern: "arrived warm" / "warm on arrival" / "not cold"
    if re.search(r"arrived?\s+warm|warm\s+on\s+arrival|not\s+cold", text, re.IGNORECASE):
        return "Arrived warm — product compromised"

    # Pattern: "smell" / "odor" / "stink"
    if re.search(r"\b(smell|odor|stink|stench)\b", text, re.IGNORECASE):
        return "Off smell/odor reported"

    # Fallback: use the issue type value from Gorgias field, cleaned up
    cf = ticket.get("custom_fields", {})
    issue_type = cf.get(FIELD_ISSUE_TYPE, {}).get("value", "")
    if issue_type:
        # "Order::Spoiled Item::Cheese" → "Spoiled Item — Cheese"
        # "Order::Quality Complaint::Meat" → "Quality Complaint — Meat"
        parts = issue_type.split("::")
        if len(parts) >= 3:
            return f"{parts[1]} — {parts[2]}"
        if len(parts) >= 2:
            return parts[1]
        return issue_type

    # Last resort: first meaningful sentence from body
    if body:
        first_line = body.split("\n")[0].strip()[:120]
        if first_line:
            return first_line

    return subject[:120] if subject else ""


def _extract_product_from_concern(concern: str) -> str:
    """Extract product name from the perceived concern.

    Returns the product name (e.g., 'Farmstead Fenugreek Gouda') or the
    category from a Gorgias field fallback (e.g., 'Cheese').
    """
    # Check cleaned Gorgias field format FIRST:
    # "Quality Complaint — Cheese" / "Spoiled Item — Meat"
    match = re.search(r"(?:Quality Complaint|Spoiled Item)\s*[—-]\s*(.+)", concern)
    if match:
        return match.group(1).strip()

    # "Mold on <product>" — stop at common non-product words
    match = re.search(
        r"[Mm]old\s+on\s+(.+?)(?:\s+when\b|\s+that\b|\s+and\b|\s+but\b|\.|,|\n|$)",
        concern,
    )
    if match:
        product = match.group(1).strip().rstrip(".,;")
        # Skip if captured a non-product phrase
        if product.lower() not in ("it", "the", "this", "them", "one"):
            return product

    # "Spoiled <product>" — but not "Spoiled Item — X" (handled above)
    match = re.search(r"(?:spoiled|rotten)\s+(.+?)(?:\.|,|\n|$)", concern, re.IGNORECASE)
    if match:
        product = match.group(1).strip().rstrip(".,;")
        if not product.lower().startswith("item"):
            return product

    # "Expired <product>" — only if next word is capitalized (product name)
    match = re.search(r"expir\w+\s+([A-Z][a-z]\w+(?:\s+[A-Z][a-z]\w+)*)", concern)
    if match:
        return match.group(1).strip().rstrip(".,;")

    return ""


def sync_food_safety_to_sheet(days_back: int = 14, dry_run: bool = False) -> dict[str, object]:
    """Pull food safety tickets from Gorgias and append to UPDATE_Food Safety tab.

    Filters on Order::Spoiled Item and Order::Quality Complaint issue types.

    Args:
        days_back: How many days back to pull tickets (default 14).
        dry_run: If True, return rows without writing to sheet.

    Returns dict with summary and rows.
    """
    auth, base_url = _gorgias_auth()
    since_dt = datetime.now() - timedelta(days=days_back)

    # Read existing rows to get next ID and avoid duplicates
    from google_integration import GoogleIntegration

    settings = _load_settings()
    creds_path = settings.get("google_credentials_path", "")
    if not creds_path or not os.path.exists(creds_path):
        creds_path = str(Path(__file__).resolve().parent.parent.parent / "shipping-perfomance-review-accd39ac4b78.json")
    gclient = GoogleIntegration(creds_path)

    existing_rows = gclient.read_sheet(SPREADSHEET_ID, f"'{FOOD_SAFETY_TAB}'!A:J")
    # Deduplicate by order number (col B) and Gorgias link (col J)
    existing_orders = set()
    existing_links = set()
    next_id = 1
    for row in existing_rows:
        if not row or not row[0]:
            continue
        try:
            row_id = int(row[0])
            if row_id >= next_id:
                next_id = row_id + 1
        except (ValueError, IndexError):
            pass
        if len(row) > 1 and row[1].strip():
            existing_orders.add(row[1].strip())
        if len(row) > 9 and row[9].strip():
            existing_links.add(row[9].strip())

    # Paginate Gorgias tickets
    cursor = None
    new_rows = []
    checked = 0
    skipped_dup = 0
    skipped_tag = 0
    done = False

    for _ in range(40):  # up to 2000 tickets
        params = {
            "limit": 50,
            "order_by": "created_datetime:desc",
        }
        if cursor:
            params["cursor"] = cursor

        resp = _gorgias_get(f"{base_url}/tickets", auth=auth, params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            break

        for t in items:
            created_str = t.get("created_datetime", "")
            try:
                ticket_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, AttributeError):
                ticket_dt = datetime.now()
            if ticket_dt < since_dt:
                done = True
                break
            checked += 1

            cf = t.get("custom_fields", {})
            issue_type = cf.get(FIELD_ISSUE_TYPE, {}).get("value", "")

            # Must match food safety issue prefixes
            if not issue_type or not _matches_valid_prefix(issue_type, FOOD_SAFETY_ISSUE_PREFIXES):
                skipped_tag += 1
                continue

            # Extract order number
            order_num = _extract_order_number(t, gorgias_auth=auth, gorgias_base=base_url)
            if order_num:
                original = _resolve_original_order(order_num)
                if original and original != order_num:
                    order_num = original

            # Skip duplicates
            gorgias_link = _extract_gorgias_link(t)
            if order_num and order_num in existing_orders:
                skipped_dup += 1
                continue
            if gorgias_link and gorgias_link in existing_links:
                skipped_dup += 1
                continue

            # Fetch messages once — reused for date, concern, and product
            msg_body = _fetch_first_customer_message_body(t, auth=auth, base_url=base_url)
            import time as _time_fs

            _time_fs.sleep(0.3)  # rate limit after message fetch

            # Get complaint date from first customer message
            first_msg_dt = _get_first_customer_message_date(t, auth=auth, base_url=base_url)
            _time_fs.sleep(0.3)  # rate limit
            try:
                dt = datetime.fromisoformat(first_msg_dt.replace("Z", "+00:00"))
                dt = dt.replace(tzinfo=None)
                date_str = dt.strftime("%m/%d/%Y")
            except (ValueError, AttributeError):
                date_str = first_msg_dt[:10] if first_msg_dt else ""

            # Customer name
            customer = t.get("customer", {})
            customer_name = customer.get("name", "") or ""
            if not customer_name:
                fn = customer.get("firstname", "") or ""
                ln = customer.get("lastname", "") or ""
                customer_name = f"{fn} {ln}".strip()

            # Extract concern and product from pre-fetched message body
            subject = t.get("subject", "")
            concern = _extract_concern_from_text(subject, msg_body, t)
            product = _extract_product_from_concern(concern)

            # Shopify enrichment: tracking number, FC
            tracking = ""
            fc = ""
            if order_num:
                shopify_order = _shopify_order_by_name(order_num)
                if shopify_order:
                    tracking = _extract_tracking_from_shopify(shopify_order)
                    fc = _extract_fc_from_shopify_tags(shopify_order)

            # Build row: ID, Order #, Date, Customer Name, Product SKU,
            # Cheese Paper or Vac Seal, FC, Tracking, Concern, Gorgias Link,
            # CEO Comments, Direction, Corrective Action, Date Resolved
            row = [
                str(next_id),
                order_num,
                date_str,
                customer_name,
                product,
                "",  # Cheese Paper or Vac Seal — manual
                fc,
                tracking,
                concern,
                gorgias_link,
                "",  # CEO Comments — manual
                "",  # Direction — manual
                "",  # Corrective Action — manual
                "",  # Date Resolved — manual
            ]
            new_rows.append(row)
            next_id += 1
            if order_num:
                existing_orders.add(order_num)
            if gorgias_link:
                existing_links.add(gorgias_link)

        if done:
            break
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    # Write to sheet
    append_result = None
    if new_rows and not dry_run:
        svc = gclient._sheets
        append_result = (
            svc.spreadsheets()
            .values()
            .append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{FOOD_SAFETY_TAB}'!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="OVERWRITE",
                body={"values": new_rows},
            )
            .execute()
        )

        # Verify rows were actually written
        updated_range = append_result.get("updates", {}).get("updatedRange", "")
        updated_rows = append_result.get("updates", {}).get("updatedRows", 0)
        if updated_rows != len(new_rows):
            raise RuntimeError(
                f"Append verification failed: expected {len(new_rows)} rows, "
                f"API reported {updated_rows}. Range: {updated_range}"
            )

    # Universal-DB tee: mirror to shipping.db.feedback (same as sister fn above).
    tee_written = _tee_to_shipping_db(new_rows) if new_rows else 0

    return {
        "checked": checked,
        "new_rows": len(new_rows),
        "skipped_duplicate": skipped_dup,
        "skipped_invalid_tag": skipped_tag,
        "dry_run": dry_run,
        "rows": new_rows,
        "append_range": append_result.get("updates", {}).get("updatedRange", "") if append_result else None,
        "sqlite_tee_written": tee_written,
    }


def register(mcp: object) -> None:
    """Register Gorgias-to-Sheets sync tools on the MCP server.

    Public MCP surface is intentionally narrow: `update_operational_issues`
    is the single entry point for operators (sync + enrich in one pass).
    `gorgias_sync_operational_issues` and `enrich_operational_issues` are
    kept as Python-callable helpers but no longer registered as MCP tools.
    """

    @mcp.tool()
    def update_operational_issues(
        days_back: int = 14,
        dry_run: bool = False,
    ) -> str:
        """One-command update: sync new Gorgias tickets + enrich all incomplete rows.

        Pulls tickets from the last N days, appends new ones to the sheet
        (with original-damaged-order resolution — reships are swapped to
        the original order number), then enriches all rows with missing
        order numbers, carriers, states, and FC tags from Shopify.

        Args:
            days_back: How many days back to pull (default 14).
                14 covers the long tail — ~90% of complaints land by T+5,
                99% by T+13. Safe to run daily; dedup is by Gorgias link.
            dry_run: If True, preview without writing (default False).

        Returns JSON summary of sync + enrichment results.
        """
        try:
            # Step 1: Sync new tickets
            sync_result = sync_gorgias_to_sheet(days_back=days_back, dry_run=dry_run)
            sync_summary = {
                "checked": sync_result["checked"],
                "new_rows_appended": sync_result["new_rows"],
                "skipped_duplicate": sync_result["skipped_duplicate"],
                "skipped_invalid_tag": sync_result["skipped_invalid_tag"],
                "append_range": sync_result.get("append_range"),
                "sample_rows": sync_result["rows"][:25],
            }

            # Step 2: Enrich incomplete rows
            enrich_result = enrich_incomplete_rows(dry_run=dry_run)
            enrich_summary = {
                "rows_enriched": enrich_result["rows_enriched"],
                "enriched": enrich_result.get("enriched", [])[:20],
            }

            return json.dumps(
                {
                    "dry_run": dry_run,
                    "sync": sync_summary,
                    "enrich": enrich_summary,
                },
                indent=2,
            )
        except Exception as e:
            import traceback

            return json.dumps({"error": str(e), "trace": traceback.format_exc()})

    @mcp.tool()
    def gorgias_sync_food_safety(
        days_back: int = 14,
        dry_run: bool = False,
    ) -> str:
        """Sync food safety tickets from Gorgias to the UPDATE_Food Safety tab.

        Pulls tickets from the last N days where the issue type is
        Order::Spoiled Item or Order::Quality Complaint, deduplicates
        against existing rows, and appends new ones.

        Args:
            days_back: How many days back to pull (default 14).
            dry_run: If True, preview rows without writing (default False).

        Returns JSON summary with counts and new rows.
        """
        try:
            result = sync_food_safety_to_sheet(days_back=days_back, dry_run=dry_run)
            summary = {
                "checked": result["checked"],
                "new_rows_appended": result["new_rows"],
                "skipped_duplicate": result["skipped_duplicate"],
                "skipped_invalid_tag": result["skipped_invalid_tag"],
                "dry_run": result["dry_run"],
                "sample_rows": result["rows"][:10],
            }
            return json.dumps(summary, indent=2)
        except Exception as e:
            import traceback

            return json.dumps({"error": str(e), "trace": traceback.format_exc()})
