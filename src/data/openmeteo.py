"""Open-Meteo HISTORICAL FORECAST archive: what the forecast said at the time.

Do NOT use the ERA5 reanalysis host (its subdomain starts with "archive-api";
this module's host starts with "historical-forecast-api"). Reanalysis is
observed weather reconstructed after the fact. Feeding it to the model is a
look-ahead violation that produces excellent metrics and no information, and
it is the single most likely way this project silently fails (CLAUDE.md §3).

The literal reanalysis host string is deliberately never spelled out in full
here: `test_reanalysis_host_appears_nowhere_in_the_module` asserts it, so a
copy-paste of this docstring can never silently reintroduce it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pandas as pd

from src.data.cache import store_raw

BASE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
HOURLY_VARS = ("wind_speed_100m", "shortwave_radiation", "temperature_2m")

# Rough centroid of the NL onshore wind fleet. Offshore points are added in
# Phase 2 if wind features justify it.
NL_LAT, NL_LON = 52.1, 5.2


def _require_forecast_url(url: str) -> str:
    # ponytail: host-substring check only. The reanalysis path segment is not
    # spelled out here so it can't leak into `inspect.getsource` -- the host
    # check alone is sufficient since the reanalysis API is only ever served
    # from that host.
    if "archive-api" in url:
        raise ValueError(
            f"Refusing to fetch {url!r}: this is the ERA5 reanalysis endpoint. "
            "Reanalysis is observed weather, not a forecast issued at the time, "
            "and using it as a feature violates R1."
        )
    return url


def _parse(payload: dict[str, Any]) -> pd.DataFrame:
    hourly = payload["hourly"]
    idx = pd.DatetimeIndex(pd.to_datetime(hourly["time"])).tz_localize("UTC")
    return pd.DataFrame({v: hourly[v] for v in HOURLY_VARS}, index=idx)


def fetch_historical_forecast(
    start: date,
    end: date,
    latitude: float = NL_LAT,
    longitude: float = NL_LON,
    timeout: float = 60.0,
) -> pd.DataFrame:
    """Fetch the forecast as it was issued, for [start, end] inclusive."""
    url = _require_forecast_url(BASE_URL)
    params: dict[str, str | float] = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "UTC",
    }
    # ponytail: transport-level retry only (httpx's own HTTPTransport), no
    # hand-rolled retry loop and no new dependency.
    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(transport=transport, timeout=timeout) as client:
        response = client.get(url, params=params)
    response.raise_for_status()
    store_raw(
        "openmeteo",
        f"forecast_{latitude}_{longitude}_{start}_{end}",
        response.content,
        datetime.now(UTC),
    )
    return _parse(response.json())
