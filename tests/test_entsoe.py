"""ENTSO-E fetcher tests.

No token exists yet (requested 2026-08-04, pending). Everything here except
the `@pytest.mark.integration` tests runs offline: token-handling and the
pure column-normalisation helper. The fetch_* functions all call
EntsoePandasClient directly, so every test that exercises them is marked
integration and skipped without ENTSOE_API_TOKEN -- see pyproject.toml
addopts (`-m 'not integration'` by default).

R3: no fixture of a "real" ENTSO-E response is committed here. We have never
seen one. A hand-built XML/DataFrame dressed up as a recorded response would
silently poison every downstream assumption about column names and, in
particular, whether the NL imbalance item actually carries two distinct price
columns (the dual-price question from DOMAIN_NOTES.md Q3) -- that is an
empirical fact about live data, not something to guess at. The
column-normalisation test below uses an input built by hand in the test
itself and says so.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import cast

import pandas as pd
import pytest

from src.data import entsoe


def test_missing_token_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENTSOE_API_TOKEN", raising=False)
    with pytest.raises(entsoe.MissingTokenError, match="ENTSOE_API_TOKEN"):
        entsoe._require_token()


def test_token_is_never_logged_or_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8. The error message must not contain the token value.

    Sets a known secret, confirms the happy path returns it untouched, then
    removes it and confirms the resulting error message -- a fixed string --
    never echoes the secret that was previously in the environment.
    """
    monkeypatch.setenv("ENTSOE_API_TOKEN", "supersecret123")
    assert entsoe._require_token() == "supersecret123"

    monkeypatch.delenv("ENTSOE_API_TOKEN", raising=False)
    with pytest.raises(entsoe.MissingTokenError) as exc_info:
        entsoe._require_token()
    assert "supersecret123" not in str(exc_info.value)


def test_nl_domain_code_is_correct() -> None:
    assert entsoe.NL_DOMAIN == "10YNL----------L"


def test_normalise_columns_lowercases_and_underscores_synthetic_input() -> None:
    """Pure helper, tested on a hand-built frame -- NOT a recorded ENTSO-E
    response. Column names below (`Long`, `Short Price`) are invented purely
    to exercise the normalisation rule, not a claim about what the API
    returns."""
    df = pd.DataFrame({"Long": [1.0], "Short Price": [2.0]})
    out = entsoe._normalise_columns(df)
    assert list(out.columns) == ["long", "short_price"]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ENTSOE_API_TOKEN"), reason="ENTSOE_API_TOKEN not set")
def test_live_imbalance_fetch_returns_utc_15min_index() -> None:
    df = entsoe.fetch_imbalance_prices(
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 6, 2, tzinfo=UTC),
    )
    assert str(cast(pd.DatetimeIndex, df.index).tz) == "UTC"
    assert len(df) == 96
    assert {"price_long", "price_short"} <= set(df.columns)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ENTSOE_API_TOKEN"), reason="ENTSOE_API_TOKEN not set")
def test_live_day_ahead_fetch_returns_utc_series() -> None:
    series = entsoe.fetch_day_ahead_prices(
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 6, 2, tzinfo=UTC),
    )
    assert str(cast(pd.DatetimeIndex, series.index).tz) == "UTC"
    assert series.name == "day_ahead_price"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ENTSOE_API_TOKEN"), reason="ENTSOE_API_TOKEN not set")
def test_live_load_forecast_fetch_returns_utc_series() -> None:
    series = entsoe.fetch_load_forecast(
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 6, 2, tzinfo=UTC),
    )
    assert str(cast(pd.DatetimeIndex, series.index).tz) == "UTC"
    assert series.name == "load_forecast"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ENTSOE_API_TOKEN"), reason="ENTSOE_API_TOKEN not set")
def test_live_wind_solar_fetch_returns_utc_frame() -> None:
    df = entsoe.fetch_wind_solar_forecast(
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 6, 2, tzinfo=UTC),
    )
    assert str(cast(pd.DatetimeIndex, df.index).tz) == "UTC"
