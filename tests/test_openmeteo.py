"""Guards against the project's highest silent-failure risk (CLAUDE.md §3):
ERA5 reanalysis (archive-api.open-meteo.com) is observed weather, not a
forecast issued at decision time. Feeding it to the model produces excellent
metrics and zero information. Two independent guards below: a source-level
check that the reanalysis host string appears nowhere in the module, and a
runtime check that rejects a reanalysis URL if one ever reaches the fetcher.

All tests here run offline. The live end-to-end call is `@pytest.mark.integration`
and excluded by default (see pyproject.toml addopts) -- see the brief's Step 6
smoke test for the manual, network-hitting check.
"""

from __future__ import annotations

import inspect
from datetime import date
from typing import cast

import pandas as pd
import pytest

from src.data import openmeteo


def test_only_the_historical_forecast_host_is_used() -> None:
    """R1's weather clause. The reanalysis archive must never appear."""
    assert openmeteo.BASE_URL.startswith("https://historical-forecast-api.open-meteo.com")


def test_reanalysis_host_appears_nowhere_in_the_module() -> None:
    source = inspect.getsource(openmeteo)
    assert "archive-api.open-meteo.com" not in source, (
        "ERA5 reanalysis is observed weather, not a forecast. Using it as a "
        "feature is the most likely silent failure in this project."
    )
    assert "/v1/archive" not in source


def test_guard_rejects_a_reanalysis_url() -> None:
    with pytest.raises(ValueError, match="reanalysis"):
        openmeteo._require_forecast_url("https://archive-api.open-meteo.com/v1/archive?x=1")


def test_guard_accepts_the_forecast_url() -> None:
    openmeteo._require_forecast_url(openmeteo.BASE_URL)


def test_guard_rejects_a_plausible_but_wrong_host() -> None:
    """The allowlist must reject every non-approved host, not just the one
    reanalysis host we thought to deny -- this is the point of an allowlist
    over a denylist, and a denylist would wave this one through."""
    with pytest.raises(ValueError, match="only"):
        openmeteo._require_forecast_url("https://api.open-meteo.com/v1/forecast?x=1")


def test_parse_produces_utc_indexed_frame() -> None:
    payload = {
        "hourly": {
            "time": ["2026-06-01T00:00", "2026-06-01T01:00"],
            "wind_speed_100m": [12.0, 14.0],
            "shortwave_radiation": [0.0, 5.0],
            "temperature_2m": [11.0, 11.5],
        }
    }
    df = openmeteo._parse(payload)
    assert str(cast(pd.DatetimeIndex, df.index).tz) == "UTC"
    assert list(df.columns) == ["wind_speed_100m", "shortwave_radiation", "temperature_2m"]
    assert len(df) == 2


@pytest.mark.integration
def test_fetch_historical_forecast_returns_48_rows_for_two_day_range() -> None:
    """Live network call, excluded by default. Run with `pytest -m integration`."""
    df = openmeteo.fetch_historical_forecast(date(2026, 6, 1), date(2026, 6, 2))
    assert df.shape == (48, 3)
    assert str(cast(pd.DatetimeIndex, df.index).tz) == "UTC"
