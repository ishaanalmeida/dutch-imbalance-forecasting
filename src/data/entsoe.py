"""ENTSO-E Transparency Platform fetchers.

Lean zone: entsoe-py does the protocol work. We add caching of raw responses,
canonical UTC indexing, and column naming that matches config/market_rules.yaml
field names (`price_long`, `price_short`) so data_availability can be
consulted by the same name.

TOKEN-GATED. There is no ENTSOE_API_TOKEN yet (requested 2026-08-04, pending
issuance). Every function below that calls EntsoePandasClient is exercised
only by an `@pytest.mark.integration` test, skipped by default -- see
pyproject.toml addopts (`-m 'not integration'`) and tests/test_entsoe.py.

UNVERIFIED until the token arrives and the first live call runs: whether
`query_imbalance_prices` actually returns two distinct columns for NL (the
dual-price structure for regulation state 2, DOMAIN_NOTES.md Q3) or just one.
If only one, this item is single-priced and TenneT's own feed is required for
the take/feed split -- see tests/test_entsoe.py's first integration test and
this module's docstring on fetch_imbalance_prices.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import cast

import pandas as pd
from entsoe.entsoe import EntsoePandasClient

from src.data.cache import store_raw, write_frame

NL_DOMAIN = "10YNL----------L"


class MissingTokenError(RuntimeError):
    """ENTSOE_API_TOKEN is not set."""


def _require_token() -> str:
    token = os.getenv("ENTSOE_API_TOKEN")
    if not token:
        raise MissingTokenError(
            "ENTSOE_API_TOKEN is not set. Copy .env.example to .env and add the "
            "token issued by transparency@entsoe.eu. See docs/DATA_SOURCES.md."
        )
    return token


def _client() -> EntsoePandasClient:
    return EntsoePandasClient(api_key=_require_token())


def _stamps(start: datetime, end: datetime) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """`Some Column Name` -> `some_column_name`, to match market_rules.yaml
    field names. Pure and side-effect-free so it is testable without a token
    or a fixture -- see test_normalise_columns_lowercases_and_underscores."""
    df = df.copy()
    df.columns = pd.Index(str(c).strip().lower().replace(" ", "_") for c in df.columns)
    return df


def _record(dataset: str, start: datetime, end: datetime, frame: pd.DataFrame) -> None:
    store_raw(
        "entsoe",
        f"{dataset}_{start:%Y%m%dT%H%M}_{end:%Y%m%dT%H%M}",
        frame.to_json().encode(),
        datetime.now(UTC),
    )


def fetch_imbalance_prices(start: datetime, end: datetime) -> pd.DataFrame:
    """Settled imbalance prices. THIS IS THE TARGET, never a feature.

    Expected columns: `price_long`, `price_short` (market_rules.yaml's dual-
    price naming). UNVERIFIED: whether the live NL item actually carries two
    distinct columns, or collapses to one -- see module docstring.
    """
    s, e = _stamps(start, end)
    raw = _client().query_imbalance_prices(NL_DOMAIN, start=s, end=e)
    df = _normalise_columns(raw.tz_convert("UTC"))
    _record("imbalance_prices", start, end, df)
    if not df.empty:
        write_frame("imbalance_prices", df)
    return df


def fetch_day_ahead_prices(start: datetime, end: datetime) -> pd.Series[float]:
    s, e = _stamps(start, end)
    series = _client().query_day_ahead_prices(NL_DOMAIN, start=s, end=e).tz_convert("UTC")
    _record("day_ahead_prices", start, end, series.to_frame("day_ahead_price"))
    named = cast("pd.Series[float]", series.rename("day_ahead_price"))
    if not named.empty:
        write_frame("day_ahead_price", named.to_frame())
    return named


def fetch_load_forecast(start: datetime, end: datetime) -> pd.Series[float]:
    s, e = _stamps(start, end)
    series = cast(
        "pd.Series[float]", _client().query_load_forecast(NL_DOMAIN, start=s, end=e).squeeze()
    )
    series = series.tz_convert("UTC")
    _record("load_forecast", start, end, series.to_frame("load_forecast"))
    named = series.rename("load_forecast")
    if not named.empty:
        write_frame("load_forecast", named.to_frame())
    return named


def fetch_wind_solar_forecast(start: datetime, end: datetime) -> pd.DataFrame:
    s, e = _stamps(start, end)
    raw = _client().query_wind_and_solar_forecast(NL_DOMAIN, start=s, end=e)
    df = _normalise_columns(raw.tz_convert("UTC"))
    _record("wind_solar_forecast", start, end, df)
    if not df.empty:
        write_frame("wind_solar_forecast", df)
    return df
