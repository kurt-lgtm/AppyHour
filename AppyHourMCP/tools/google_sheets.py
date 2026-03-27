"""
Google Sheets MCP tools — read, write, and create Google Sheets
via the existing GoogleIntegration service account.
"""

import json
import os
import sys
from pathlib import Path

# Add GelPackCalculator to path so we can import GoogleIntegration
_GPC_DIR = Path(__file__).resolve().parent.parent.parent / "GelPackCalculator"
sys.path.insert(0, str(_GPC_DIR))

# Credentials path from the Kori app's runtime settings
_APPDATA_SETTINGS = Path(os.environ.get("APPDATA", "")) / "AppyHour" / "gel_calc_shopify_settings.json"
_FALLBACK_CREDS = Path(__file__).resolve().parent.parent.parent / "shipping-perfomance-review-accd39ac4b78.json"


def _get_credentials_path() -> str:
    """Resolve Google service account credentials path."""
    if _APPDATA_SETTINGS.exists():
        with open(_APPDATA_SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
        path = settings.get("google_credentials_path", "")
        if path and os.path.exists(path):
            return path
    if _FALLBACK_CREDS.exists():
        return str(_FALLBACK_CREDS)
    raise FileNotFoundError(
        "No Google service account credentials found. "
        "Configure google_credentials_path in Kori settings or place credentials in AppyHour/."
    )


def _get_client():
    """Lazy-initialize and return a GoogleIntegration instance."""
    from google_integration import GoogleIntegration
    return GoogleIntegration(_get_credentials_path())


def register(mcp):
    """Register Google Sheets tools on the MCP server."""

    @mcp.tool()
    def sheets_read(
        spreadsheet_id: str,
        range: str = "Sheet1!A1:Z1000",
    ) -> str:
        """Read data from a Google Sheet.

        Args:
            spreadsheet_id: The spreadsheet ID (from the URL between /d/ and /edit).
            range: Sheet range in A1 notation (e.g. 'Sheet1!A1:D50'). Default: Sheet1!A1:Z1000.

        Returns JSON with headers and rows.
        """
        try:
            client = _get_client()
            rows = client.read_sheet(spreadsheet_id, range)
            if not rows:
                return json.dumps({"headers": [], "rows": [], "total_rows": 0})
            return json.dumps({
                "headers": rows[0],
                "rows": rows[1:],
                "total_rows": len(rows) - 1,
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def sheets_write(
        spreadsheet_id: str,
        sheet_name: str,
        headers: list[str],
        rows: list[list],
    ) -> str:
        """Write data to a Google Sheet (overwrites from A1).

        Args:
            spreadsheet_id: The spreadsheet ID.
            sheet_name: Tab name to write to (e.g. 'Sheet1').
            headers: List of column header strings.
            rows: List of rows, each row is a list of values.

        Returns confirmation message.
        """
        try:
            client = _get_client()
            client.write_sheet(spreadsheet_id, sheet_name, headers, rows)
            return json.dumps({
                "success": True,
                "message": f"Wrote {len(rows)} rows to {sheet_name}",
                "spreadsheet_id": spreadsheet_id,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def sheets_append(
        spreadsheet_id: str,
        sheet_name: str,
        rows: list[list],
    ) -> str:
        """Append rows to the end of an existing Google Sheet tab.

        Args:
            spreadsheet_id: The spreadsheet ID.
            sheet_name: Tab name to append to.
            rows: List of rows to append.

        Returns confirmation message.
        """
        try:
            client = _get_client()
            svc = client._sheets
            range_str = f"{sheet_name}!A1"
            svc.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ).execute()
            return json.dumps({
                "success": True,
                "message": f"Appended {len(rows)} rows to {sheet_name}",
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def sheets_create(
        title: str,
        folder_id: str = "",
    ) -> str:
        """Create a new Google Sheet.

        Args:
            title: Name for the new spreadsheet.
            folder_id: Optional Drive folder ID to create in.

        Returns the spreadsheet URL.
        """
        try:
            client = _get_client()
            url = client.create_spreadsheet(title, folder_id or None)
            return json.dumps({"success": True, "url": url})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def sheets_add_tab(
        spreadsheet_id: str,
        tab_name: str,
    ) -> str:
        """Add a new tab to an existing Google Sheet.

        Args:
            spreadsheet_id: The spreadsheet ID.
            tab_name: Name for the new tab.

        Returns confirmation message.
        """
        try:
            client = _get_client()
            client.add_sheet_tab(spreadsheet_id, tab_name)
            return json.dumps({
                "success": True,
                "message": f"Added tab '{tab_name}'",
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def sheets_list_tabs(
        spreadsheet_id: str,
    ) -> str:
        """List all tabs in a Google Sheet.

        Args:
            spreadsheet_id: The spreadsheet ID.

        Returns JSON array of tab names.
        """
        try:
            client = _get_client()
            result = client._sheets.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties.title",
            ).execute()
            tabs = [s["properties"]["title"] for s in result.get("sheets", [])]
            return json.dumps({"tabs": tabs})
        except Exception as e:
            return json.dumps({"error": str(e)})
