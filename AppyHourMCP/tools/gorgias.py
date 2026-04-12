"""
Gorgias MCP tools — query tickets, customers, and satisfaction data
from the Gorgias helpdesk API.
"""

import json

from tools._gorgias_internal import gorgias_get, gorgias_paginate
from utils import format_error


def register(mcp: object) -> None:
    """Register Gorgias tools on the MCP server."""

    @mcp.tool()
    def gorgias_test_connection() -> str:
        """Test connection to the Gorgias API.

        Returns account info if successful.
        """
        try:
            data = gorgias_get("account")
            return json.dumps({
                "success": True,
                "domain": data.get("domain"),
                "name": data.get("name"),
                "plan": data.get("plan", {}).get("name", "unknown"),
            }, indent=2)
        except Exception as e:
            return format_error(e, "gorgias")

    @mcp.tool()
    def gorgias_list_tickets(
        status: str = "",
        created_after: str = "",
        created_before: str = "",
        limit: int = 50,
    ) -> str:
        """List Gorgias tickets with optional filters.

        Args:
            status: Filter by status: 'open', 'closed', 'unresolved'. Empty = all.
            created_after: ISO date string (e.g. '2026-03-01'). Empty = no filter.
            created_before: ISO date string. Empty = no filter.
            limit: Max tickets to return (default 50, max 500).

        Returns JSON array of ticket summaries.
        """
        try:
            params = {}
            if status:
                params["status"] = status
            if created_after:
                params["created_datetime__gte"] = created_after
            if created_before:
                params["created_datetime__lte"] = created_before

            tickets = gorgias_paginate("tickets", params, min(limit, 500))
            summaries = []
            for t in tickets:
                summaries.append({
                    "id": t.get("id"),
                    "subject": t.get("subject", ""),
                    "status": t.get("status"),
                    "channel": t.get("channel"),
                    "created": t.get("created_datetime"),
                    "updated": t.get("updated_datetime"),
                    "tags": [tag.get("name") for tag in t.get("tags", [])],
                    "assignee": t.get("assignee_user", {}).get("name", "") if t.get("assignee_user") else "",
                    "customer_email": t.get("customer", {}).get("email", "") if t.get("customer") else "",
                    "messages_count": t.get("messages_count", 0),
                })
            return json.dumps({
                "total_returned": len(summaries),
                "tickets": summaries,
            }, indent=2)
        except Exception as e:
            return format_error(e, "gorgias")

    @mcp.tool()
    def gorgias_get_ticket(ticket_id: int) -> str:
        """Get full details for a single Gorgias ticket including messages.

        Args:
            ticket_id: The ticket ID number.

        Returns full ticket data with messages.
        """
        try:
            ticket = gorgias_get(f"tickets/{ticket_id}")
            messages = gorgias_paginate(f"tickets/{ticket_id}/messages", limit=50)

            # Extract custom fields (Issue Type, Resolution, Category)
            raw_cf = ticket.get("custom_fields") or []
            if not isinstance(raw_cf, list):
                raw_cf = []
            custom_fields = {}
            for cf in raw_cf:
                field_id = cf.get("field_id") or cf.get("id")
                value = cf.get("value")
                name = cf.get("name", "")
                if field_id and value:
                    custom_fields[str(field_id)] = {"name": name, "value": value}

            return json.dumps({
                "id": ticket.get("id"),
                "subject": ticket.get("subject"),
                "status": ticket.get("status"),
                "channel": ticket.get("channel"),
                "created": ticket.get("created_datetime"),
                "tags": [
                    tag.get("name") if isinstance(tag, dict) else str(tag)
                    for tag in (ticket.get("tags") or [])
                    if isinstance(tag, (dict, str))
                ],
                "custom_fields": custom_fields,
                "customer": ticket.get("customer", {}),
                "messages": [{
                    "sender": m.get("sender", {}).get("email", ""),
                    "body_text": m.get("body_text", "")[:500],
                    "created": m.get("created_datetime"),
                    "source_type": m.get("source", {}).get("type", ""),
                } for m in messages],
            }, indent=2)
        except Exception as e:
            return format_error(e, "gorgias")

    @mcp.tool()
    def gorgias_ticket_stats(
        created_after: str = "",
        created_before: str = "",
    ) -> str:
        """Get ticket statistics — counts by status, channel, and tag.

        Args:
            created_after: ISO date (e.g. '2026-03-01'). Empty = last 30 days.
            created_before: ISO date. Empty = now.

        Returns JSON with breakdowns by status, channel, and top tags.
        """
        try:
            from collections import Counter
            from datetime import datetime, timedelta

            if not created_after:
                created_after = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

            params = {"created_datetime__gte": created_after}
            if created_before:
                params["created_datetime__lte"] = created_before

            tickets = gorgias_paginate("tickets", params, limit=500)

            status_counts = Counter()
            channel_counts = Counter()
            tag_counts = Counter()

            for t in tickets:
                status_counts[t.get("status", "unknown")] += 1
                channel_counts[t.get("channel", "unknown")] += 1
                for tag in t.get("tags", []):
                    tag_counts[tag.get("name", "unknown")] += 1

            return json.dumps({
                "period": {"from": created_after, "to": created_before or "now"},
                "total_tickets": len(tickets),
                "by_status": dict(status_counts.most_common()),
                "by_channel": dict(channel_counts.most_common()),
                "top_tags": dict(tag_counts.most_common(20)),
            }, indent=2)
        except Exception as e:
            return format_error(e, "gorgias")

    @mcp.tool()
    def gorgias_satisfaction_stats(
        created_after: str = "",
        created_before: str = "",
    ) -> str:
        """Get customer satisfaction survey results.

        Args:
            created_after: ISO date. Empty = last 30 days.
            created_before: ISO date. Empty = now.

        Returns CSAT breakdown.
        """
        try:
            from collections import Counter
            from datetime import datetime, timedelta

            if not created_after:
                created_after = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

            params = {"created_datetime__gte": created_after}
            if created_before:
                params["created_datetime__lte"] = created_before

            surveys = gorgias_paginate("satisfaction-surveys", params, limit=500)

            score_counts = Counter()
            for s in surveys:
                score = s.get("score")
                if score is not None:
                    score_counts[str(score)] += 1

            total = sum(score_counts.values())
            positive = score_counts.get("5", 0) + score_counts.get("4", 0)

            return json.dumps({
                "period": {"from": created_after, "to": created_before or "now"},
                "total_responses": total,
                "csat_pct": round(positive / total * 100, 1) if total else 0,
                "by_score": dict(score_counts.most_common()),
            }, indent=2)
        except Exception as e:
            return format_error(e, "gorgias")
