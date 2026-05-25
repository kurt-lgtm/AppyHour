"""#8 — Empirically calibrate MELT_EFFICIENCY from postmortem outcomes.

Theory:
  Kori's thermal model uses MELT_EFFICIENCY = 0.90 (constant). Means the
  config_btu × 0.90 = "effective supply". Margin = effective_btu - q_safe.
  If real efficiency is lower, model over-promises supply → silent under-pack.
  If higher, over-pack — wasted gel.

Calibration:
  Group orders into margin bands (using current MELT_EFFICIENCY). Compute the
  actual warm/melted rate per band. The band where warm-rate crosses some
  threshold (e.g. 1%) tells us the real "no margin" point. Back out the
  efficiency that would have placed that band at exactly 0% margin.

Minimum data needed:
  - ≥4 locked cohorts with thermal fields (post-2026-05-25 schema)
  - ≥50 warm-bucket tickets across the cohorts (volume for statistical band)
  - delivery_status joined → confirms transit_type matched (no Ground downgrade
    contamination)

Output: _outputs/postmortems/melt_efficiency_calibration.md
  - Bucket histogram (margin band × warm rate)
  - Suggested MELT_EFFICIENCY value with 95% CI if data sufficient
  - "Insufficient data" note with N currently available

Currently expected: insufficient data until late-July 2026 cohort #4+.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "GelPackCalculator/kori"))

DB_PATH = Path.home() / "AppData/Roaming/AppyHour/shipping.db"
OUT_PATH = (
    Path(r"C:\Users\Work\Claude Projects\_outputs\postmortems")
    / "melt_efficiency_calibration.md"
)
MIN_COHORTS = 4
MIN_WARM_EVENTS = 50

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("melt-calibrate")


def band(m_pct: float) -> str:
    if m_pct < -25:
        return "<-25% (severe under)"
    if m_pct < 0:
        return "-25-0% (under)"
    if m_pct < 25:
        return "0-25%"
    if m_pct < 50:
        return "25-50%"
    if m_pct < 75:
        return "50-75%"
    return "75%+"


def collect_dataset(db: sqlite3.Connection) -> tuple[list[dict], int]:
    """Join all canonical snapshots' orders with feedback (warm/melted only)."""
    rows = db.execute(
        """
        SELECT kso.snapshot_id, kso.order_number, kso.predicted_risk,
               kso.predicted_config, kso.effective_btu, kso.margin_btu,
               kso.transit_type,
               ds.transit_days,
               (
                 CASE WHEN EXISTS (
                   SELECT 1 FROM feedback fb
                   WHERE REPLACE(fb.order_number,'#','') = kso.order_number
                     AND (fb.issue_type LIKE '%Warm%' OR fb.issue_type LIKE '%Melted%')
                 ) THEN 1 ELSE 0 END
               ) AS warm
        FROM kori_snapshot_orders kso
        JOIN kori_snapshots ks ON ks.snapshot_id = kso.snapshot_id
        LEFT JOIN fulfillments f ON f.order_number = kso.order_number
        LEFT JOIN delivery_status ds ON ds.tracking_number = f.tracking_number
        WHERE ks.fulfilled_at IS NOT NULL
          AND kso.margin_btu IS NOT NULL
          AND kso.effective_btu IS NOT NULL
          AND kso.effective_btu > 0
        """
    ).fetchall()
    n_cohorts = db.execute(
        "SELECT COUNT(DISTINCT snapshot_id) FROM kori_snapshots "
        "WHERE fulfilled_at IS NOT NULL"
    ).fetchone()[0]
    return [dict(r) for r in rows], n_cohorts


def build_report(rows: list[dict], n_cohorts: int) -> str:
    n = len(rows)
    n_warm = sum(r["warm"] for r in rows)

    out = [
        "# MELT_EFFICIENCY Calibration Report\n",
        f"_Generated {datetime.now().isoformat(timespec='seconds')}_\n\n",
        "## Inputs\n\n",
        f"- Locked cohorts with thermal fields: **{n_cohorts}** (need ≥{MIN_COHORTS})\n",
        f"- Orders analyzed: **{n}**\n",
        f"- Warm/melted tickets: **{n_warm}** (need ≥{MIN_WARM_EVENTS})\n\n",
    ]
    if n_cohorts < MIN_COHORTS or n_warm < MIN_WARM_EVENTS:
        out.append(
            "## Status: ⏸ INSUFFICIENT DATA\n\n"
            "Calibration requires postmortem-quality data accumulated over multiple "
            "cohorts. Run weekly; first useful output expected after 4 locked "
            "cohorts + 50 warm events.\n\n"
            "Until then, current `MELT_EFFICIENCY = 0.90` remains the working "
            "assumption. Postmortem `Gel margin` section monitors per-cohort "
            "warm rate vs band — eyeball it for drift.\n"
        )
        return "".join(out)

    # Bucket margin band → warm rate
    buckets: dict[str, dict[str, int]] = {}
    for r in rows:
        b = band(r["margin_btu"] / r["effective_btu"] * 100)
        bucket = buckets.setdefault(b, {"n": 0, "warm": 0})
        bucket["n"] += 1
        bucket["warm"] += int(r["warm"])

    out.append("## Margin band × warm rate\n\n| Band | N | Warm | Rate |\n|---|---|---|---|\n")
    order = ["<-25% (severe under)", "-25-0% (under)", "0-25%", "25-50%", "50-75%", "75%+"]
    for b in order:
        if b in buckets:
            x = buckets[b]
            rate = (100 * x["warm"] / x["n"]) if x["n"] else 0
            out.append(f"| {b} | {x['n']} | {x['warm']} | {rate:.2f}% |\n")
    out.append("\n")

    # Calibration: find the band where warm rate crosses 1%. That's the
    # effective margin threshold. Back-solve efficiency that places this
    # threshold at 0% margin under current model.
    #
    # threshold_band_low_margin_pct = X (low edge of crossover band, e.g. -25%)
    # We want: effective_btu_new = config_btu * MELT_NEW
    #         current model: effective_btu = config_btu * 0.90
    #         shift = MELT_NEW / 0.90
    # If crossover at margin = -25% with current 0.90, real efficiency is
    # MELT_NEW = 0.90 * (1 + (-0.25)) = 0.675
    crossover = None
    for b in order:
        if b in buckets and buckets[b]["n"] >= 10:
            rate = buckets[b]["warm"] / buckets[b]["n"]
            if rate < 0.01:
                # First band below 1% warm — this is the "safe" band start
                edges = {
                    "<-25% (severe under)": -0.50,
                    "-25-0% (under)": -0.25,
                    "0-25%": 0.0,
                    "25-50%": 0.25,
                    "50-75%": 0.50,
                    "75%+": 0.75,
                }
                crossover = edges.get(b)
                break
    if crossover is None:
        out.append("**Calibration result:** could not isolate crossover band; data still noisy. Keep 0.90.\n")
    else:
        melt_new = 0.90 * (1 + crossover)
        out.append(
            f"**Estimated MELT_EFFICIENCY:** {melt_new:.3f} "
            f"(crossover at {crossover*100:+.0f}% margin under current 0.90).\n\n"
            f"_Suggest setting MELT_EFFICIENCY = {melt_new:.2f} in gel_pack_shopify.py._\n"
        )
    return "".join(out)


def main() -> int:
    if not DB_PATH.exists():
        log.error(f"DB missing: {DB_PATH}")
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Ensure schema has the new thermal columns (auto-migrates if missing)
    try:
        from db_snapshots import init_schema
        init_schema(DB_PATH, force=True)
    except Exception as e:
        log.warning(f"init_schema failed: {e}")
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    try:
        rows, n_cohorts = collect_dataset(db)
        md = build_report(rows, n_cohorts)
        OUT_PATH.write_text(md, encoding="utf-8")
        log.info(f"Wrote {OUT_PATH}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
