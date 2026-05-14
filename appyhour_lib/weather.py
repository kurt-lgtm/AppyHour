"""
Weather data fetching — OpenWeatherMap forecasts and NWS alerts.

Extracted from GelPackCalculator/gel_pack_shopify.py for reuse across
the MCP server and gel pack GUI without importing the 3200-line monolith.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests

# Default lookahead for NWS alert filtering
NWS_HOLD_DAYS_AHEAD = 4


def fetch_weather_by_zip(
    api_key: str, zip_code: str
) -> tuple[list[tuple[str, float]] | None, float | None, float | None]:
    """Fetch full 5-day/3-hour forecast for a US zip code via OpenWeatherMap.

    Returns ([(datetime_str, temp_F), ...], lat, lon) or (None, None, None).
    Each tuple is one 3-hour forecast reading.
    """
    # Geocode zip → lat/lon
    geo_url = "http://api.openweathermap.org/geo/1.0/zip"
    geo_params = {"zip": f"{zip_code},US", "appid": api_key}

    try:
        resp = requests.get(geo_url, params=geo_params, timeout=10)
        if resp.status_code != 200:
            return None, None, None
        geo = resp.json()
        lat, lon = geo.get("lat"), geo.get("lon")
        if lat is None or lon is None:
            return None, None, None
    except Exception:
        return None, None, None

    # Fetch 5-day forecast (3-hour intervals)
    url = "http://api.openweathermap.org/data/2.5/forecast"
    params = {"lat": lat, "lon": lon, "appid": api_key, "units": "imperial"}

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None, lat, lon
        data = resp.json()

        readings = []
        for entry in data.get("list", []):
            dt_txt = entry.get("dt_txt", "")
            temp = entry["main"]["temp"]
            readings.append((dt_txt, temp))

        if readings:
            return readings, lat, lon
        return None, lat, lon
    except Exception:
        return None, lat, lon


def fetch_weather_batch(
    api_key: str,
    zip_codes: list[str],
    progress_callback: object | None = None,
) -> tuple[dict, dict]:
    """Fetch weather for a list of unique zip codes concurrently.

    Returns (readings_dict, latlons_dict).
      readings_dict: {zip: [(datetime_str, temp_F), ...] or None}
      latlons_dict:  {zip: (lat, lon)}
    """
    readings_dict: dict = {}
    latlons_dict: dict = {}
    total = len(zip_codes)

    if total == 0:
        return readings_dict, latlons_dict

    def _fetch_one(zc: str) -> tuple:
        readings, lat, lon = fetch_weather_by_zip(api_key, zc)
        return zc, readings, lat, lon

    # OWM free tier: 60 calls/min — 4 workers keeps us under limit
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_one, zc): zc for zc in zip_codes}
        done = 0
        for future in as_completed(futures):
            done += 1
            if progress_callback:
                progress_callback(done, total, futures[future])
            zc, readings, lat, lon = future.result()
            readings_dict[zc] = readings
            if lat is not None and lon is not None:
                latlons_dict[zc] = (lat, lon)

    return readings_dict, latlons_dict


def fetch_nws_alerts(
    lat: float, lon: float, days_ahead: int = NWS_HOLD_DAYS_AHEAD
) -> tuple[list[dict], str | None]:
    """Fetch NWS weather alerts for a lat/lon within the next N days.

    Returns (alerts, error_str) where error_str is None on success.
    Uses the public NWS API — no key required.
    """
    url = "https://api.weather.gov/alerts"
    params = {"point": f"{lat:.4f},{lon:.4f}", "status": "actual"}
    headers = {
        "User-Agent": "ElevateFoodsGelPackCalculator/2.0",
        "Accept": "application/geo+json",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"
        data = resp.json()
        cutoff = datetime.now() + timedelta(days=days_ahead)
        alerts: list[dict] = []
        seen_events: set[str] = set()
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            event = props.get("event", "")
            effective_str = props.get("effective") or ""
            expires_str = props.get("expires") or props.get("ends") or ""
            try:
                eff = datetime.fromisoformat(effective_str)
                eff_naive = eff.replace(tzinfo=None)
            except (ValueError, AttributeError):
                eff_naive = datetime.now()
            # Deduplicate: NWS returns same alert for multiple zones/areas
            if eff_naive <= cutoff and event not in seen_events:
                seen_events.add(event)
                alerts.append({
                    "event": event,
                    "headline": props.get("headline", event),
                    "severity": props.get("severity", "Unknown"),
                    "urgency": props.get("urgency", "Unknown"),
                    "effective": effective_str,
                    "expires": expires_str,
                    "area": props.get("areaDesc", ""),
                })
        return alerts, None
    except requests.exceptions.Timeout:
        return [], "timeout"
    except requests.exceptions.ConnectionError as e:
        return [], f"connection error: {e}"
    except Exception as e:
        return [], str(e)
