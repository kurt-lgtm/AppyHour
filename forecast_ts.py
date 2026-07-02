#!/usr/bin/env python3
"""Canonical TimesFM forecasting wrapper for AppyHour.

Hardened entry point for zero-shot univariate time-series forecasting with
Google's TimesFM 2.5. Wraps the vendored `timesfm` package with the guards this
machine needs:

  * Pinned Hugging Face revision (supply-chain integrity — weights are
    safetensors, so there is no pickle code-exec risk, but pinning prevents a
    silently swapped checkpoint).
  * KMP_DUPLICATE_LIB_OK to dodge the Anaconda + torch OMP double-init crash
    (see ~/.claude skill `torch-omp-double-init-windows`).
  * UTF-8 stdout so emoji/box-drawing prints don't crash under cp1252
    (see skill `windows-python-cp1252-stdout-crash`).

Use this, NOT a hand-rolled timesfm call. For domain-aware AppyHour demand the
canonical owners are `/cut-order` + appyhour_forecast_demand; TimesFM is for
(a) new/un-modeled series, (b) quantile safety-stock bands, (c) trading vol
ranges. See AppyHour/CLAUDE.md routing rows.

Usage:
    python forecast_ts.py input.csv --horizon 12
    python forecast_ts.py input.csv --horizon 8 --value-cols boxes --output out.csv
    python forecast_ts.py input.csv --horizon 30 --output out.json --format json

Install (one-time): pip install "timesfm[torch]"
"""

from __future__ import annotations

# --- Windows env guards (must run before torch / numpy import) ---------------
import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")  # OMP double-init guard

import sys

try:  # force UTF-8 stdout so prints never die on cp1252
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

# --- Hardening: pin the checkpoint revision ----------------------------------
# Current google/timesfm-2.5-200m-pytorch main as of 2026-06-26 absorb.
# Bump deliberately after reviewing upstream changes; do not float to main.
HF_REPO_ID = "google/timesfm-2.5-200m-pytorch"
HF_REVISION = "1d952420fba87f3c6dee4f240de0f1a0fbc790e3"


def load_model(batch_size: int = 32):
    """Load + compile TimesFM 2.5 from the pinned revision."""
    import torch
    import timesfm

    torch.set_float32_matmul_precision("high")
    print(f"Loading TimesFM 2.5 (repo={HF_REPO_ID} rev={HF_REVISION[:10]}...)")
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
        HF_REPO_ID, revision=HF_REVISION
    )
    model.compile(
        timesfm.ForecastConfig(
            max_context=1024,
            max_horizon=256,
            normalize_inputs=True,
            use_continuous_quantile_head=True,
            force_flip_invariance=True,
            infer_is_positive=True,
            fix_quantile_crossing=True,
            per_core_batch_size=batch_size,
        )
    )
    return model


def load_csv(path: str, date_col: str | None, value_cols: list[str] | None):
    df = pd.read_csv(path)
    if date_col and date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col])
    elif date_col:
        print(f"⚠️ date col '{date_col}' not found; cols={list(df.columns)}")
        date_col = None

    if value_cols:
        value_cols = [c for c in value_cols if c in df.columns]
    else:
        value_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if date_col in value_cols:
            value_cols.remove(date_col)
    if not value_cols:
        print("🛑 no numeric columns to forecast")
        sys.exit(1)
    print(f"Forecasting {len(value_cols)} series: {value_cols}")
    return df, value_cols, date_col


def forecast(model, df, value_cols, horizon: int) -> dict:
    inputs = [df[c].dropna().values.astype(np.float32) for c in value_cols]
    point, q = model.forecast(horizon=horizon, inputs=inputs)
    # q[:, :, k]: k=0 mean, 1..9 = q10..q90
    return {
        c: {
            "forecast": point[i].tolist(),
            "lower_90": q[i, :, 1].tolist(),
            "median": q[i, :, 5].tolist(),
            "upper_90": q[i, :, 9].tolist(),
        }
        for i, c in enumerate(value_cols)
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Hardened TimesFM CSV forecaster")
    p.add_argument("input", help="input CSV")
    p.add_argument("--horizon", type=int, required=True)
    p.add_argument("--date-col", default=None)
    p.add_argument("--value-cols", default=None, help="comma-separated")
    p.add_argument("--output", default=None)
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.add_argument("--batch-size", type=int, default=32)
    a = p.parse_args()

    vcols = a.value_cols.split(",") if a.value_cols else None
    df, vcols, dcol = load_csv(a.input, a.date_col, vcols)
    model = load_model(a.batch_size)
    results = forecast(model, df, vcols, a.horizon)

    if not a.output:
        print(json.dumps(results, indent=2))
        return
    if a.format == "json":
        Path(a.output).write_text(json.dumps(results, indent=2), encoding="utf-8")
    else:
        rows = [
            {"series": c, "step": h + 1, **{k: v[h] for k, v in d.items()}}
            for c, d in results.items()
            for h in range(a.horizon)
        ]
        pd.DataFrame(rows).to_csv(a.output, index=False)
    print(f"✅ wrote {a.output}")


if __name__ == "__main__":
    main()
