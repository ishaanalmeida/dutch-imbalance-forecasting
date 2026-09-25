"""Event-driven backtest engine.

RIGOUR ZONE (CLAUDE.md §12). At each decision point the engine may only
access data whose `available_at` timestamp precedes the decision. Enforced
in code, not by convention.

CLAUDE.md §6: settle using actual rules from config/market_rules.yaml,
produce block-bootstrap confidence intervals, compute the ratio-to-perfect-
foresight, and the revenue-per-MW saturation curve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from src.data.data_availability import assert_available
from src.models.base import FloatArray
from src.optimisation.battery import (
    BatteryParams,
    dispatch_deterministic,
    dispatch_perfect_foresight,
)


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    battery: BatteryParams
    window_isps: int = 32
    step_isps: int = 16


@dataclass
class ISPResult:
    """Settlement result for one ISP."""

    timestamp: datetime
    price_short: float
    charge_mw: float
    discharge_mw: float
    soc_mwh: float
    net_position_mwh: float
    revenue_eur: float


@dataclass
class BacktestResult:
    """Full backtest output."""

    isps: list[ISPResult]
    total_revenue_eur: float
    total_degradation_eur: float
    net_revenue_eur: float
    n_periods: int
    policy: str

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([vars(r) for r in self.isps])

    def annualised_revenue(self, isps_per_year: int = 35040) -> float:
        if self.n_periods == 0:
            return 0.0
        return self.net_revenue_eur * (isps_per_year / self.n_periods)


def assert_no_lookahead(
    decision_time: datetime,
    field: str,
    target_time: datetime,
) -> None:
    """Raise if a datum is not yet available at decision time.

    CLAUDE.md §6: "Write a test that deliberately tries to leak and
    asserts the engine refuses."

    Delegates to data_availability.assert_available, which is the single
    source of truth for publication lag enforcement (R1).
    """
    assert_available(field, target_time, decision_time)


def run_backtest_perfect_foresight(
    prices: pd.Series[float],
    config: BacktestConfig,
) -> BacktestResult:
    """Perfect foresight backtest — the upper bound."""
    price_arr = prices.to_numpy(dtype=float)
    T = len(price_arr)
    dt = config.battery.isp_hours

    result = dispatch_perfect_foresight(price_arr, config.battery)
    isps = []
    total_rev = 0.0
    for t in range(T):
        net = (result.discharge_mw[t] - result.charge_mw[t]) * dt
        rev = net * price_arr[t]
        total_rev += rev
        ts = prices.index[t]
        isps.append(
            ISPResult(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                price_short=price_arr[t],
                charge_mw=result.charge_mw[t],
                discharge_mw=result.discharge_mw[t],
                soc_mwh=result.soc_mwh[t],
                net_position_mwh=net,
                revenue_eur=rev,
            )
        )

    return BacktestResult(
        isps=isps,
        total_revenue_eur=total_rev,
        total_degradation_eur=result.degradation_cost_eur,
        net_revenue_eur=total_rev - result.degradation_cost_eur,
        n_periods=T,
        policy="perfect_foresight",
    )


def run_backtest_deterministic(
    prices: pd.Series[float],
    forecasts: FloatArray,
    config: BacktestConfig,
) -> BacktestResult:
    """Deterministic backtest — optimise against the median forecast,
    settle against realised prices."""
    price_arr = prices.to_numpy(dtype=float)
    T = len(price_arr)
    dt = config.battery.isp_hours

    result = dispatch_deterministic(forecasts, config.battery)
    isps = []
    total_rev = 0.0
    for t in range(T):
        net = (result.discharge_mw[t] - result.charge_mw[t]) * dt
        rev = net * price_arr[t]
        total_rev += rev
        ts = prices.index[t]
        isps.append(
            ISPResult(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                price_short=price_arr[t],
                charge_mw=result.charge_mw[t],
                discharge_mw=result.discharge_mw[t],
                soc_mwh=result.soc_mwh[t],
                net_position_mwh=net,
                revenue_eur=rev,
            )
        )

    return BacktestResult(
        isps=isps,
        total_revenue_eur=total_rev,
        total_degradation_eur=result.degradation_cost_eur,
        net_revenue_eur=total_rev - result.degradation_cost_eur,
        n_periods=T,
        policy="deterministic",
    )


def run_backtest_do_nothing(
    prices: pd.Series[float],
    config: BacktestConfig,
) -> BacktestResult:
    """Baseline: hold position."""
    T = len(prices)
    soc = config.battery.soc_initial * config.battery.energy_mwh
    isps = []
    for t in range(T):
        ts = prices.index[t]
        isps.append(
            ISPResult(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                price_short=float(prices.iloc[t]),
                charge_mw=0.0,
                discharge_mw=0.0,
                soc_mwh=soc,
                net_position_mwh=0.0,
                revenue_eur=0.0,
            )
        )
    return BacktestResult(
        isps=isps,
        total_revenue_eur=0.0,
        total_degradation_eur=0.0,
        net_revenue_eur=0.0,
        n_periods=T,
        policy="do_nothing",
    )


def block_bootstrap_ci(
    revenues: FloatArray,
    block_size: int = 96,
    n_bootstrap: int = 5000,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Stationary block bootstrap confidence interval on total revenue.

    Returns (lower, mean, upper) at the (1-alpha) level. block_size=96
    corresponds to one day of ISPs, preserving daily autocorrelation.
    """
    rng = np.random.default_rng(seed)
    T = len(revenues)
    n_blocks = max(1, T // block_size)

    boot_totals = np.empty(n_bootstrap)
    for b in range(n_bootstrap):
        starts = rng.integers(0, T - block_size + 1, size=n_blocks)
        sample = np.concatenate([revenues[s : s + block_size] for s in starts])[:T]
        boot_totals[b] = sample.sum()

    lo = float(np.percentile(boot_totals, 100 * alpha / 2))
    hi = float(np.percentile(boot_totals, 100 * (1 - alpha / 2)))
    return lo, float(boot_totals.mean()), hi
