"""Postmortem auto-runner — generates a markdown postmortem 7 days after each
ship_week, joining canonical snapshot ⨝ feedback ⨝ delivery_status ⨝ weather.

Schedule (Windows Task Scheduler):
  Mondays 09:00 ET (Sat cohort = 7 days, Tue cohort = 6 days — both covered).

Output:
  C:\\Users\\Work\\Claude Projects\\_outputs\\postmortem-{ship_week}.md

Skips ship_weeks already with a postmortem file. Skips ship_weeks without a
canonical snapshot (fulfilled_at IS NOT NULL).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────
DB_PATH = Path.home() / "AppData/Roaming/AppyHour/shipping.db"
OUT_DIR = Path(r"C:\Users\Work\Claude Projects\_outputs\postmortems")
LOOKBACK_DAYS = 21  # check ship_weeks up to 3 weeks back
MIN_AGE_DAYS = 7    # only run postmortem for cohorts ≥7d old (delivery + ticket lag)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path.home() / "AppData/Roaming/AppyHour/postmortem_runner.log"),
    ],
)
log = logging.getLogger("postmortem")


def find_eligible_ship_weeks(db: sqlite3.Connection) -> list[str]:
    """Locked snapshots with ship_week between today-21d and today-7d that
    haven't been postmortemed yet."""
    today = date.today()
    earliest = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    latest = (today - timedelta(days=MIN_AGE_DAYS)).isoformat()
    cur = db.execute(
        """
        SELECT DISTINCT ship_week
        FROM kori_snapshots
        WHERE fulfilled_at IS NOT NULL
          AND ship_week BETWEEN ? AND ?
        ORDER BY ship_week DESC
        """,
        (earliest, latest),
    )
    weeks = [row[0] for row in cur]
    existing = {p.stem.replace("postmortem-", "") for p in OUT_DIR.glob("postmortem-*.md")}
    pending = [w for w in weeks if w not in existing]
    log.info(f"Eligible ship_weeks: {weeks}, already done: {sorted(existing)}, pending: {pending}")
    return pending


def get_canonical_snapshot(db: sqlite3.Connection, ship_week: str) -> dict | None:
    """Pick the canonical snapshot for a ship_week (most recent locked)."""
    cur = db.execute(
        """
        SELECT snapshot_id, ship_week, ship_tag, locked_at, fulfilled_at,
               total_orders, target_temp_f, safety_pct, hub_temp_f,
               filter_and, total_cost
        FROM kori_snapshots
        WHERE ship_week = ? AND fulfilled_at IS NOT NULL
        ORDER BY locked_at DESC LIMIT 1
        """,
        (ship_week,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return dict(row)


def section_summary(db: sqlite3.Connection, snap: dict) -> str:
    """Top-level snapshot facts."""
    return (
        f"## Snapshot\n\n"
        f"- snapshot_id: `{snap['snapshot_id']}`\n"
        f"- ship_week: **{snap['ship_week']}** · ship_tag: `{snap['ship_tag']}`\n"
        f"- locked_at: {snap['locked_at']}\n"
        f"- total_orders: {snap['total_orders']}\n"
        f"- target_temp_f: {snap['target_temp_f']}, safety_pct: {snap['safety_pct']}, "
        f"hub_temp_f: {snap['hub_temp_f']}\n"
        f"- filter_and: `{snap['filter_and']}`\n\n"
    )


def section_failures_by_bucket(db: sqlite3.Connection, ship_tag: str) -> str:
    """Ticket buckets keyed to this cohort by ship_tag."""
    cur = db.execute(
        """
        WITH bucketed AS (
          SELECT
            CASE
              WHEN issue_type LIKE '%Warm%' OR issue_type LIKE '%Melted%' THEN 'Arrived Warm'
              WHEN issue_type LIKE '%Delayed%' THEN 'Delayed'
              WHEN issue_type LIKE '%Lost%' OR issue_type LIKE '%Misdeliver%' THEN 'Lost/Misdelivered'
              WHEN issue_type LIKE '%Cannot be delivered%' THEN 'Undeliverable'
              WHEN issue_type LIKE '%Damage%' THEN 'Damaged'
              ELSE NULL
            END AS bucket
          FROM feedback fb
          LEFT JOIN fulfillments f ON TRIM(REPLACE(f.order_number,'#','')) = TRIM(REPLACE(fb.order_number,'#',''))
          WHERE f.tags LIKE ?
        )
        SELECT bucket, COUNT(*) n FROM bucketed WHERE bucket IS NOT NULL
        GROUP BY bucket ORDER BY n DESC
        """,
        (f"%{ship_tag}%",),
    )
    rows = list(cur)
    out = "## Failures by bucket\n\n| Bucket | Count |\n|---|---|\n"
    if not rows:
        out += "| _(none)_ | 0 |\n"
    else:
        for r in rows:
            out += f"| {r[0]} | {r[1]} |\n"
    return out + "\n"


def section_warm_by_state(db: sqlite3.Connection, ship_tag: str) -> str:
    """Warm tickets grouped by state — state-level lift signal."""
    cur = db.execute(
        """
        SELECT COALESCE(fb.state, f.dest_state, '?') AS state, COUNT(*) n
        FROM feedback fb
        LEFT JOIN fulfillments f ON TRIM(REPLACE(f.order_number,'#','')) = TRIM(REPLACE(fb.order_number,'#',''))
        WHERE (fb.issue_type LIKE '%Warm%' OR fb.issue_type LIKE '%Melted%')
          AND f.tags LIKE ?
        GROUP BY state ORDER BY n DESC LIMIT 12
        """,
        (f"%{ship_tag}%",),
    )
    rows = list(cur)
    out = "## Arrived Warm — by state\n\n| State | Tickets |\n|---|---|\n"
    if not rows:
        out += "| _(none)_ | 0 |\n"
    else:
        for r in rows:
            out += f"| {r[0]} | {r[1]} |\n"
    return out + "\n"


def section_predicted_vs_actual(db: sqlite3.Connection, snapshot_id: str) -> str:
    """Per-order predicted (snapshot) vs actual (weather_history) peak temp."""
    cur = db.execute(
        """
        SELECT
          kso.order_number, kso.state, kso.predicted_risk, kso.predicted_config,
          kso.dest_peak_temp_f AS pred_peak,
          MAX(wh.peak_temp) AS actual_peak,
          ds.transit_days, ds.carrier
        FROM kori_snapshot_orders kso
        LEFT JOIN fulfillments f ON TRIM(REPLACE(f.order_number,'#','')) = TRIM(REPLACE(kso.order_number,'#',''))
        LEFT JOIN delivery_status ds ON ds.tracking_number = f.tracking_number
        LEFT JOIN weather_history wh
          ON wh.zip_prefix = substr(kso.dest_zip, 1, 5)
          AND wh.date BETWEEN COALESCE(ds.pickup_date, date(f.fulfilled_at))
                          AND COALESCE(ds.delivery_date, date(f.fulfilled_at, '+3 days'))
        WHERE kso.snapshot_id = ?
        GROUP BY kso.order_number
        HAVING actual_peak IS NOT NULL
        ORDER BY (actual_peak - pred_peak) DESC LIMIT 20
        """,
        (snapshot_id,),
    )
    rows = list(cur)
    out = "## Top 20 forecast-miss orders (actual_peak − predicted_peak)\n\n"
    out += "| Order | State | Carrier | Transit | Pred peak | Actual peak | Δ | Pred risk | Config |\n"
    out += "|---|---|---|---|---|---|---|---|---|\n"
    if not rows:
        out += "| _(no weather coverage for this cohort)_ | | | | | | | | |\n"
    else:
        for r in rows:
            delta = (r["actual_peak"] - r["pred_peak"]) if r["pred_peak"] else None
            delta_s = f"+{delta:.1f}" if delta and delta > 0 else (f"{delta:.1f}" if delta else "—")
            out += (
                f"| {r['order_number']} | {r['state']} | {r['carrier'] or '?'} | "
                f"{r['transit_days'] or '?'} | {r['pred_peak'] or '—'} | "
                f"{r['actual_peak']:.1f} | {delta_s} | {r['predicted_risk']} | "
                f"{r['predicted_config']} |\n"
            )
    return out + "\n"


def section_gel_margin(db: sqlite3.Connection, snapshot_id: str) -> str:
    """Per-cohort BTU margin distribution + cost rollup.

    Reads thermal fields (total_q_safe / effective_btu / margin_btu / transit_type)
    captured at Lock & Ship time. Snapshots before 2026-05-25 won't have these
    fields — section reports "data not captured" for those cohorts.
    """
    rows = db.execute(
        """
        SELECT predicted_config, predicted_risk, predicted_cost, state,
               total_q_safe, effective_btu, margin_btu, transit_type
        FROM kori_snapshot_orders
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    has_thermal = any(r["margin_btu"] is not None for r in rows)
    if not has_thermal:
        return (
            "## Gel margin (BTU supply vs demand)\n\n"
            "_Thermal fields not captured for this cohort "
            "(snapshot pre-dates 2026-05-25 schema)._\n\n"
        )

    # Bucket by margin band + tier + risk
    def band(m_pct: float) -> str:
        if m_pct < 0:
            return "neg (under-packed)"
        if m_pct < 25:
            return "0-25%"
        if m_pct < 50:
            return "25-50%"
        if m_pct < 75:
            return "50-75%"
        return "75%+ (over-packed)"

    by_band: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    by_state_band: dict[tuple[str, str], int] = {}
    over_packed: list[dict] = []
    under_packed: list[dict] = []
    total_cost = 0.0
    n_analyzed = 0
    for r in rows:
        if r["margin_btu"] is None or r["effective_btu"] is None or r["effective_btu"] <= 0:
            continue
        pct = r["margin_btu"] / r["effective_btu"] * 100
        b = band(pct)
        by_band[b] = by_band.get(b, 0) + 1
        by_tier[r["predicted_config"] or "?"] = by_tier.get(r["predicted_config"] or "?", 0) + 1
        key = (r["state"] or "?", b)
        by_state_band[key] = by_state_band.get(key, 0) + 1
        total_cost += float(r["predicted_cost"] or 0)
        n_analyzed += 1
        if pct >= 75 and (r["predicted_risk"] or "") == "LOW":
            over_packed.append(
                {
                    "state": r["state"],
                    "config": r["predicted_config"],
                    "margin_pct": round(pct, 1),
                    "demand": round(r["total_q_safe"] or 0, 1),
                    "supply": round(r["effective_btu"] or 0, 1),
                    "cost": r["predicted_cost"],
                    "transit": r["transit_type"],
                }
            )
        elif pct < 0:
            under_packed.append(
                {
                    "state": r["state"],
                    "config": r["predicted_config"],
                    "margin_pct": round(pct, 1),
                    "demand": round(r["total_q_safe"] or 0, 1),
                    "supply": round(r["effective_btu"] or 0, 1),
                    "risk": r["predicted_risk"],
                    "transit": r["transit_type"],
                }
            )

    out = [
        f"## Gel margin (BTU supply vs demand)\n\n",
        f"_Analyzed {n_analyzed} of {len(rows)} orders. Total gel cost: **${total_cost:.2f}**._\n\n",
        "### By margin band\n\n| Band | Count |\n|---|---|\n",
    ]
    for k in ("neg (under-packed)", "0-25%", "25-50%", "50-75%", "75%+ (over-packed)"):
        out.append(f"| {k} | {by_band.get(k, 0)} |\n")
    out.append("\n### By assigned tier\n\n| Tier | Count |\n|---|---|\n")
    for tier, n in sorted(by_tier.items(), key=lambda x: -x[1]):
        out.append(f"| {tier} | {n} |\n")
    out.append("\n### Top over-packed candidates (LOW risk + margin ≥ 75%)\n\n")
    if not over_packed:
        out.append("_(none — no LOW-risk orders with 75%+ margin)_\n\n")
    else:
        out.append("| State | Config | Transit | Margin % | Demand | Supply | Cost |\n|---|---|---|---|---|---|---|\n")
        for o in sorted(over_packed, key=lambda x: -x["margin_pct"])[:15]:
            out.append(
                f"| {o['state']} | {o['config']} | {o['transit']} | {o['margin_pct']} | "
                f"{o['demand']} | {o['supply']} | ${o['cost']} |\n"
            )
        out.append("\n")
    out.append("### Under-packed (margin < 0)\n\n")
    if not under_packed:
        out.append("_(none — all orders predicted to have supply ≥ demand)_\n\n")
    else:
        out.append("| State | Config | Transit | Margin % | Demand | Supply | Risk |\n|---|---|---|---|---|---|---|\n")
        for o in sorted(under_packed, key=lambda x: x["margin_pct"])[:15]:
            out.append(
                f"| {o['state']} | {o['config']} | {o['transit']} | {o['margin_pct']} | "
                f"{o['demand']} | {o['supply']} | {o['risk']} |\n"
            )
        out.append("\n")
    return "".join(out)


def section_transit_anomalies(db: sqlite3.Connection, ship_tag: str) -> str:
    """Shipments that exceeded TNT expectation — silent service downgrades."""
    cur = db.execute(
        """
        SELECT ds.carrier, COUNT(*) n,
               AVG(ds.transit_days) AS avg_transit,
               SUM(CASE WHEN ds.transit_days > 2 THEN 1 ELSE 0 END) AS over_2day,
               SUM(CASE WHEN ds.transit_days > 3 THEN 1 ELSE 0 END) AS over_3day
        FROM delivery_status ds
        JOIN fulfillments f ON f.tracking_number = ds.tracking_number
        WHERE f.tags LIKE ? AND ds.transit_days IS NOT NULL
        GROUP BY ds.carrier ORDER BY n DESC
        """,
        (f"%{ship_tag}%",),
    )
    rows = list(cur)
    out = "## Transit anomalies by carrier\n\n| Carrier | Shipments | Avg transit | >2d | >3d |\n|---|---|---|---|---|\n"
    if not rows:
        out += "| _(no delivery_status rows)_ | 0 | — | 0 | 0 |\n"
    else:
        for r in rows:
            out += (
                f"| {r['carrier'] or '?'} | {r['n']} | {r['avg_transit']:.1f} | "
                f"{r['over_2day']} | {r['over_3day']} |\n"
            )
    return out + "\n"


def build_postmortem(db: sqlite3.Connection, ship_week: str) -> str | None:
    snap = get_canonical_snapshot(db, ship_week)
    if not snap:
        return None
    parts = [
        f"# Postmortem — ship_week {ship_week}\n",
        f"_Generated {datetime.now().isoformat(timespec='seconds')} by postmortem_runner.py_\n\n",
        section_summary(db, snap),
        section_failures_by_bucket(db, snap["ship_tag"]),
        section_warm_by_state(db, snap["ship_tag"]),
        section_predicted_vs_actual(db, snap["snapshot_id"]),
        section_gel_margin(db, snap["snapshot_id"]),
        section_transit_anomalies(db, snap["ship_tag"]),
        "---\n_Auto-generated. Edit notes manually below._\n\n## Notes\n\n",
    ]
    return "".join(parts)


def main() -> int:
    if not DB_PATH.exists():
        log.error(f"shipping.db missing: {DB_PATH}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    try:
        pending = find_eligible_ship_weeks(db)
        if not pending:
            log.info("No pending postmortems.")
            return 0
        for ship_week in pending:
            log.info(f"Building postmortem for {ship_week}")
            md = build_postmortem(db, ship_week)
            if not md:
                log.warning(f"Skip {ship_week} — no canonical snapshot")
                continue
            out_path = OUT_DIR / f"postmortem-{ship_week}.md"
            out_path.write_text(md, encoding="utf-8")
            log.info(f"Wrote {out_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
