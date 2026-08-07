"""The vintage-logging job. Offline: the live fetch is stubbed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.data import vintage
from src.jobs import log_weather_vintage

T0 = datetime(2026, 8, 7, 6, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vintage, "VINTAGE_ROOT", tmp_path)


def _synthetic(values: list[float]) -> pd.DataFrame:
    """Hand-built frame -- NOT a recorded Open-Meteo response (R3)."""
    idx = pd.date_range("2026-08-08T00:00Z", periods=len(values), freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "wind_speed_100m": values,
            "shortwave_radiation": [0.0] * len(values),
            "temperature_2m": [15.0] * len(values),
        },
        index=idx,
    )


def test_run_records_one_vintage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        log_weather_vintage, "fetch_live_forecast", lambda **kw: _synthetic([10.0, 11.0])
    )
    assert log_weather_vintage.run(observed_at=T0) == 2

    stored = vintage.read_vintages("weather_forecast")
    assert len(stored) == 2
    assert (stored["observed_at"] == pd.Timestamp(T0)).all()


def test_successive_runs_accumulate_rather_than_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property the whole job exists for."""
    monkeypatch.setattr(
        log_weather_vintage, "fetch_live_forecast", lambda **kw: _synthetic([10.0, 11.0])
    )
    log_weather_vintage.run(observed_at=T0)

    monkeypatch.setattr(
        log_weather_vintage, "fetch_live_forecast", lambda **kw: _synthetic([20.0, 21.0])
    )
    log_weather_vintage.run(observed_at=T0 + timedelta(hours=6))

    assert len(vintage.read_vintages("weather_forecast")) == 4

    # And the revision is recoverable: what was visible before the second run
    # differs from what was visible after it.
    before = vintage.latest_as_of("weather_forecast", T0 + timedelta(hours=1))
    after = vintage.latest_as_of("weather_forecast", T0 + timedelta(hours=7))
    assert before["wind_speed_100m"].tolist() == [10.0, 11.0]
    assert after["wind_speed_100m"].tolist() == [20.0, 21.0]


def test_forecast_revision_is_computable_from_two_vintages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The forecast-error proxy CLAUDE.md §4 asks for: successive vintages of
    the same target hour, differenced. Available only because both were kept."""
    monkeypatch.setattr(
        log_weather_vintage, "fetch_live_forecast", lambda **kw: _synthetic([10.0, 11.0])
    )
    log_weather_vintage.run(observed_at=T0)
    monkeypatch.setattr(
        log_weather_vintage, "fetch_live_forecast", lambda **kw: _synthetic([14.0, 11.5])
    )
    log_weather_vintage.run(observed_at=T0 + timedelta(hours=6))

    frame = vintage.read_vintages("weather_forecast")
    wide = frame.pivot(index="target_time", columns="observed_at", values="wind_speed_100m")
    revision = wide.iloc[:, 1] - wide.iloc[:, 0]
    assert revision.tolist() == [4.0, 0.5]


def test_empty_forecast_records_nothing_and_signals_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty response must not be recorded as 'we observed nothing'."""
    empty = pd.DataFrame(
        {v: [] for v in ("wind_speed_100m", "shortwave_radiation", "temperature_2m")},
        index=pd.DatetimeIndex([], tz="UTC"),
    )
    monkeypatch.setattr(log_weather_vintage, "fetch_live_forecast", lambda **kw: empty)

    assert log_weather_vintage.run(observed_at=T0) == 0
    assert vintage.read_vintages("weather_forecast").empty
    assert log_weather_vintage.main([]) == 1, "empty run must exit non-zero so cron alerts"


def test_forecast_days_is_bounded() -> None:
    from src.data.openmeteo import fetch_live_forecast

    for bad in (0, 17, -1):
        with pytest.raises(ValueError, match="forecast_days"):
            fetch_live_forecast(forecast_days=bad)
