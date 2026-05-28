"""Weather sync cron — backfill weather_history for past N days of dest zips.

Why this exists:
  weather_history is populated only by UI-driven calls in Kori (forecast +
  historical lookup). After Apr 16 the table is empty because no one clicked
  the right buttons. This script fills the gap by reading recent shipments,
  finding their dest zips + delivery date windows, and calling OWM timemachine
  for each missing (zip, date) pair.

Schedule (Windows Task Scheduler suggestion):
  Daily at 03:00 ET. Runs ~1-2 min depending on zip novelty.

Source: OpenWeatherMap One Call 3.0 timemachine (paid tier, key in Kori
  settings). Free tier: 1000 calls/day — well within our scale.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────
SETTINGS_PATH = Path.home() / "AppData/Roaming/AppyHour/gel_calc_shopify_settings.json"
DB_PATH = Path.home() / "AppData/Roaming/AppyHour/shipping.db"
LOOKBACK_DAYS = 30  # how far back to backfill (covers 2 ship_weeks comfortably)
PADDING_DAYS = 2    # extend window past delivery date for tail-end temps
MAX_CALLS = 1000    # OWM free-tier daily cap
SLEEP_BETWEEN = 0.2  # seconds — stay polite, 5 calls/sec ≈ 300/min < 60/min limit irrelevant for timemachine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path.home() / "AppData/Roaming/AppyHour/weather_sync.log"),
    ],
)
log = logging.getLogger("weather-sync")


def load_api_key() -> str:
    """Pull OWM key from Kori settings file."""
    if not SETTINGS_PATH.exists():
        raise FileNotFoundError(f"Kori settings not found: {SETTINGS_PATH}")
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        s = json.load(f)
    key = s.get("api_key", "").strip()
    if not key:
        raise ValueError("OpenWeatherMap api_key empty in Kori settings")
    return key


def get_needed_pairs(db: sqlite3.Connection, lookback_days: int) -> list[tuple[str, str]]:
    """Return list of (zip5, YYYY-MM-DD) pairs we don't yet have weather for.

    Strategy: look at fulfillments + delivery_status in the past N days.
    For each shipment, generate a date range [pickup_date, delivery_date+pad]
    (or [fulfilled_at, fulfilled_at+5] as fallback).
    Cross with weather_history. Return only the missing pairs.
    """
    today = date.today()
    earliest = (today - timedelta(days=lookback_days)).isoformat()
    log.info(f"Scanning fulfillments since {earliest}")

    cur = db.execute(
        """
        SELECT DISTINCT
          substr(f.dest_zip, 1, 5) AS zip5,
          COALESCE(ds.pickup_date, date(f.fulfilled_at)) AS start_d,
          COALESCE(ds.delivery_date, date(f.fulfilled_at, '+5 days')) AS end_d
        FROM fulfillments f
        LEFT JOIN delivery_status ds ON ds.tracking_number = f.tracking_number
        WHERE f.fulfilled_at >= ?
          AND f.dest_zip IS NOT NULL
          AND length(substr(f.dest_zip, 1, 5)) = 5
        """,
        (earliest,),
    )

    needed: set[tuple[str, str]] = set()
    for row in cur:
        zip5, start_d, end_d = row
        if not zip5 or not start_d:
            continue
        try:
            d0 = datetime.fromisoformat(start_d[:10]).date()
            d1 = datetime.fromisoformat(end_d[:10]).date() if end_d else d0 + timedelta(days=2)
        except (ValueError, TypeError):
            continue
        # Pad end so we capture peak-temp at delivery
        d1 = d1 + timedelta(days=PADDING_DAYS)
        cur_d = d0
        while cur_d <= d1 and cur_d <= today:
            needed.add((zip5, cur_d.isoformat()))
            cur_d += timedelta(days=1)

    # Filter against existing weather_history
    if not needed:
        return []
    needed_list = list(needed)
    # Chunk to avoid SQL parameter limit (999 by default in SQLite)
    have: set[tuple[str, str]] = set()
    CHUNK = 400
    for i in range(0, len(needed_list), CHUNK):
        chunk = needed_list[i : i + CHUNK]
        placeholders = ",".join(["(?,?)"] * len(chunk))
        params = [v for pair in chunk for v in pair]
        existing = db.execute(
            f"SELECT zip_prefix, date FROM weather_history "
            f"WHERE (zip_prefix, date) IN ({placeholders})",
            params,
        ).fetchall()
        have.update(tuple(r) for r in existing)

    missing = [p for p in needed_list if p not in have]
    log.info(f"Need {len(needed_list)} pairs, have {len(have)}, fetching {len(missing)}")
    return missing


def geocode_zip(zip5: str, api_key: str) -> tuple[float, float] | None:
    """OWM geocoding zip → (lat, lon). Cached for session via lru_cache.
    Returns None if API fails.
    """
    try:
        resp = requests.get(
            "http://api.openweathermap.org/geo/1.0/zip",
            params={"zip": f"{zip5},US", "appid": api_key},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning(f"Geocode {zip5}: HTTP {resp.status_code}")
            return None
        data = resp.json()
        lat, lon = data.get("lat"), data.get("lon")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except Exception as e:
        log.warning(f"Geocode {zip5} failed: {e}")
        return None


def fetch_one(zip5: str, date_str: str, lat: float, lon: float, api_key: str) -> dict | None:
    """OWM timemachine for one (zip, date). Returns {avg, peak} or None."""
    try:
        dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
        unix_ts = int(dt_obj.timestamp())
        resp = requests.get(
            "https://api.openweathermap.org/data/3.0/onecall/timemachine",
            params={
                "lat": lat,
                "lon": lon,
                "dt": unix_ts,
                "appid": api_key,
                "units": "imperial",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning(f"OWM {zip5} {date_str}: HTTP {resp.status_code}")
            return None
        data = resp.json()
        hourly = data.get("data", [])
        if not hourly:
            return None
        temps = [h.get("temp") for h in hourly if h.get("temp") is not None]
        if not temps:
            return None
        return {"avg": round(sum(temps) / len(temps), 1), "peak": round(max(temps), 1)}
    except Exception as e:
        log.warning(f"OWM {zip5} {date_str} failed: {e}")
        return None


def main() -> int:
    api_key = load_api_key()
    if not DB_PATH.exists():
        log.error(f"shipping.db missing: {DB_PATH}")
        return 1
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row

    try:
        missing = get_needed_pairs(db, LOOKBACK_DAYS)
        if not missing:
            log.info("Nothing to fetch — weather_history fully covered for window.")
            return 0
        if len(missing) > MAX_CALLS:
            log.warning(f"Capping at {MAX_CALLS} (have {len(missing)} missing)")
            missing = missing[:MAX_CALLS]

        # Geocode per unique zip (cache)
        geo_cache: dict[str, tuple[float, float] | None] = {}
        ok, fail = 0, 0
        for i, (zip5, date_str) in enumerate(missing, 1):
            if zip5 not in geo_cache:
                geo_cache[zip5] = geocode_zip(zip5, api_key)
                time.sleep(SLEEP_BETWEEN)
            geo = geo_cache[zip5]
            if not geo:
                fail += 1
                continue
            lat, lon = geo
            temps = fetch_one(zip5, date_str, lat, lon, api_key)
            time.sleep(SLEEP_BETWEEN)
            if not temps:
                fail += 1
                continue
            db.execute(
                """
                INSERT OR REPLACE INTO weather_history
                    (zip_prefix, date, avg_temp, peak_temp, lat, lon)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (zip5, date_str, temps["avg"], temps["peak"], lat, lon),
            )
            ok += 1
            if i % 25 == 0:
                db.commit()
                log.info(f"Progress: {i}/{len(missing)} (ok={ok}, fail={fail})")
        db.commit()
        log.info(f"Done. ok={ok}, fail={fail}, total={len(missing)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
