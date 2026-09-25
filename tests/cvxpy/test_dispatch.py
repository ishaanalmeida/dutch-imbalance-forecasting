"""Tests for the battery dispatch optimiser (CLAUDE.md §5).

Marked `cvxpy` because osqp and lightgbm have a DLL loading conflict on
Windows — importing both in the same process segfaults. Run these with
`pytest -m cvxpy` in a separate invocation, or exclude them from the
default run with `-m 'not cvxpy'`.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.optimisation.battery import (
    BatteryParams,
    dispatch_cvar,
    dispatch_deterministic,
    dispatch_do_nothing,
    dispatch_perfect_foresight,
)


@pytest.fixture
def params() -> BatteryParams:
    return BatteryParams(power_mw=10.0, energy_mwh=40.0)


def test_perfect_foresight_earns_positive_on_volatile_prices(
    params: BatteryParams,
) -> None:
    """A battery with perfect foresight on a volatile price series must earn
    positive net revenue — if it can't, the optimiser is broken."""
    rng = np.random.default_rng(42)
    prices = 50.0 + 30.0 * np.sin(np.linspace(0, 4 * np.pi, 96)) + rng.normal(0, 5, 96)
    result = dispatch_perfect_foresight(prices, params)
    assert result.status == "optimal"
    assert result.net_revenue_eur > 0


def test_do_nothing_earns_zero(params: BatteryParams) -> None:
    result = dispatch_do_nothing(96, params)
    assert result.revenue_eur == 0.0
    assert result.net_revenue_eur == 0.0
    assert result.status == "trivial"


def test_perfect_foresight_dominates_deterministic(
    params: BatteryParams,
) -> None:
    """Perfect foresight is the upper bound (CLAUDE.md §5)."""
    rng = np.random.default_rng(1)
    prices = 50.0 + 20.0 * np.sin(np.linspace(0, 4 * np.pi, 96)) + rng.normal(0, 5, 96)
    noisy = prices + rng.normal(0, 10, 96)

    pf = dispatch_perfect_foresight(prices, params)
    det = dispatch_deterministic(noisy, params)
    # Evaluate deterministic against actual prices
    dt = params.isp_hours
    det_actual_rev = float(np.sum((det.discharge_mw - det.charge_mw) * prices) * dt)
    assert pf.revenue_eur >= det_actual_rev - 1e-6


def test_soc_stays_in_bounds(params: BatteryParams) -> None:
    rng = np.random.default_rng(2)
    prices = rng.normal(50, 30, 96)
    result = dispatch_perfect_foresight(prices, params)
    soc_min = params.soc_min * params.energy_mwh
    soc_max = params.soc_max * params.energy_mwh
    assert np.all(result.soc_mwh >= soc_min - 1e-6)
    assert np.all(result.soc_mwh <= soc_max + 1e-6)


def test_power_limits_respected(params: BatteryParams) -> None:
    prices = np.concatenate([np.full(48, 0.0), np.full(48, 200.0)])
    result = dispatch_perfect_foresight(prices, params)
    assert np.all(result.charge_mw <= params.power_mw + 1e-6)
    assert np.all(result.discharge_mw <= params.power_mw + 1e-6)


def test_degradation_cost_is_positive_when_cycling(
    params: BatteryParams,
) -> None:
    prices = np.concatenate([np.full(48, 0.0), np.full(48, 200.0)])
    result = dispatch_perfect_foresight(prices, params)
    assert result.degradation_cost_eur > 0


def test_cvar_dispatch_produces_valid_result(
    params: BatteryParams,
) -> None:
    rng = np.random.default_rng(3)
    n_scenarios, T = 20, 48
    scenarios = rng.normal(50, 20, (n_scenarios, T))
    result = dispatch_cvar(scenarios, params, alpha=0.05, risk_aversion=0.5)
    assert result.status in ("optimal", "optimal_inaccurate")
    assert len(result.charge_mw) == T
    assert len(result.soc_mwh) == T + 1


def test_flat_prices_yield_no_trading(params: BatteryParams) -> None:
    """With flat prices and degradation cost, the optimal action is to do
    nothing — trading costs throughput but earns nothing."""
    prices = np.full(96, 50.0)
    result = dispatch_deterministic(prices, params)
    total_throughput = np.sum(result.charge_mw + result.discharge_mw)
    assert total_throughput < 0.1
