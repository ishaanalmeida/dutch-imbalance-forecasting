"""ENTSO-E fetcher tests.

Everything here except the `@pytest.mark.integration` tests runs offline, so
the suite stays green without credentials -- see pyproject.toml addopts
(`-m 'not integration'` by default). Run the live ones with
`uv run pytest -m integration`.

VERIFIED LIVE 2026-08-07: the NL imbalance item DOES carry two distinct price
columns (ENTSO-E names them `Long`/`Short`), so the dual-price question from
DOMAIN_NOTES.md Q3 is resolved and TenneT's own feed is not required for
settlement. `price_short >= price_long` held across 8,064 sampled ISPs with
zero violations, independently confirming the invariant derived by hand from
[IPS61] Table 2.

R3: no fixture of a "real" ENTSO-E response is committed here even now. Every
synthetic frame below is hand-built to exercise a code path and says so; the
facts about live data are asserted against live data, in the integration
tests, not baked into a fixture that would drift.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from src.data import cache, entsoe
from src.env import load_env

# Integration tests are the only ones here that need real credentials, so this
# module loads .env explicitly at import — before the skipif conditions below
# are evaluated. Library code deliberately never loads .env: if it did, a call
# would silently repopulate an environment a caller had cleared, and the
# missing-token tests could not simulate a missing token.
load_env()


@pytest.fixture(autouse=True)
def _tmp_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "DATA_ROOT", tmp_path)


class _FakeClient:
    """Stands in for EntsoePandasClient: returns whatever the test hands it,
    no network, no token. Only used via `monkeypatch.setattr(entsoe, "_client", ...)`."""

    def __init__(
        self,
        imbalance: pd.DataFrame | None = None,
        day_ahead: pd.Series[float] | None = None,
        load: pd.Series[float] | None = None,
        wind_solar: pd.DataFrame | None = None,
    ) -> None:
        self.imbalance = imbalance
        self.day_ahead = day_ahead
        self.load = load
        self.wind_solar = wind_solar

    def query_imbalance_prices(self, domain: str, start: object, end: object) -> pd.DataFrame:
        assert self.imbalance is not None
        return self.imbalance

    def query_day_ahead_prices(self, domain: str, start: object, end: object) -> pd.Series[float]:
        assert self.day_ahead is not None
        return self.day_ahead

    def query_load_forecast(self, domain: str, start: object, end: object) -> pd.Series[float]:
        assert self.load is not None
        return self.load

    def query_wind_and_solar_forecast(
        self, domain: str, start: object, end: object
    ) -> pd.DataFrame:
        assert self.wind_solar is not None
        return self.wind_solar


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
    assert {"price_long", "price_short"} <= set(df.columns), (
        "ENTSO-E's NL imbalance item must carry two distinct price columns. "
        "Verified 2026-08-07: it names them Long/Short and we rename at the "
        "boundary. If this ever fails, the dual-price structure is no longer "
        "available from ENTSO-E and TenneT's own feed becomes required."
    )
    # The settlement invariant from [IPS61] Table 2, checked against live data.
    assert (df["price_short"] >= df["price_long"] - 1e-9).all(), (
        "price_short < price_long: a BRP would be paid more for being long "
        "than charged for being short. Either the columns are swapped or the "
        "settlement rules have changed."
    )


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


# --- write_frame wiring (Finding 1): fetchers must persist the parsed frame
# to the Parquet cache, not just the raw response. Every payload below is
# SYNTHETIC -- hand-built to exercise the caching path, not a recorded
# ENTSO-E response (R3) -- see module docstring's note on why no "real"
# response fixture is committed here.


def test_fetch_imbalance_prices_writes_to_parquet_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    synthetic = pd.DataFrame(
        {"Long": [1.0, 2.0, 3.0, 4.0], "Short": [1.1, 2.1, 3.1, 4.1]}, index=idx
    )
    monkeypatch.setattr(entsoe, "_client", lambda: _FakeClient(imbalance=synthetic))

    out = entsoe.fetch_imbalance_prices(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 1, 1, tzinfo=UTC)
    )

    cached = cache.read_frame(
        "imbalance_prices", datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC)
    )
    # Parquet does not persist DatetimeIndex.freq -- see test_cache.py's
    # test_frame_round_trip_preserves_utc_index for the same hazard.
    pd.testing.assert_frame_equal(cached, out, check_freq=False)


def test_fetch_day_ahead_prices_writes_to_parquet_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    synthetic = cast(
        "pd.Series[float]", pd.Series([10.0, 20.0, 30.0, 40.0], index=idx, name="Price")
    )
    monkeypatch.setattr(entsoe, "_client", lambda: _FakeClient(day_ahead=synthetic))

    out = entsoe.fetch_day_ahead_prices(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 1, 1, tzinfo=UTC)
    )

    cached = cache.read_frame(
        "day_ahead_price", datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC)
    )
    pd.testing.assert_series_equal(cached["day_ahead_price"], out, check_freq=False)


def test_fetch_load_forecast_writes_to_parquet_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    synthetic = cast(
        "pd.Series[float]", pd.Series([100.0, 110.0, 120.0, 130.0], index=idx, name="Load")
    )
    monkeypatch.setattr(entsoe, "_client", lambda: _FakeClient(load=synthetic))

    out = entsoe.fetch_load_forecast(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 1, 1, tzinfo=UTC)
    )

    cached = cache.read_frame(
        "load_forecast", datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC)
    )
    pd.testing.assert_series_equal(cached["load_forecast"], out, check_freq=False)


def test_fetch_wind_solar_forecast_writes_to_parquet_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    synthetic = pd.DataFrame(
        {"Wind Onshore": [1.0, 2.0, 3.0, 4.0], "Solar": [0.0, 0.0, 1.0, 2.0]}, index=idx
    )
    monkeypatch.setattr(entsoe, "_client", lambda: _FakeClient(wind_solar=synthetic))

    out = entsoe.fetch_wind_solar_forecast(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 1, 1, tzinfo=UTC)
    )

    cached = cache.read_frame(
        "wind_solar_forecast", datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC)
    )
    pd.testing.assert_frame_equal(cached, out, check_freq=False)


def test_fetch_imbalance_prices_does_not_write_empty_partition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: an empty partition on disk would read as 'we have data for this
    month, and it is empty', which is not true -- we have no data at all."""
    empty = pd.DataFrame(
        {"Long": pd.Series(dtype="float64"), "Short": pd.Series(dtype="float64")},
        index=pd.DatetimeIndex([], tz="UTC"),
    )
    monkeypatch.setattr(entsoe, "_client", lambda: _FakeClient(imbalance=empty))

    entsoe.fetch_imbalance_prices(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 1, 1, tzinfo=UTC)
    )

    assert list((cache.DATA_ROOT / "processed" / "imbalance_prices").glob("*.parquet")) == []
