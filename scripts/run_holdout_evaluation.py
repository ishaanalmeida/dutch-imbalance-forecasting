"""Final holdout evaluation — evaluated EXACTLY ONCE (CLAUDE.md R2).

Trains all models on data up to HOLDOUT_START (2026-05-01), evaluates on the
holdout period HOLDOUT_START to HOLDOUT_END (2026-05-01 to 2026-08-01).

Produces:
  - Forecast metrics (pinball loss, calibration, CRPS, MAE/RMSE) on holdout
  - Comparison to walk-forward metrics (degradation analysis)
  - Dispatch results (deterministic, CVaR, perfect foresight) on holdout
  - Bootstrap CIs on holdout revenue
"""

from __future__ import annotations

# ruff: noqa: I001 — cvxpy MUST load before pandas (Windows osqp DLL conflict)
import cvxpy as cp  # noqa: F401
import json
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
from src.evaluation.metrics import (
    QUANTILES,
    crps_from_quantiles,
    diebold_mariano,
    empirical_coverage,
    mae,
    mean_pinball,
    pinball_loss_per_obs,
    pit_values,
    rmse,
)
from src.features.builder import build_features
from src.features.targets import HOLDOUT_END, HOLDOUT_START, PICASSO_START, build_targets
from src.models.base import FloatArray, QuantileModel
from src.models.baselines import (
    ClimatologyBaseline,
    DayAheadBaseline,
    PersistenceBaseline,
    SeasonalNaiveBaseline,
)
from src.models.gbm import QuantileGBM
from src.models.lear import LEARModel
from src.optimisation.battery import (
    BatteryParams,
    DispatchResult,
    dispatch_cvar,
    dispatch_deterministic,
)

RESULTS_DIR = Path("work/holdout")

MODEL_FACTORIES: dict[str, type[QuantileModel] | object] = {
    "persistence": PersistenceBaseline,
    "seasonal_naive_1w": lambda: SeasonalNaiveBaseline(period_isps=672),
    "climatology": ClimatologyBaseline,
    "day_ahead": DayAheadBaseline,
    "lear": LEARModel,
    "gbm": QuantileGBM,
}


def _resample_day_ahead(
    day_ahead_raw: pd.Series[float], isp_index: pd.DatetimeIndex
) -> pd.Series[float]:
    return day_ahead_raw.reindex(isp_index, method="ffill")


def _calibration(y: FloatArray, pred: FloatArray) -> dict:
    coverage = empirical_coverage(y, pred, QUANTILES)
    taus = np.asarray(QUANTILES)
    cal_error = np.abs(coverage - taus)
    pit = pit_values(y, pred, QUANTILES)
    pit_hist, _ = np.histogram(pit, bins=10, range=(0.0, 1.0))
    pit_cv = float(pit_hist.std() / pit_hist.mean()) if pit_hist.mean() > 0 else float("inf")
    median_idx = list(QUANTILES).index(0.5)
    return {
        "mean_abs_cal_error": float(cal_error.mean()),
        "max_cal_error": float(cal_error.max()),
        "pit_cv": pit_cv,
        "crps": float(crps_from_quantiles(y, pred, QUANTILES)),
        "mae": mae(y, pred[:, median_idx]),
        "rmse": rmse(y, pred[:, median_idx]),
        "coverage": {f"{t:.2f}": float(c) for t, c in zip(taus, coverage, strict=True)},
        "pit_histogram": pit_hist.tolist(),
    }


# ── Scenario generation (same as backtest) ──────────────────────────


def generate_scenarios(
    q_pred: FloatArray, quantiles: tuple[float, ...],
    n_scenarios: int, rng: np.random.Generator,
) -> FloatArray:
    T, _ = q_pred.shape
    taus = np.asarray(quantiles)
    scenarios = np.empty((n_scenarios, T))
    for i in range(n_scenarios):
        u_base = (i + 0.5) / n_scenarios
        u_per_isp = np.clip(u_base + rng.normal(0, 0.03, T), 0.01, 0.99)
        for t in range(T):
            scenarios[i, t] = np.interp(u_per_isp[t], taus, q_pred[t])
    return scenarios


# ── Rolling dispatch (imported pattern from backtest script) ─────────


def rolling_dispatch_deterministic(
    prices: FloatArray, median_forecast: FloatArray,
    params: BatteryParams, window: int = 32, step: int = 16,
) -> DispatchResult:
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
        cp_ = BatteryParams(
            power_mw=params.power_mw, energy_mwh=params.energy_mwh,
            efficiency_charge=params.efficiency_charge,
            efficiency_discharge=params.efficiency_discharge,
            soc_min=params.soc_min, soc_max=params.soc_max,
            soc_initial=soc_all[t] / params.energy_mwh,
            soc_target=params.soc_target,
            degradation_eur_per_mwh=params.degradation_eur_per_mwh,
            terminal_penalty_eur_per_mwh=params.terminal_penalty_eur_per_mwh,
            isp_hours=params.isp_hours,
        )
        result = dispatch_deterministic(median_forecast[t:end], cp_)
        execute = min(step, n_window)
        charge_all[t:t + execute] = result.charge_mw[:execute]
        discharge_all[t:t + execute] = result.discharge_mw[:execute]
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
        charge_mw=charge_all, discharge_mw=discharge_all, soc_mwh=soc_all,
        revenue_eur=gross, degradation_cost_eur=deg, net_revenue_eur=gross - deg,
        status="rolling_optimal",
    )


def rolling_dispatch_cvar(
    prices: FloatArray, q_pred: FloatArray, quantiles: tuple[float, ...],
    params: BatteryParams, risk_aversion: float = 0.5, alpha: float = 0.05,
    n_scenarios: int = 20, window: int = 32, step: int = 16, seed: int = 42,
) -> DispatchResult:
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
        cp_ = BatteryParams(
            power_mw=params.power_mw, energy_mwh=params.energy_mwh,
            efficiency_charge=params.efficiency_charge,
            efficiency_discharge=params.efficiency_discharge,
            soc_min=params.soc_min, soc_max=params.soc_max,
            soc_initial=soc_all[t] / params.energy_mwh,
            soc_target=params.soc_target,
            degradation_eur_per_mwh=params.degradation_eur_per_mwh,
            terminal_penalty_eur_per_mwh=params.terminal_penalty_eur_per_mwh,
            isp_hours=params.isp_hours,
        )
        q_window = q_pred[t:end]
        scenarios = generate_scenarios(q_window, quantiles, n_scenarios, rng)
        result = dispatch_cvar(scenarios, cp_, alpha=alpha, risk_aversion=risk_aversion)
        execute = min(step, n_window)
        charge_all[t:t + execute] = result.charge_mw[:execute]
        discharge_all[t:t + execute] = result.discharge_mw[:execute]
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
        charge_mw=charge_all, discharge_mw=discharge_all, soc_mwh=soc_all,
        revenue_eur=gross, degradation_cost_eur=deg, net_revenue_eur=gross - deg,
        status="rolling_cvar",
    )


def _to_isp_results(
    prices_series: pd.Series[float], dispatch: DispatchResult, dt: float,
) -> list[ISPResult]:
    prices_arr = prices_series.to_numpy(dtype=float)
    results = []
    for t in range(len(prices_series)):
        net = (dispatch.discharge_mw[t] - dispatch.charge_mw[t]) * dt
        rev = net * prices_arr[t]
        ts = prices_series.index[t]
        results.append(ISPResult(
            timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
            price_short=prices_arr[t], charge_mw=dispatch.charge_mw[t],
            discharge_mw=dispatch.discharge_mw[t], soc_mwh=dispatch.soc_mwh[t],
            net_position_mwh=net, revenue_eur=rev,
        ))
    return results


def _dispatch_to_backtest_result(
    prices_series: pd.Series[float], dispatch: DispatchResult,
    policy: str, dt: float,
) -> BacktestResult:
    isps = _to_isp_results(prices_series, dispatch, dt)
    rev_arr = np.array([r.revenue_eur for r in isps])
    total_rev = float(rev_arr.sum())
    return BacktestResult(
        isps=isps, total_revenue_eur=total_rev,
        total_degradation_eur=dispatch.degradation_cost_eur,
        net_revenue_eur=total_rev - dispatch.degradation_cost_eur,
        n_periods=len(isps), policy=policy,
    )


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    battery = BatteryParams()

    print("=" * 70)
    print("FINAL HOLDOUT EVALUATION — ONE-TIME (R2)")
    print(f"Holdout: {HOLDOUT_START} to {HOLDOUT_END}")
    print("=" * 70)

    # Load ALL data including holdout
    prices_all = cache.read_frame("imbalance_prices", PICASSO_START, HOLDOUT_END)
    da_cached = cache.read_frame("day_ahead_price", PICASSO_START, HOLDOUT_END)
    da_raw = da_cached["day_ahead_price"]
    da = _resample_day_ahead(da_raw, pd.DatetimeIndex(prices_all.index))

    X_all = build_features(prices_all, day_ahead=da)
    targets_all = build_targets(prices_all)
    y_all = targets_all["price_short"]

    # Split: train on everything before holdout
    X_train = X_all[X_all.index < HOLDOUT_START]
    y_train = y_all[y_all.index < HOLDOUT_START]
    X_hold = X_all[(X_all.index >= HOLDOUT_START) & (X_all.index < HOLDOUT_END)]
    y_hold = y_all[(y_all.index >= HOLDOUT_START) & (y_all.index < HOLDOUT_END)]

    n_train = len(X_train)
    n_hold = len(X_hold)
    print(f"\nTrain: {n_train} ISPs, Holdout: {n_hold} ISPs")
    print(f"Holdout covers {n_hold * 0.25 / 24:.0f} days ({n_hold * 0.25 / 8760:.2f} years)")

    # ── Part 1: Forecast evaluation ─────────────────────────────────────
    print("\n" + "=" * 50)
    print("PART 1: FORECAST EVALUATION ON HOLDOUT")
    print("=" * 50)

    y_hold_arr = y_hold.to_numpy()
    forecast_results: dict[str, dict] = {}
    all_preds: dict[str, FloatArray] = {}
    per_obs: dict[str, FloatArray] = {}

    for name, factory in MODEL_FACTORIES.items():
        model = factory() if callable(factory) else factory
        model.fit(X_train, y_train)
        pred = model.predict_quantiles(X_hold, QUANTILES)

        valid = ~np.isnan(pred).any(axis=1)
        y_v = y_hold_arr[valid]
        p_v = pred[valid]
        if len(y_v) == 0:
            print(f"  {name}: all predictions NaN, skipped")
            continue

        loss = mean_pinball(y_v, p_v, QUANTILES)
        obs_loss = pinball_loss_per_obs(y_v, p_v, QUANTILES)
        cal = _calibration(y_v, p_v)

        all_preds[name] = pred
        per_obs[name] = obs_loss
        forecast_results[name] = {
            "pinball_loss": float(loss),
            "n_valid": int(valid.sum()),
            "n_nan": int((~valid).sum()),
            **cal,
        }
        print(f"  {name:18s}  pinball={loss:.2f}  CRPS={cal['crps']:.2f}  "
              f"MAE={cal['mae']:.1f}  cal_err={cal['mean_abs_cal_error']:.4f}")

    # DM tests: LEAR and GBM vs climatology
    print("\n--- DM tests (holdout) ---")
    ref_obs = per_obs.get("climatology")
    dm_holdout = {}
    if ref_obs is not None:
        for name in ["lear", "gbm"]:
            if name in per_obs and len(per_obs[name]) == len(ref_obs):
                max_lag = int(np.floor(len(ref_obs) ** (1.0 / 3.0)))
                dm_stat, p_val = diebold_mariano(per_obs[name], ref_obs, max_lag=max_lag)
                sig = "YES" if p_val < 0.05 else "no"
                print(f"  {name} vs climatology: DM={dm_stat:.3f}, p={p_val:.4f}, sig={sig}")
                dm_holdout[name] = {"dm_stat": dm_stat, "p_value": p_val}

    # Compare to walk-forward results
    wf_path = Path("work/evaluation/walkforward_results.json")
    if wf_path.exists():
        wf = json.loads(wf_path.read_text())
        print("\n--- Walk-forward vs holdout comparison ---")
        print(f"  {'model':18s} {'WF pinball':>12s} {'Holdout pinball':>16s} {'Delta':>8s}")
        for name in ["climatology", "lear", "gbm"]:
            wf_loss = wf["fold_losses"].get(name, {}).get("mean")
            ho_loss = forecast_results.get(name, {}).get("pinball_loss")
            if wf_loss is not None and ho_loss is not None:
                delta = ho_loss - wf_loss
                marker = " ***" if abs(delta) / wf_loss > 0.1 else ""
                print(f"  {name:18s} {wf_loss:12.2f} {ho_loss:16.2f} {delta:+8.2f}{marker}")

    # ── Part 2: Dispatch backtest on holdout ────────────────────────────
    print("\n" + "=" * 50)
    print("PART 2: DISPATCH BACKTEST ON HOLDOUT")
    print("=" * 50)

    prices_hold = y_hold
    config = BacktestConfig(battery=battery)

    # Train GBM on all pre-holdout data for dispatch
    gbm = QuantileGBM()
    gbm.fit(X_train, y_train)
    q_pred = gbm.predict_quantiles(X_hold, QUANTILES)
    median_idx = QUANTILES.index(0.5)
    median_pred = q_pred[:, median_idx]
    prices_arr = prices_hold.to_numpy(dtype=float)
    dt = battery.isp_hours

    # Perfect foresight
    print("\nRunning perfect foresight...")
    pf_result = run_backtest_perfect_foresight(prices_hold, config)
    print(f"  Net revenue: EUR {pf_result.net_revenue_eur:,.0f}")

    # Deterministic
    print("\nRunning deterministic (rolling horizon)...")
    det_dispatch = rolling_dispatch_deterministic(
        prices_arr, median_pred, battery, window=32, step=16,
    )
    det_result = _dispatch_to_backtest_result(prices_hold, det_dispatch, "deterministic", dt)
    print(f"  Net revenue: EUR {det_result.net_revenue_eur:,.0f}")

    # CVaR
    print("\nRunning CVaR (risk_aversion=0.5)...")
    cvar_dispatch = rolling_dispatch_cvar(
        prices_arr, q_pred, QUANTILES, battery,
        risk_aversion=0.5, n_scenarios=20, window=32, step=16,
    )
    cvar_result = _dispatch_to_backtest_result(prices_hold, cvar_dispatch, "cvar_0.5", dt)
    print(f"  Net revenue: EUR {cvar_result.net_revenue_eur:,.0f}")

    # Do nothing
    dn_result = run_backtest_do_nothing(prices_hold, config)

    policies = {
        "perfect_foresight": pf_result,
        "deterministic": det_result,
        "cvar_0.5": cvar_result,
        "do_nothing": dn_result,
    }

    pf_net = pf_result.net_revenue_eur
    print(f"\n{'Policy':<22s} {'Net Revenue':>14s} {'Ratio to PF':>14s}")
    for name, r in policies.items():
        ratio = r.net_revenue_eur / pf_net if pf_net != 0 else 0.0
        print(f"{name:<22s} {r.net_revenue_eur:>14,.0f} {ratio:>14.1%}")

    # Bootstrap CIs
    print("\n--- Block bootstrap 95% CIs (holdout) ---")
    ci_holdout: dict[str, dict[str, float]] = {}
    for name, r in policies.items():
        if name == "do_nothing":
            continue
        rev_per_isp = np.array([isp.revenue_eur for isp in r.isps])
        rev_per_isp_net = rev_per_isp.copy()
        if r.total_degradation_eur > 0 and len(rev_per_isp) > 0:
            rev_per_isp_net -= r.total_degradation_eur / len(rev_per_isp)
        lo, mean_v, hi = block_bootstrap_ci(rev_per_isp_net, block_size=96, n_bootstrap=5000)
        ci_holdout[name] = {"lo": lo, "mean": mean_v, "hi": hi}
        print(f"  {name:<22s} [{lo:>12,.0f}, {hi:>12,.0f}]  mean={mean_v:>12,.0f}")

    # Compare to walk-forward backtest
    bt_path = Path("work/backtest/backtest_results.json")
    if bt_path.exists():
        bt = json.loads(bt_path.read_text())
        n_wf_years = bt["n_test_isps"] * 0.25 / 8760
        n_ho_years = n_hold * 0.25 / 8760
        print("\n--- Walk-forward vs holdout revenue (annualised) ---")
        print(f"  {'Policy':<22s} {'WF ann.':>12s} {'Holdout ann.':>14s} {'Delta':>8s}")
        for name in ["deterministic", "cvar_0.5"]:
            wf_rev = bt["policies"].get(name, {}).get("net_revenue_eur", 0)
            ho_rev = policies[name].net_revenue_eur
            wf_ann = wf_rev / n_wf_years
            ho_ann = ho_rev / n_ho_years
            delta_pct = ((ho_ann / wf_ann) - 1) * 100 if wf_ann != 0 else 0
            print(f"  {name:<22s} {wf_ann:>12,.0f} {ho_ann:>14,.0f} {delta_pct:>+7.0f}%")

    # ── Save results ────────────────────────────────────────────────────
    results = {
        "holdout_start": str(HOLDOUT_START),
        "holdout_end": str(HOLDOUT_END),
        "n_train": n_train,
        "n_holdout": n_hold,
        "forecast_evaluation": forecast_results,
        "dm_tests_holdout": dm_holdout,
        "dispatch": {
            name: {
                "net_revenue_eur": r.net_revenue_eur,
                "total_revenue_eur": r.total_revenue_eur,
                "degradation_eur": r.total_degradation_eur,
                "ratio_to_pf": r.net_revenue_eur / pf_net if pf_net != 0 else 0.0,
            }
            for name, r in policies.items()
        },
        "bootstrap_ci_95": ci_holdout,
        "battery": {
            "power_mw": battery.power_mw,
            "energy_mwh": battery.energy_mwh,
            "efficiency_charge": battery.efficiency_charge,
            "efficiency_discharge": battery.efficiency_discharge,
            "degradation_eur_per_mwh": battery.degradation_eur_per_mwh,
        },
    }

    out = RESULTS_DIR / "holdout_results.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResults saved to {out}")
    print("\n" + "=" * 70)
    print("HOLDOUT EVALUATION COMPLETE. This was the one-time final evaluation.")
    print("=" * 70)


if __name__ == "__main__":
    main()
