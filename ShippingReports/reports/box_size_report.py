"""Executive PDF report: shipping performance by box size.

Reads from Kori's shipping.db (via shipping_invoice_db helpers) and generates
a 4-page PDF for stakeholder review.

Usage:
    python -m reports.box_size_report --output /path/to/report.pdf
    python -m reports.box_size_report --since 2026-01-01
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
GELPACK = REPO_ROOT / "GelPackCalculator"
sys.path.insert(0, str(GELPACK))

import shipping_invoice_db as sidb  # noqa: E402
from fpdf import FPDF  # noqa: E402


BOX_TYPE_ORDER = [
    "REGULAR_MEDIUM", "REGULAR_LARGE", "TRAY", "TRAY_LARGE",
    "SPECIALTY", "UNKNOWN", "UNCLASSIFIED",
]

BOX_TYPE_LABELS = {
    "REGULAR_MEDIUM": "Regular Medium",
    "REGULAR_LARGE":  "Regular Large",
    "TRAY":           "Tray",
    "TRAY_LARGE":     "Tray Large",
    "SPECIALTY":      "Specialty",
    "UNKNOWN":        "Unknown",
    "UNCLASSIFIED":   "Unclassified",
}


def _fmt_money(v):
    if v is None:
        return "-"
    return f"${v:.2f}"


def _fmt_pct(v):
    if v is None:
        return "-"
    return f"{v:.1f}%"


def _fmt_days(v):
    if v is None:
        return "-"
    return f"{v:.2f}"


class BoxSizeReport(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, "AppyHour - Shipping Analytics by Box Size", align="L")
        self.cell(0, 8, datetime.now().strftime("%Y-%m-%d %H:%M"), align="R")
        self.ln(12)
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def _fetch_weekly_trend(conn: sqlite3.Connection, since: str) -> list[dict]:
    """12 most recent ship-weeks, count + avg cost per box_type."""
    rows = conn.execute("""
        SELECT
            strftime('%Y-%W', ship_date) AS week,
            COALESCE(box_type, 'UNCLASSIFIED') AS box_type,
            COUNT(*) AS n,
            AVG(NULLIF(cost, 0)) AS avg_cost
        FROM shipments
        WHERE ship_date >= ?
        GROUP BY week, box_type
        ORDER BY week DESC
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def _fetch_zone_failures(conn: sqlite3.Connection, since: str, limit: int = 15) -> list[dict]:
    """Top problem zip prefixes (3-digit) by late-delivery rate, per box_type."""
    rows = conn.execute("""
        WITH zipz AS (
            SELECT
                substr(zip_code, 1, 3) AS zip3,
                COALESCE(box_type, 'UNCLASSIFIED') AS box_type,
                COUNT(*) AS n,
                SUM(CASE WHEN transit_days > 2 THEN 1 ELSE 0 END) AS late_n
            FROM shipments
            WHERE ship_date >= ?
              AND zip_code IS NOT NULL AND zip_code != ''
              AND transit_days IS NOT NULL
            GROUP BY zip3, box_type
            HAVING n >= 10
        )
        SELECT zip3, box_type, n, late_n,
               100.0 * late_n / n AS late_pct
        FROM zipz
        ORDER BY late_pct DESC, n DESC
        LIMIT ?
    """, (since, limit)).fetchall()
    return [dict(r) for r in rows]


def _page_summary(pdf: BoxSizeReport, stats: list[dict], since: str) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Shipping Performance by Box Size", ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Reporting period: {since} to {date.today().isoformat()}", ln=1)
    pdf.ln(4)
    pdf.set_text_color(0, 0, 0)

    # Aggregate across carriers per box_type
    agg: dict[str, dict] = {}
    for r in stats:
        bt = r["box_type"]
        if bt not in agg:
            agg[bt] = {"count": 0, "cost_sum": 0.0, "cost_n": 0,
                      "transit_sum": 0.0, "transit_n": 0, "on_time_sum": 0.0, "on_time_n": 0}
        agg[bt]["count"] += r["count"]
        if r.get("avg_cost") is not None:
            agg[bt]["cost_sum"] += r["avg_cost"] * r["count"]
            agg[bt]["cost_n"] += r["count"]
        if r.get("avg_transit") is not None:
            agg[bt]["transit_sum"] += r["avg_transit"] * r["count"]
            agg[bt]["transit_n"] += r["count"]
        if r.get("on_time_pct") is not None:
            agg[bt]["on_time_sum"] += r["on_time_pct"] * r["count"]
            agg[bt]["on_time_n"] += r["count"]

    total_ships = sum(v["count"] for v in agg.values())
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Total shipments: {total_ships:,}", ln=1)
    pdf.ln(4)

    # Table
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(40, 40, 40)
    pdf.set_text_color(255, 255, 255)
    col_widths = [45, 25, 30, 30, 30]
    headers = ["Box Type", "Count", "Avg Cost", "Avg Transit", "On-Time %"]
    for h, w in zip(headers, col_widths):
        pdf.cell(w, 8, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    for bt in BOX_TYPE_ORDER:
        if bt not in agg:
            continue
        v = agg[bt]
        avg_cost = v["cost_sum"] / v["cost_n"] if v["cost_n"] else None
        avg_tr = v["transit_sum"] / v["transit_n"] if v["transit_n"] else None
        on_time = v["on_time_sum"] / v["on_time_n"] if v["on_time_n"] else None
        row = [
            BOX_TYPE_LABELS[bt],
            f"{v['count']:,}",
            _fmt_money(avg_cost),
            _fmt_days(avg_tr),
            _fmt_pct(on_time),
        ]
        for cell, w in zip(row, col_widths):
            pdf.cell(w, 7, cell, border=1, align="R" if cell != row[0] else "L")
        pdf.ln()


def _page_cost_breakdown(pdf: BoxSizeReport, stats: list[dict]) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Cost Breakdown by Box Type × Carrier", ln=1)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(40, 40, 40)
    pdf.set_text_color(255, 255, 255)
    col_widths = [45, 30, 25, 30, 30]
    headers = ["Box Type", "Carrier", "Count", "Avg Cost", "Total Spend"]
    for h, w in zip(headers, col_widths):
        pdf.cell(w, 8, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 9)
    sorted_stats = sorted(stats, key=lambda r: (
        BOX_TYPE_ORDER.index(r["box_type"]) if r["box_type"] in BOX_TYPE_ORDER else 99,
        r["carrier"] or "",
    ))
    for r in sorted_stats:
        total_spend = (r.get("avg_cost") or 0) * r["count"]
        row = [
            BOX_TYPE_LABELS.get(r["box_type"], r["box_type"]),
            r["carrier"] or "-",
            f"{r['count']:,}",
            _fmt_money(r.get("avg_cost")),
            _fmt_money(total_spend),
        ]
        for cell, w in zip(row, col_widths):
            pdf.cell(w, 6, cell, border=1, align="R" if cell not in (row[0], row[1]) else "L")
        pdf.ln()


def _page_transit(pdf: BoxSizeReport, stats: list[dict]) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Transit & On-Time Performance by Box Type × Carrier", ln=1)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(40, 40, 40)
    pdf.set_text_color(255, 255, 255)
    col_widths = [45, 30, 25, 35, 35]
    headers = ["Box Type", "Carrier", "Count", "Avg Transit (d)", "On-Time %"]
    for h, w in zip(headers, col_widths):
        pdf.cell(w, 8, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 9)
    sorted_stats = sorted(stats, key=lambda r: (
        BOX_TYPE_ORDER.index(r["box_type"]) if r["box_type"] in BOX_TYPE_ORDER else 99,
        r["carrier"] or "",
    ))
    for r in sorted_stats:
        row = [
            BOX_TYPE_LABELS.get(r["box_type"], r["box_type"]),
            r["carrier"] or "-",
            f"{r['count']:,}",
            _fmt_days(r.get("avg_transit")),
            _fmt_pct(r.get("on_time_pct")),
        ]
        for cell, w in zip(row, col_widths):
            pdf.cell(w, 6, cell, border=1, align="R" if cell not in (row[0], row[1]) else "L")
        pdf.ln()


def _page_weekly(pdf: BoxSizeReport, rows: list[dict]) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Weekly Trend (last 12 weeks)", ln=1)
    pdf.ln(2)

    if not rows:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, "No data in reporting window.", ln=1)
        return

    # Pivot: row = week, col = box_type -> count
    weeks = sorted({r["week"] for r in rows}, reverse=True)[:12]
    box_types = [bt for bt in BOX_TYPE_ORDER if any(r["box_type"] == bt for r in rows)]
    pivot: dict = {(r["week"], r["box_type"]): r["n"] for r in rows}

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(40, 40, 40)
    pdf.set_text_color(255, 255, 255)
    week_col = 22
    bt_col = (190 - week_col) / max(len(box_types), 1)
    pdf.cell(week_col, 7, "Week", border=1, fill=True, align="C")
    for bt in box_types:
        pdf.cell(bt_col, 7, BOX_TYPE_LABELS[bt][:14], border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 9)
    for wk in weeks:
        pdf.cell(week_col, 6, wk, border=1, align="C")
        for bt in box_types:
            v = pivot.get((wk, bt), 0)
            pdf.cell(bt_col, 6, f"{v:,}" if v else "-", border=1, align="R")
        pdf.ln()


def _page_problem_zones(pdf: BoxSizeReport, rows: list[dict]) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Top Problem Zones (3-digit ZIP)", ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, "Late-delivery rate, minimum 10 shipments in bucket", ln=1)
    pdf.ln(2)
    pdf.set_text_color(0, 0, 0)

    if not rows:
        pdf.cell(0, 8, "No problem zones found (or insufficient data).", ln=1)
        return

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(40, 40, 40)
    pdf.set_text_color(255, 255, 255)
    col_widths = [25, 45, 25, 30, 35]
    headers = ["ZIP3", "Box Type", "Count", "Late", "Late %"]
    for h, w in zip(headers, col_widths):
        pdf.cell(w, 8, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    for r in rows:
        row = [
            r["zip3"] or "-",
            BOX_TYPE_LABELS.get(r["box_type"], r["box_type"]),
            f"{r['n']:,}",
            f"{r['late_n']:,}",
            _fmt_pct(r.get("late_pct")),
        ]
        for cell, w in zip(row, col_widths):
            pdf.cell(w, 6, cell, border=1, align="R" if cell not in (row[0], row[1]) else "L")
        pdf.ln()


def generate_report(
    db_path: str | None = None,
    output_path: str | None = None,
    since: str | None = None,
) -> str:
    """Generate PDF report. Returns output path."""
    if since is None:
        since = (date.today() - timedelta(days=90)).isoformat()

    if db_path is None:
        # Default to Kori's live DB
        appdata = os.environ.get("APPDATA")
        if appdata:
            db_path = os.path.join(appdata, "AppyHour", "shipping.db")
        if not db_path or not os.path.exists(db_path):
            raise FileNotFoundError(f"shipping.db not found at {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    stats = sidb.stats_by_box_type(conn, since=since)
    weekly = _fetch_weekly_trend(conn, since)
    zones = _fetch_zone_failures(conn, since)
    conn.close()

    if output_path is None:
        home = os.path.expanduser("~")
        reports_dir = os.path.join(home, ".knowledge", "reports")
        os.makedirs(reports_dir, exist_ok=True)
        output_path = os.path.join(reports_dir, f"box-size-{date.today().isoformat()}.pdf")

    pdf = BoxSizeReport()
    pdf.set_auto_page_break(auto=True, margin=15)
    _page_summary(pdf, stats, since)
    _page_cost_breakdown(pdf, stats)
    _page_transit(pdf, stats)
    _page_weekly(pdf, weekly)
    _page_problem_zones(pdf, zones)
    pdf.output(output_path)
    return output_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to shipping.db (default: Kori app data)")
    ap.add_argument("--output", default=None, help="Output PDF path")
    ap.add_argument("--since", default=None, help="ISO start date (default: 90 days ago)")
    args = ap.parse_args()

    path = generate_report(db_path=args.db, output_path=args.output, since=args.since)
    print(f"Wrote: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
