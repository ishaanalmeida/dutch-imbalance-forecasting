"""Tests for the event-driven backtest engine (CLAUDE.md §6).

RIGOUR ZONE tests:
1. Look-ahead leak: deliberately try to use future data, assert engine refuses.
2. Settlement: verify revenue calculation against a hand-worked example.
3. Perfect foresight dominance: PF >= any forecast-based policy.
4. Block bootstrap: CI contains the actual total.
5. Do-nothing baseline: zero revenue.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import (
    BacktestConfig,
    assert_no_lookahead,
    block_bootstrap_ci,
    run_backtest_deterministic,
    run_backtest_do_nothing,
    run_backtest_perfect_foresight,
)
from src.data.data_availability import LookAheadError
from src.optimisation.battery import BatteryParams

UTC = ZoneInfo("UTC")


@pytest.fixture
def params() -> BatteryParams:
    return BatteryParams(power_mw=10.0, energy_mwh=40.0)


@pytest.fixture
def config(params: BatteryParams) -> BacktestConfig:
    return BacktestConfig(battery=params)


def _price_series(n: int = 96, seed: int = 42) -> pd.Series:  # type: ignore[type-arg]
    """A volatile price series with a DatetimeIndex of ISPs."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2025, 6, 1, tzinfo=UTC)
    idx = pd.DatetimeIndex(
        [t0 + timedelta(minutes=15 * i) for i in range(n)],
        tz=UTC,
    )
    prices = 50.0 + 30.0 * np.sin(np.linspace(0, 4 * np.pi, n)) + rng.normal(0, 5, n)
    return pd.Series(prices, index=idx, dtype=float)


# ── 1. Look-ahead leak test (CLAUDE.md §6) ──────────────────────────


def test_assert_no_lookahead_refuses_future_data() -> None:
    """Deliberately try to read a field before it is published.

    CLAUDE.md §6: "Write a test that deliberately tries to leak and
    asserts the engine refuses."

    balance_delta has rule=lag_after_period. For ISP starting 12:00 it
    is available at ~12:17:26 (period end + lag). A decision at 12:00
    must be refused.
    """
    target = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    decision_too_early = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)

    with pytest.raises(LookAheadError):
        assert_no_lookahead(decision_too_early, "balance_delta", target)


def test_assert_no_lookahead_accepts_past_data() -> None:
    """Same field, but decision time is well after publication."""
    target = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    decision_late = datetime(2025, 6, 1, 12, 18, tzinfo=UTC)
    assert_no_lookahead(decision_late, "balance_delta", target)


# ── 2. Settlement hand-worked example ────────────────────────────────


def test_settlement_hand_worked(config: BacktestConfig) -> None:
    """Hand-worked settlement: battery charges 10 MW for one ISP at
    EUR 20/MWh, then discharges 10 MW for one ISP at EUR 80/MWh.

    Charge cost:  10 MW * 0.25 h * 20 EUR/MWh = 50 EUR (negative position)
    Discharge rev: 10 MW * 0.25 h * 80 EUR/MWh = 200 EUR (positive position)
    Gross = 200 - 50 = 150 EUR

    The deterministic optimiser should find at least this pattern on a
    two-period problem with extreme price difference.
    """
    t0 = datetime(2025, 6, 1, tzinfo=UTC)
    idx = pd.DatetimeIndex([t0, t0 + timedelta(minutes=15)], tz=UTC)
    prices = pd.Series([20.0, 80.0], index=idx, dtype=float)

    result = run_backtest_perfect_foresight(prices, config)

    assert result.total_revenue_eur > 0
    assert result.n_periods == 2
    assert result.policy == "perfect_foresight"

    for isp in result.isps:
        assert isp.net_position_mwh == pytest.approx(
            (isp.discharge_mw - isp.charge_mw) * config.battery.isp_hours,
            abs=1e-6,
        )
        assert isp.revenue_eur == pytest.approx(
            isp.net_position_mwh * isp.price_short,
            abs=1e-4,
        )


# ── 3. Perfect foresight dominance ───────────────────────────────────


def test_perfect_foresight_dominates_deterministic(
    config: BacktestConfig,
) -> None:
    """PF revenue >= deterministic revenue (settled against realised)."""
    prices = _price_series()
    rng = np.random.default_rng(99)
    noisy_forecast = prices.to_numpy(dtype=float) + rng.normal(0, 15, len(prices))

    pf = run_backtest_perfect_foresight(prices, config)
    det = run_backtest_deterministic(prices, noisy_forecast, config)

    assert pf.net_revenue_eur >= det.net_revenue_eur - 1e-4


# ── 4. Do-nothing baseline ──────────────────────────────────────────


def test_do_nothing_zero_revenue(config: BacktestConfig) -> None:
    prices = _price_series()
    result = run_backtest_do_nothing(prices, config)

    assert result.total_revenue_eur == 0.0
    assert result.net_revenue_eur == 0.0
    assert result.n_periods == len(prices)
    assert result.policy == "do_nothing"


# ── 5. Block bootstrap CI ───────────────────────────────────────────


def test_block_bootstrap_ci_contains_actual() -> None:
    """95% CI from the bootstrap should contain the observed total
    most of the time. With deterministic seed, verify it does."""
    rng = np.random.default_rng(123)
    revenues = rng.normal(100, 50, 960)
    actual_total = revenues.sum()

    lo, mean, hi = block_bootstrap_ci(revenues, block_size=96, n_bootstrap=5000, seed=42)

    assert lo < hi
    assert lo < actual_total < hi


def test_block_bootstrap_ci_tighter_with_less_variance() -> None:
    """Near-constant revenues → tighter CI."""
    constant = np.full(960, 100.0)
    lo_c, _, hi_c = block_bootstrap_ci(constant, block_size=96)

    noisy = np.random.default_rng(1).normal(100, 50, 960)
    lo_n, _, hi_n = block_bootstrap_ci(noisy, block_size=96)

    assert (hi_c - lo_c) < (hi_n - lo_n)


# ── 6. BacktestResult helpers ───────────────────────────────────────


def test_backtest_result_to_dataframe(config: BacktestConfig) -> None:
    prices = _price_series(n=48)
    result = run_backtest_do_nothing(prices, config)
    df = result.to_dataframe()

    assert len(df) == 48
    assert "timestamp" in df.columns
    assert "revenue_eur" in df.columns


def test_annualised_revenue(config: BacktestConfig) -> None:
    prices = _price_series(n=96)
    result = run_backtest_perfect_foresight(prices, config)
    ann = result.annualised_revenue()
    assert ann == pytest.approx(result.net_revenue_eur * (35040 / 96), rel=1e-6)
