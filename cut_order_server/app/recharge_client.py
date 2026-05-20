"""Recharge queued-charges client — v2021-11, cursor pagination, 30s timeout, retry."""
from __future__ import annotations

import time
from datetime import date
from typing import Iterator
import requests
from .creds import get_recharge_token


def _headers() -> dict[str, str]:
    return {
        "X-Recharge-Access-Token": get_recharge_token(),
        "Accept": "application/json",
        "X-Recharge-Version": "2021-11",
    }


def _get(path: str, params: dict | None = None, retries: int = 5, timeout: int = 30) -> dict:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(
                f"https://api.rechargeapps.com{path}",
                headers=_headers(),
                params=params,
                timeout=timeout,
            )
            if r.status_code == 429:
                time.sleep(int(r.headers.get("retry-after", "5")))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as e:
            last = e
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(2)
    if last:
        raise last
    raise RuntimeError(f"Recharge GET {path} failed after {retries} retries")


def fetch_queued_charges(scheduled_min: date, scheduled_max: date) -> Iterator[dict]:
    """Yield queued charges in [scheduled_min, scheduled_max] window.

    Cursor pagination per skill: filters/sort on first request only, cursor+limit after.
    """
    cursor: str | None = None
    while True:
        if cursor:
            params = {"cursor": cursor, "limit": 250}
        else:
            params = {
                "status": "queued",
                "limit": 250,
                "sort_by": "id-asc",
                "scheduled_at_min": scheduled_min.isoformat(),
                "scheduled_at_max": scheduled_max.isoformat(),
            }
        data = _get("/charges", params=params)
        batch = data.get("charges", [])
        if not batch:
            break
        for c in batch:
            yield c
        cursor = data.get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)
