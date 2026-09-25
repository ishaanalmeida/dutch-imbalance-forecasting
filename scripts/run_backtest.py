"""Phase 4 backtest: walk-forward GBM forecasts → rolling-horizon dispatch
→ settlement against realised prices (CLAUDE.md §6).

Produces:
  - Per-policy revenue (deterministic, CVaR, perfect-foresight, do-nothing)
  - Ratio to perfect foresight
  - Block-bootstrap confidence intervals
  - Per-year revenue breakdown
  - Efficient frontier: expected revenue vs CVaR across risk-aversion settings
  - Revenue-per-MW saturation curve with square-root market impact
"""

from __future__ import annotations

# ruff: noqa: I001 — cvxpy MUST load before pandas (Windows osqp DLL conflict)
import cvxpy as cp  # noqa: F401
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    ISPResult,
    block_bootstrap_ci,
    run_backtest_do_nothing,
    run_backtest_perfect_foresight,
)
from src.data import cache
from src.evaluation.metrics import QUANTILES
from src.evaluation.walkforward import generate_folds
from src.features.builder import build_features
from src.features.targets import HOLDOUT_START, PICASSO_START, build_targets
from src.models.base import FloatArray
from src.models.gbm import QuantileGBM
from src.optimisation.battery import (
    BatteryParams,
    DispatchResult,
    dispatch_cvar,
    dispatch_deterministic,
)

RESULTS_DIR = Path("work/backtest")


def _resample_day_ahead(
    day_ahead_raw: pd.Series[float], isp_index: pd.DatetimeIndex
) -> pd.Series[float]:
    return day_ahead_raw.reindex(isp_index, method="ffill")


def _slice(
    frame: pd.DataFrame | pd.Series[float],
    start: datetime,
    end: datetime,
) -> pd.DataFrame | pd.Series[float]:
    return frame[(frame.index >= start) & (frame.index < end)]


# ── Scenario generation ──────────────────────────────────────────────


def generate_scenarios(
    q_pred: FloatArray,
    quantiles: tuple[float, ...],
    n_scenarios: int,
    rng: np.random.Generator,
) -> FloatArray:
    """Stratified scenario generation from quantile forecasts.

    CLAUDE.md §5: "preserving temporal correlation ... do not sample
    quantiles independently across time."

    Each scenario samples at a consistent quantile LEVEL across all ISPs
    (stratified: evenly spaced from low to high), with small per-ISP
    jitter (std=0.03) so scenarios aren't perfectly deterministic.
    Temporal correlation comes from the forecast shape itself — a
    scenario at the 20th percentile follows the forecast's temporal
    pattern at that level, preserving the directional signal the
    optimizer needs to find charge/discharge patterns.

    ADR-031: the original Schaake shuffle sampled independent quantile
    levels per ISP, which destroyed the forecast signal and made the
    CVaR policy lose money even at risk_aversion=0. Stratified sampling
    fixes this by keeping the forecast shape within each scenario.
    """
    T, n_q = q_pred.shape
    taus = np.asarray(quantiles)
    scenarios = np.empty((n_scenarios, T))

    for i in range(n_scenarios):
        u_base = (i + 0.5) / n_scenarios
        u_per_isp = np.clip(
            u_base + rng.normal(0, 0.03, T),
            0.01,
            0.99,
        )
        for t in range(T):
            scenarios[i, t] = np.interp(u_per_isp[t], taus, q_pred[t])

    return scenarios


# ── Rolling-horizon dispatch ─────────────────────────────────────────


def rolling_dispatch_deterministic(
    prices: FloatArray,
    median_forecast: FloatArray,
    params: BatteryParams,
    window: int = 32,
    step: int = 16,
) -> DispatchResult:
    """Rolling-horizon deterministic dispatch.

    At each step: optimise over `window` ISPs, execute `step` ISPs, carry
    SoC forward. More realistic than full-horizon dispatch because it
    mirrors the finite forecast horizon a real operator faces.
    """
    T = len(prices)
    charge_all = np.zeros(T)
    discharge_all = np.zeros(T)
    soc_all = np.zeros(T + 1)
    soc_all[0] = params.soc_initial * params.energy_mwh
    dt = params.isp_hours
    total_throughput = 0.0

    t = 0
    while t < T:
        end = min(t + window, T)
        n_window = end - t

        current_params = BatteryParams(
            power_mw=params.power_mw,
            energy_mwh=params.energy_mwh,
            efficiency_charge=params.efficiency_charge,
            efficiency_discharge=params.efficiency_discharge,
            soc_min=params.soc_min,
            soc_max=params.soc_max,
            soc_initial=soc_all[t] / params.energy_mwh,
            soc_target=params.soc_target,
            degradation_eur_per_mwh=params.degradation_eur_per_mwh,
            terminal_penalty_eur_per_mwh=params.terminal_penalty_eur_per_mwh,
            isp_hours=params.isp_hours,
        )

        result = dispatch_deterministic(median_forecast[t:end], current_params)
        execute = min(step, n_window)

        charge_all[t : t + execute] = result.charge_mw[:execute]
        discharge_all[t : t + execute] = result.discharge_mw[:execute]

        for k in range(execute):
            soc_all[t + k + 1] = (
                soc_all[t + k]
                + params.efficiency_charge * charge_all[t + k] * dt
                - (1.0 / params.efficiency_discharge) * discharge_all[t + k] * dt
            )
            total_throughput += (charge_all[t + k] + discharge_all[t + k]) * dt

        t += execute

    net_pos = (discharge_all - charge_all) * dt
    gross = float(np.sum(net_pos * prices))
    deg = total_throughput * params.degradation_eur_per_mwh

    return DispatchResult(
        charge_mw=charge_all,
        discharge_mw=discharge_all,
        soc_mwh=soc_all,
        revenue_eur=gross,
        degradation_cost_eur=deg,
        net_revenue_eur=gross - deg,
        status="rolling_optimal",
    )


def rolling_dispatch_cvar(
    prices: FloatArray,
    q_pred: FloatArray,
    quantiles: tuple[float, ...],
    params: BatteryParams,
    risk_aversion: float = 0.5,
    alpha: float = 0.05,
    n_scenarios: int = 20,
    window: int = 32,
    step: int = 16,
    seed: int = 42,
) -> DispatchResult:
    """Rolling-horizon CVaR dispatch with Schaake-shuffled scenarios."""
    T = len(prices)
    rng = np.random.default_rng(seed)
    charge_all = np.zeros(T)
    discharge_all = np.zeros(T)
    soc_all = np.zeros(T + 1)
    soc_all[0] = params.soc_initial * params.energy_mwh
    dt = params.isp_hours
    total_throughput = 0.0

    t = 0
    while t < T:
        end = min(t + window, T)
        n_window = end - t

        current_params = BatteryParams(
            power_mw=params.power_mw,
            energy_mwh=params.energy_mwh,
            efficiency_charge=params.efficiency_charge,
            efficiency_discharge=params.efficiency_discharge,
            soc_min=params.soc_min,
            soc_max=params.soc_max,
            soc_initial=soc_all[t] / params.energy_mwh,
            soc_target=params.soc_target,
            degradation_eur_per_mwh=params.degradation_eur_per_mwh,
            terminal_penalty_eur_per_mwh=params.terminal_penalty_eur_per_mwh,
            isp_hours=params.isp_hours,
        )

        q_window = q_pred[t:end]
        scenarios = generate_scenarios(q_window, quantiles, n_scenarios, rng)
        result = dispatch_cvar(scenarios, current_params, alpha=alpha, risk_aversion=risk_aversion)
        execute = min(step, n_window)

        charge_all[t : t + execute] = result.charge_mw[:execute]
        discharge_all[t : t + execute] = result.discharge_mw[:execute]

        for k in range(execute):
            soc_all[t + k + 1] = (
                soc_all[t + k]
                + params.efficiency_charge * charge_all[t + k] * dt
                - (1.0 / params.efficiency_discharge) * discharge_all[t + k] * dt
            )
            total_throughput += (charge_all[t + k] + discharge_all[t + k]) * dt

        t += execute

    net_pos = (discharge_all - charge_all) * dt
    gross = float(np.sum(net_pos * prices))
    deg = total_throughput * params.degradation_eur_per_mwh

    return DispatchResult(
        charge_mw=charge_all,
        discharge_mw=discharge_all,
        soc_mwh=soc_all,
        revenue_eur=gross,
        degradation_cost_eur=deg,
        net_revenue_eur=gross - deg,
        status="rolling_cvar",
    )


# ── Market impact ────────────────────────────────────────────────────


def apply_market_impact(
    revenues_per_isp: FloatArray,
    net_positions: FloatArray,
    balance_deltas: FloatArray | None,
    capacity_mw: float,
    ref_capacity_mw: float = 500.0,
) -> FloatArray:
    """Square-root market impact model (CLAUDE.md §6).

    A public-signal strategy degrades as capacity scales because acting
    on the imbalance signal pushes the system back toward balance.

    impact_factor = sqrt(capacity_mw / ref_capacity_mw)
    adjusted_revenue = revenue * max(0, 1 - impact_factor)

    ref_capacity_mw is calibrated so that at 500 MW deployed, the signal
    is fully absorbed. This is a modelling choice, not a measurement.
    """
    impact = np.sqrt(capacity_mw / ref_capacity_mw)
    return np.asarray(revenues_per_isp * max(0.0, 1.0 - impact))


# ── Build ISPResult list ─────────────────────────────────────────────


def _to_isp_results(
    prices_series: pd.Series[float],
    dispatch: DispatchResult,
    dt: float,
) -> list[ISPResult]:
    T = len(prices_series)
    prices_arr = prices_series.to_numpy(dtype=float)
    results = []
    for t in range(T):
        net = (dispatch.discharge_mw[t] - dispatch.charge_mw[t]) * dt
        rev = net * prices_arr[t]
        ts = prices_series.index[t]
        results.append(
            ISPResult(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                price_short=prices_arr[t],
                charge_mw=dispatch.charge_mw[t],
                discharge_mw=dispatch.discharge_mw[t],
                soc_mwh=dispatch.soc_mwh[t],
                net_position_mwh=net,
                revenue_eur=rev,
            )
        )
    return results


def _dispatch_to_backtest_result(
    prices_series: pd.Series[float],
    dispatch: DispatchResult,
    policy: str,
    dt: float,
) -> BacktestResult:
    isps = _to_isp_results(prices_series, dispatch, dt)
    rev_arr = np.array([r.revenue_eur for r in isps])
    total_rev = float(rev_arr.sum())
    return BacktestResult(
        isps=isps,
        total_revenue_eur=total_rev,
        total_degradation_eur=dispatch.degradation_cost_eur,
        net_revenue_eur=total_rev - dispatch.degradation_cost_eur,
        n_periods=len(isps),
        policy=policy,
    )


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    battery = BatteryParams()
    config = BacktestConfig(battery=battery)

    # Load data (same pipeline as walk-forward eval)
    prices = cache.read_frame("imbalance_prices", PICASSO_START, HOLDOUT_START)
    day_ahead_cached = cache.read_frame("day_ahead_price", PICASSO_START, HOLDOUT_START)
    day_ahead_raw = day_ahead_cached["day_ahead_price"]
    day_ahead = _resample_day_ahead(day_ahead_raw, pd.DatetimeIndex(prices.index))

    X = build_features(prices, day_ahead=day_ahead)
    targets = build_targets(prices)
    y = targets["price_short"]

    folds = generate_folds(
        datetime(2024, 11, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    print(f"Backtest: {len(folds)} walk-forward folds\n")

    # ── Walk-forward: train GBM, forecast, dispatch, settle ──────────
    all_prices: list[pd.Series[float]] = []
    all_forecasts_median: list[FloatArray] = []
    all_forecasts_quantile: list[FloatArray] = []
    all_idx: list[pd.DatetimeIndex] = []

    for fold in folds:
        X_train = X[(X.index >= fold.train_start) & (X.index < fold.train_end)]
        y_train = y[(y.index >= fold.train_start) & (y.index < fold.train_end)]
        X_test = X[(X.index >= fold.test_start) & (X.index < fold.test_end)]
        y_test = y[(y.index >= fold.test_start) & (y.index < fold.test_end)]

        model = QuantileGBM()
        model.fit(X_train, y_train)
        q_pred = model.predict_quantiles(X_test, QUANTILES)

        median_idx = QUANTILES.index(0.5)
        median_pred = q_pred[:, median_idx]

        all_prices.append(y_test)
        all_forecasts_median.append(median_pred)
        all_forecasts_quantile.append(q_pred)
        all_idx.append(pd.DatetimeIndex(X_test.index))
        print(f"  {fold.label} done (train={len(X_train)}, test={len(X_test)})")

    # Concatenate all test periods
    prices_concat = pd.concat(all_prices)
    median_concat = np.concatenate(all_forecasts_median)
    q_concat = np.concatenate(all_forecasts_quantile)
    prices_arr = prices_concat.to_numpy(dtype=float)
    T = len(prices_arr)
    dt = battery.isp_hours

    print(f"\nTotal test ISPs: {T}")

    # ── Policy 1: Perfect foresight (upper bound) ────────────────────
    print("\nRunning perfect foresight...")
    pf_result = run_backtest_perfect_foresight(prices_concat, config)
    print(f"  Net revenue: EUR {pf_result.net_revenue_eur:,.0f}")

    # ── Policy 2: Deterministic (median forecast) ────────────────────
    print("\nRunning deterministic (rolling horizon)...")
    det_dispatch = rolling_dispatch_deterministic(
        prices_arr,
        median_concat,
        battery,
        window=32,
        step=16,
    )
    det_result = _dispatch_to_backtest_result(prices_concat, det_dispatch, "deterministic", dt)
    print(f"  Net revenue: EUR {det_result.net_revenue_eur:,.0f}")

    # ── Policy 3: CVaR (risk_aversion=0.5) ───────────────────────────
    print("\nRunning CVaR (risk_aversion=0.5, rolling horizon)...")
    cvar_dispatch = rolling_dispatch_cvar(
        prices_arr,
        q_concat,
        QUANTILES,
        battery,
        risk_aversion=0.5,
        n_scenarios=20,
        window=32,
        step=16,
    )
    cvar_result = _dispatch_to_backtest_result(prices_concat, cvar_dispatch, "cvar_0.5", dt)
    print(f"  Net revenue: EUR {cvar_result.net_revenue_eur:,.0f}")

    # ── Policy 4: Do nothing ─────────────────────────────────────────
    dn_result = run_backtest_do_nothing(prices_concat, config)

    # ── Summary ──────────────────────────────────────────────────────
    policies = {
        "perfect_foresight": pf_result,
        "deterministic": det_result,
        "cvar_0.5": cvar_result,
        "do_nothing": dn_result,
    }

    print("\n=== Revenue summary (EUR, 10 MW / 40 MWh battery) ===")
    print(f"{'Policy':<22s} {'Net Revenue':>14s} {'Ratio to PF':>14s}")
    for name, r in policies.items():
        pf_rev = pf_result.net_revenue_eur
        ratio = r.net_revenue_eur / pf_rev if pf_rev != 0 else 0.0
        print(f"{name:<22s} {r.net_revenue_eur:>14,.0f} {ratio:>14.1%}")

    # ── Per-year revenue ─────────────────────────────────────────────
    print("\n=== Revenue by year ===")
    idx_concat = pd.DatetimeIndex(np.concatenate([i.values for i in all_idx]))
    if all_idx and all_idx[0].tz is not None:
        idx_concat = idx_concat.tz_localize(all_idx[0].tz)

    yearly: dict[str, dict[str, float]] = {}
    for name, r in policies.items():
        df = r.to_dataframe()
        df.index = idx_concat[: len(df)]
        yearly[name] = {}
        for year_val in sorted(df.index.year.unique()):
            mask = df.index.year == year_val
            rev = float(df.loc[mask, "revenue_eur"].sum())
            yearly[name][str(year_val)] = rev
            n_isps = int(mask.sum())
            ann = rev * (35040 / n_isps) if n_isps > 0 else 0
            print(f"  {name:<22s} {year_val}: EUR {rev:>10,.0f}  (ann. EUR {ann:>10,.0f})")

    # ── Block bootstrap CIs ──────────────────────────────────────────
    print("\n=== Block bootstrap 95% CIs (net revenue, EUR) ===")
    ci_results: dict[str, dict[str, float]] = {}
    for name, r in policies.items():
        if name == "do_nothing":
            continue
        rev_per_isp = np.array([isp.revenue_eur for isp in r.isps])
        rev_per_isp_net = rev_per_isp.copy()
        if r.total_degradation_eur > 0 and len(rev_per_isp) > 0:
            rev_per_isp_net -= r.total_degradation_eur / len(rev_per_isp)
        lo, mean, hi = block_bootstrap_ci(rev_per_isp_net, block_size=96, n_bootstrap=5000)
        ci_results[name] = {"lo": lo, "mean": mean, "hi": hi}
        print(f"  {name:<22s} [{lo:>12,.0f}, {hi:>12,.0f}]  mean={mean:>12,.0f}")

    # ── Ratio to perfect foresight ───────────────────────────────────
    ratio_to_pf: dict[str, float] = {}
    pf_net = pf_result.net_revenue_eur
    for name, r in policies.items():
        ratio_to_pf[name] = r.net_revenue_eur / pf_net if pf_net != 0 else 0.0

    # ── Efficient frontier (sweep risk_aversion) ─────────────────────
    print("\n=== Efficient frontier: sweep risk_aversion ===")
    frontier: list[dict[str, float]] = []
    risk_aversions = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

    # Use last 3 months of walk-forward data for the sweep (faster)
    n_frontier = min(T, 96 * 90)
    frontier_prices = prices_arr[-n_frontier:]
    frontier_q = q_concat[-n_frontier:]
    print(f"  (using last {n_frontier} ISPs for frontier sweep)")

    for ra in risk_aversions:
        disp = rolling_dispatch_cvar(
            frontier_prices,
            frontier_q,
            QUANTILES,
            battery,
            risk_aversion=ra,
            n_scenarios=20,
            window=32,
            step=16,
            seed=42,
        )
        net_pos = (disp.discharge_mw - disp.charge_mw) * dt
        rev_per_isp = net_pos * frontier_prices
        total_rev = float(rev_per_isp.sum()) - disp.degradation_cost_eur
        cvar_05 = float(np.percentile(rev_per_isp, 5))
        std_rev = float(rev_per_isp.std())
        frontier.append(
            {
                "risk_aversion": ra,
                "net_revenue": total_rev,
                "cvar_5pct": cvar_05,
                "std_daily_revenue": std_rev * np.sqrt(96),
            }
        )
        print(f"  ra={ra:.1f}  net_rev={total_rev:>10,.0f}  CVaR5={cvar_05:>8,.1f}")

    # ── Revenue-per-MW saturation curve ──────────────────────────────
    print("\n=== Revenue-per-MW saturation curve (sqrt market impact) ===")
    det_rev_per_isp = np.array([isp.revenue_eur for isp in det_result.isps])
    det_net_per_isp = np.array([isp.net_position_mwh for isp in det_result.isps])
    saturation: list[dict[str, float]] = []
    capacities = [1, 2, 5, 10, 20, 50, 100, 200, 500]

    for cap in capacities:
        impacted = apply_market_impact(
            det_rev_per_isp,
            det_net_per_isp,
            None,
            cap,
            ref_capacity_mw=500.0,
        )
        scale = cap / battery.power_mw
        total_impacted = float(impacted.sum()) * scale
        deg_scaled = det_result.total_degradation_eur * scale
        net = total_impacted - deg_scaled
        rev_per_mw = net / cap if cap > 0 else 0
        saturation.append(
            {
                "capacity_mw": float(cap),
                "total_net_revenue": net,
                "revenue_per_mw": rev_per_mw,
            }
        )
        print(f"  {cap:>4d} MW:  EUR {net:>12,.0f}  ({rev_per_mw:>8,.0f}/MW)")

    # ── Save results ─────────────────────────────────────────────────
    results = {
        "battery": {
            "power_mw": battery.power_mw,
            "energy_mwh": battery.energy_mwh,
            "efficiency_charge": battery.efficiency_charge,
            "efficiency_discharge": battery.efficiency_discharge,
            "degradation_eur_per_mwh": battery.degradation_eur_per_mwh,
        },
        "n_folds": len(folds),
        "n_test_isps": T,
        "policies": {
            name: {
                "net_revenue_eur": r.net_revenue_eur,
                "total_revenue_eur": r.total_revenue_eur,
                "degradation_eur": r.total_degradation_eur,
                "ratio_to_pf": ratio_to_pf[name],
            }
            for name, r in policies.items()
        },
        "yearly_revenue": yearly,
        "bootstrap_ci_95": ci_results,
        "efficient_frontier": frontier,
        "saturation_curve": saturation,
        "market_impact_model": "sqrt(capacity / 500)",
        "dispatch_config": {
            "window_isps": 32,
            "step_isps": 16,
            "cvar_n_scenarios": 20,
            "cvar_alpha": 0.05,
            "scenario_method": "stratified_quantile",
        },
    }

    out = RESULTS_DIR / "backtest_results.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
