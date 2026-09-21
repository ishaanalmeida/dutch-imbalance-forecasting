"""Battery dispatch optimisation against the imbalance price.

CLAUDE.md §5: rolling-horizon optimisation with cvxpy. Three dispatch
policies (deterministic, risk-aware CVaR, perfect foresight) plus naive
baselines (do-nothing, day-ahead arbitrage).

The battery settles at the imbalance price: discharging when short of system
balance earns price_short per MWh; charging when long pays price_long per
MWh. Under single pricing (regulation states 0, +1, -1) the two are equal.
Under dual pricing (state 2) a battery that moves against the system
(e.g. charging during an upward-regulation ISP) pays a penalty price.

For the backtest, we simplify: the battery's position is small relative to
the system, so we treat price_short as the settlement price for both
directions. This is the conservative assumption — dual pricing only makes
the battery better off when it moves with the system, and our model does
not predict regulation state well enough to reliably exploit that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cvxpy as cp
import numpy as np

from src.models.base import FloatArray


@dataclass(frozen=True)
class BatteryParams:
    """Physical and economic parameters for a grid-scale BESS."""

    power_mw: float = 10.0
    energy_mwh: float = 40.0
    efficiency_charge: float = 0.95
    efficiency_discharge: float = 0.95
    soc_min: float = 0.05
    soc_max: float = 0.95
    soc_initial: float = 0.50
    soc_target: float = 0.50
    degradation_eur_per_mwh: float = 5.0
    terminal_penalty_eur_per_mwh: float = 50.0
    isp_hours: float = 0.25


@dataclass
class DispatchResult:
    """Output of one optimisation window."""

    charge_mw: FloatArray
    discharge_mw: FloatArray
    soc_mwh: FloatArray
    revenue_eur: float
    degradation_cost_eur: float
    net_revenue_eur: float
    status: str
    n_periods: int = field(init=False)

    def __post_init__(self) -> None:
        self.n_periods = len(self.charge_mw)


def dispatch_deterministic(
    prices: FloatArray,
    params: BatteryParams,
) -> DispatchResult:
    """Optimise against a single price scenario (e.g. the median forecast).

    This is the simplest policy: treat the point forecast as certain and
    maximise expected revenue. Equivalent to perfect foresight when the
    forecast is perfect.
    """
    return _solve_lp(prices, params)


def dispatch_perfect_foresight(
    realised_prices: FloatArray,
    params: BatteryParams,
) -> DispatchResult:
    """The upper bound: optimise against realised prices.

    Not achievable in practice, but essential context — it tells you what
    fraction of the theoretical maximum your forecast captures (CLAUDE.md §5).
    """
    return _solve_lp(realised_prices, params)


def dispatch_cvar(
    price_scenarios: FloatArray,
    params: BatteryParams,
    alpha: float = 0.05,
    risk_aversion: float = 0.5,
) -> DispatchResult:
    """Risk-aware dispatch using CVaR.

    Maximises (1 - risk_aversion) * E[revenue] + risk_aversion * CVaR_alpha.

    price_scenarios: shape (n_scenarios, n_periods). Each row is a price
    path sampled from the forecast distribution.

    alpha: CVaR tail probability (0.05 = worst 5% of scenarios).
    risk_aversion: 0 = risk-neutral (pure expected value), 1 = pure CVaR.
    """
    n_scenarios, T = price_scenarios.shape
    dt = params.isp_hours
    E_max = params.energy_mwh
    P_max = params.power_mw

    charge = cp.Variable(T, nonneg=True)
    discharge = cp.Variable(T, nonneg=True)
    soc = cp.Variable(T + 1, nonneg=True)

    # CVaR auxiliary variables
    zeta = cp.Variable()
    shortfall = cp.Variable(n_scenarios, nonneg=True)

    constraints: list[Any] = [
        charge <= P_max,
        discharge <= P_max,
        soc >= params.soc_min * E_max,
        soc <= params.soc_max * E_max,
        soc[0] == params.soc_initial * E_max,
    ]

    for t in range(T):
        constraints.append(
            soc[t + 1] == soc[t]
            + params.efficiency_charge * charge[t] * dt
            - (1.0 / params.efficiency_discharge) * discharge[t] * dt
        )

    throughput = cp.sum(charge + discharge) * dt
    degradation = params.degradation_eur_per_mwh * throughput
    terminal_dev = cp.abs(soc[T] - params.soc_target * E_max)
    terminal_cost = params.terminal_penalty_eur_per_mwh * terminal_dev

    # Per-scenario revenue
    scenario_revenues = []
    for s in range(n_scenarios):
        p = price_scenarios[s]
        rev = cp.sum(cp.multiply(p, discharge - charge)) * dt
        scenario_revenues.append(rev)
        constraints.append(shortfall[s] >= zeta - rev)

    expected_rev = (1.0 / n_scenarios) * cp.sum(cp.hstack(scenario_revenues))
    cvar = zeta - (1.0 / (alpha * n_scenarios)) * cp.sum(shortfall)

    objective = cp.Maximize(
        (1.0 - risk_aversion) * expected_rev
        + risk_aversion * cvar
        - degradation
        - terminal_cost
    )

    prob = cp.Problem(objective, constraints)
    prob.solve(solver=cp.CLARABEL, warm_start=True)

    if prob.status not in ("optimal", "optimal_inaccurate"):
        return DispatchResult(
            charge_mw=np.zeros(T),
            discharge_mw=np.zeros(T),
            soc_mwh=np.full(T + 1, params.soc_initial * E_max),
            revenue_eur=0.0,
            degradation_cost_eur=0.0,
            net_revenue_eur=0.0,
            status=prob.status or "unknown",
        )

    c_val = np.asarray(charge.value, dtype=float).flatten()
    d_val = np.asarray(discharge.value, dtype=float).flatten()
    soc_val = np.asarray(soc.value, dtype=float).flatten()

    mean_prices = price_scenarios.mean(axis=0)
    gross = float(np.sum((d_val - c_val) * mean_prices) * dt)
    deg = float(np.sum(c_val + d_val) * dt * params.degradation_eur_per_mwh)

    return DispatchResult(
        charge_mw=c_val,
        discharge_mw=d_val,
        soc_mwh=soc_val,
        revenue_eur=gross,
        degradation_cost_eur=deg,
        net_revenue_eur=gross - deg,
        status=prob.status or "optimal",
    )


def dispatch_do_nothing(
    n_periods: int,
    params: BatteryParams,
) -> DispatchResult:
    """Baseline: hold position. Revenue = 0."""
    return DispatchResult(
        charge_mw=np.zeros(n_periods),
        discharge_mw=np.zeros(n_periods),
        soc_mwh=np.full(n_periods + 1, params.soc_initial * params.energy_mwh),
        revenue_eur=0.0,
        degradation_cost_eur=0.0,
        net_revenue_eur=0.0,
        status="trivial",
    )


def _solve_lp(
    prices: FloatArray,
    params: BatteryParams,
) -> DispatchResult:
    """Core LP for deterministic and perfect-foresight policies."""
    T = len(prices)
    dt = params.isp_hours
    E_max = params.energy_mwh
    P_max = params.power_mw

    charge = cp.Variable(T, nonneg=True)
    discharge = cp.Variable(T, nonneg=True)
    soc = cp.Variable(T + 1, nonneg=True)

    constraints: list[Any] = [
        charge <= P_max,
        discharge <= P_max,
        soc >= params.soc_min * E_max,
        soc <= params.soc_max * E_max,
        soc[0] == params.soc_initial * E_max,
    ]

    for t in range(T):
        constraints.append(
            soc[t + 1] == soc[t]
            + params.efficiency_charge * charge[t] * dt
            - (1.0 / params.efficiency_discharge) * discharge[t] * dt
        )

    revenue = cp.sum(cp.multiply(prices, discharge - charge)) * dt
    throughput = cp.sum(charge + discharge) * dt
    degradation = params.degradation_eur_per_mwh * throughput
    terminal_dev = cp.abs(soc[T] - params.soc_target * E_max)
    terminal_cost = params.terminal_penalty_eur_per_mwh * terminal_dev

    objective = cp.Maximize(revenue - degradation - terminal_cost)
    prob = cp.Problem(objective, constraints)
    prob.solve(solver=cp.CLARABEL, warm_start=True)

    if prob.status not in ("optimal", "optimal_inaccurate"):
        return DispatchResult(
            charge_mw=np.zeros(T),
            discharge_mw=np.zeros(T),
            soc_mwh=np.full(T + 1, params.soc_initial * E_max),
            revenue_eur=0.0,
            degradation_cost_eur=0.0,
            net_revenue_eur=0.0,
            status=prob.status or "unknown",
        )

    c_val = np.asarray(charge.value, dtype=float).flatten()
    d_val = np.asarray(discharge.value, dtype=float).flatten()
    soc_val = np.asarray(soc.value, dtype=float).flatten()

    gross = float(np.sum((d_val - c_val) * prices) * dt)
    deg = float(np.sum(c_val + d_val) * dt * params.degradation_eur_per_mwh)

    return DispatchResult(
        charge_mw=c_val,
        discharge_mw=d_val,
        soc_mwh=soc_val,
        revenue_eur=gross,
        degradation_cost_eur=deg,
        net_revenue_eur=gross - deg,
        status=prob.status or "optimal",
    )
