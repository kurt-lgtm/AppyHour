"""
Shopify bulk operations MCP tool.

Ported from printing-press-library bulk_operations.go (absorb 2026-05-30).
Retires the Matrixify >1000-order CSV detour for READS by wrapping Shopify's
async bulkOperationRunQuery. The query runs server-side with no pagination;
results download as JSONL.

READ-ONLY: only bulkOperationRunQuery (an async read job) + currentBulkOperation
are used. Bulk WRITES (tag updates etc.) still go through Matrixify — this tool
does not wire bulk mutations.
"""
import json
import logging
import time

import requests
from pydantic import BaseModel, ConfigDict, Field

from utils import get_shopify_auth, shopify_graphql, format_error, to_json

logger = logging.getLogger("appyhour_mcp.shopify_bulk")


class BulkOperationError(RuntimeError):
    """A Shopify bulk operation failed, was canceled, or returned userErrors."""


class BulkOperationTimeoutError(RuntimeError):
    """A bulk operation did not complete within the allotted wall-clock budget."""


_SUBMIT_MUTATION = """
mutation($query: String!) {
  bulkOperationRunQuery(query: $query) {
    bulkOperation { id status type createdAt }
    userErrors { field message }
  }
}
"""

_POLL_QUERY = """
query {
  currentBulkOperation {
    id status type createdAt completedAt
    objectCount rootObjectCount fileSize url partialDataUrl errorCode
  }
}
"""


def _submit_bulk_query(base: str, headers: dict, inner_query: str) -> str:
    """Submit a bulk query, returning the bulk operation id.

    Raises BulkOperationError on userErrors (notably OPERATION_IN_PROGRESS when
    another bulk op is already running — Shopify allows only one per shop).
    """
    data = shopify_graphql(base, headers, _SUBMIT_MUTATION, {"query": inner_query})
    payload = data["bulkOperationRunQuery"]
    errors = payload.get("userErrors") or []
    if errors:
        raise BulkOperationError(
            f"bulkOperationRunQuery userErrors: {errors}. "
            "Shopify allows only one bulk op per shop at a time — "
            "wait for the in-progress op to finish."
        )
    op = payload.get("bulkOperation")
    if not op or not op.get("id"):
        raise BulkOperationError(f"no bulkOperation returned: {payload}")
    return op["id"]


def _poll_bulk_operation(
    base: str,
    headers: dict,
    *,
    poll_interval_start: float = 2.0,
    poll_cap: float = 60.0,
    timeout_sec: float = 1200.0,
) -> dict:
    """Poll currentBulkOperation until COMPLETED. Returns the operation dict.

    Raises BulkOperationError on FAILED/CANCELED, BulkOperationTimeoutError if
    the wall-clock budget is exceeded.
    """
    deadline = time.monotonic() + timeout_sec
    sleep = poll_interval_start
    while True:
        # Poll must never be served from cache — pass a bypass resource so the
        # status is always fresh.
        data = shopify_graphql(base, headers, _POLL_QUERY, resource="bulk-poll-live")
        op = data.get("currentBulkOperation") or {}
        status = op.get("status")
        if status == "COMPLETED":
            return op
        if status in ("FAILED", "CANCELED"):
            raise BulkOperationError(
                f"bulk op {status}: errorCode={op.get('errorCode')}"
            )
        if time.monotonic() > deadline:
            raise BulkOperationTimeoutError(
                f"bulk op did not complete within {timeout_sec:.0f}s "
                f"(last status={status})"
            )
        time.sleep(sleep)
        sleep = min(sleep * 1.5, poll_cap)


def _download_jsonl(url: str) -> list[dict]:
    """Download + parse a bulk-op JSONL result. Returns flat list of dicts.

    The url is a signed, short-lived URL — download immediately, never cache it.
    """
    rows: list[dict] = []
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _reassemble(rows: list[dict], child_key: str) -> list[dict]:
    """Nest __parentId child rows under their parent at child_key.

    Shopify bulk JSONL emits nested connection nodes as separate lines carrying
    a __parentId. This reattaches each child to its parent's child_key list.
    Raises ValueError if children exist but child_key is empty.
    """
    roots: list[dict] = []
    index: dict[str, dict] = {}
    children: list[dict] = []
    for row in rows:
        if row.get("__parentId"):
            children.append(row)
        else:
            roots.append(row)
            if row.get("id"):
                index[row["id"]] = row
    if children and not child_key:
        raise ValueError(
            "JSONL contains __parentId child rows but no child_key was given "
            "to nest them under"
        )
    for child in children:
        parent = index.get(child["__parentId"])
        if parent is None:
            continue  # orphan child — parent not in this page; skip defensively
        parent.setdefault(child_key, []).append(child)
    return roots


def register(mcp: object) -> None:
    """Register the bulk query tool on the MCP server."""

    class BulkQueryInput(BaseModel):
        """Input for a Shopify bulk read query."""
        model_config = ConfigDict(str_strip_whitespace=True)

        inner_query: str = Field(
            ...,
            description=(
                "The GraphQL query body to run as a bulk operation, e.g. "
                "'{ orders(query: \"tag:_SHIP_2026-05-26\") { edges { node "
                "{ id name tags } } } }'. Runs server-side, no pagination."
            ),
        )
        child_key: str = Field(
            "",
            description=(
                "If the query nests connections (e.g. lineItems), set this to "
                "the key to nest child rows under (e.g. 'lineItems'). Leave "
                "empty for flat queries."
            ),
        )
        timeout_minutes: int = Field(
            20, ge=1, le=60, description="Max wall-clock wait for completion."
        )
        max_rows: int = Field(
            5000, ge=1, description="Cap rows returned in-memory; response notes truncation."
        )

    @mcp.tool(
        name="appyhour_bulk_query",
        annotations={
            "title": "Shopify Bulk Query (>1000 records)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def bulk_query(params: BulkQueryInput) -> str:
        """Run a Shopify bulk read query — the >1000-record escape hatch.

        Replaces the Matrixify CSV detour for READS. Submits an async
        bulkOperationRunQuery, polls to completion, downloads the JSONL
        result, and (if child_key given) reassembles __parentId children.

        READ-ONLY. For bulk WRITES (tag updates), use Matrixify. Note: 5-10%
        of orders may be locked — apply active_line_items filtering downstream
        when reading line items.
        """
        try:
            base, headers = get_shopify_auth()
            _submit_bulk_query(base, headers, params.inner_query)
            op = _poll_bulk_operation(
                base, headers, timeout_sec=params.timeout_minutes * 60
            )
            url = op.get("url")
            if not url:
                # COMPLETED with no url == zero results
                return to_json({"count": 0, "rows": [], "object_count": op.get("objectCount", 0)})
            rows = _download_jsonl(url)
            result = _reassemble(rows, params.child_key) if params.child_key else rows
            truncated = len(result) > params.max_rows
            envelope = {
                "count": len(result),
                "truncated": truncated,
                "total": len(result),
                "rows": result[: params.max_rows],
            }
            return to_json(envelope)
        except (BulkOperationError, BulkOperationTimeoutError, ValueError) as e:
            return format_error(e, "appyhour_bulk_query")
        except Exception as e:  # noqa: BLE001 — surface any transport/parse error to caller
            return format_error(e, "appyhour_bulk_query")
