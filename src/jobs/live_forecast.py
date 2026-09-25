"""Scheduled job: produce and log a live forecast.

CLAUDE.md §7: "logs the forecast at the time it was made, so that after
a few weeks you have a genuine, unfalsifiable out-of-sample track record."

Each run:
  1. Loads the latest cached imbalance price data
  2. Trains a GBM model on all available data
  3. Produces quantile forecasts for the next 8 ISPs (2 hours)
  4. Derives regulation state probabilities and dispatch recommendation
  5. Logs the forecast with a timestamp to forecast_log/forecasts.jsonl
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.data import cache
from src.data.timebase import ISP_MINUTES
from src.evaluation.metrics import QUANTILES
from src.features.builder import build_features
from src.features.targets import PICASSO_START, build_targets
from src.models.gbm import QuantileGBM
from src.models.lear import FEATURE_COLUMNS

LOG_DIR = Path("forecast_log")
LOG_FILE = LOG_DIR / "forecasts.jsonl"
N_AHEAD = 8


def _regulation_state_probs(quantiles: dict[str, float]) -> dict[str, float]:
    """Estimate regulation state probabilities from quantile forecast.

    State 1 (single-priced): price near the day-ahead level, moderate.
    State 2 (dual-priced): extreme prices (very high or very low).
    Heuristic based on the spread of the predictive distribution.
    """
    q10 = quantiles.get("0.10", 0.0)
    q50 = quantiles.get("0.50", 0.0)
    q90 = quantiles.get("0.90", 0.0)
    spread = q90 - q10

    if spread > 150:
        p_dual = 0.6
    elif spread > 80:
        p_dual = 0.4
    elif abs(q50) > 100:
        p_dual = 0.5
    else:
        p_dual = 0.2

    return {"single_price": round(1 - p_dual, 2), "dual_price": round(p_dual, 2)}


def _dispatch_recommendation(quantiles: dict[str, float]) -> dict[str, str]:
    """Simple dispatch recommendation from the quantile forecast."""
    q50 = quantiles.get("0.50", 0.0)
    q25 = quantiles.get("0.25", 0.0)
    q75 = quantiles.get("0.75", 0.0)

    if q25 > 30:
        return {"action": "discharge", "reason": f"Price likely positive (q25={q25:.0f})"}
    if q75 < -30:
        return {"action": "charge", "reason": f"Price likely negative (q75={q75:.0f})"}
    if q50 > 15:
        return {
            "action": "discharge",
            "reason": f"Median positive ({q50:.0f}), moderate confidence",
        }
    if q50 < -15:
        return {"action": "charge", "reason": f"Median negative ({q50:.0f}), moderate confidence"}
    return {"action": "hold", "reason": f"Price near zero (q50={q50:.0f}), insufficient edge"}


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    run_ts = datetime.now(UTC)

    prices = cache.read_frame("imbalance_prices", PICASSO_START, run_ts)
    if prices.empty:
        print("No cached price data available. Run data fetchers first.")
        return

    # Targets are the next N_AHEAD ISPs that start after this run, not the ISP
    # after the last cached price: settled prices publish in daily batches, so
    # that ISP is hours in the past and identical for every run of the day.
    step = f"{ISP_MINUTES}min"
    targets_idx = pd.date_range(pd.Timestamp(run_ts).ceil(step), periods=N_AHEAD, freq=step)
    grid = pd.date_range(prices.index[0], targets_idx[-1], freq=step)
    prices_ext = prices.reindex(grid)  # NaN = not yet published; features mask/propagate it

    # Day-ahead for the target ISPs is already published (D-1 13:00, market_rules.yaml),
    # so read through the last target, not just up to run_ts.
    da_end = targets_idx[-1].to_pydatetime() + timedelta(minutes=ISP_MINUTES)
    da_cached = cache.read_frame("day_ahead_price", PICASSO_START, da_end)
    if da_cached.empty or "day_ahead_price" not in da_cached.columns:
        print("No day-ahead price data. Run data fetchers first.")
        return
    da_raw = da_cached["day_ahead_price"]
    # ffill bridges the pre-2025-10 hourly DA onto the 15-min grid; target rows
    # take exact values only, so a missing auction result is NaN, never a stale carry.
    da = da_raw.reindex(grid, method="ffill")
    da.loc[targets_idx] = da_raw.reindex(targets_idx)

    X = build_features(prices_ext, day_ahead=da)
    X_train = X.loc[prices.index]
    y = build_targets(prices)["price_short"]

    if len(X_train) < 1000:
        print(f"Only {len(X_train)} ISPs available — need at least 1000 for training.")
        return

    print(f"Training GBM on {len(X_train)} ISPs...")
    model = QuantileGBM()
    model.fit(X_train, y)

    X_next = X.loc[targets_idx, list(FEATURE_COLUMNS)]
    skipped = X_next.index[X_next.isna().any(axis=1)]
    if len(skipped):
        print(f"Skipping {len(skipped)} ISPs with unpublished inputs: {list(skipped)}")
    X_next = X_next.drop(skipped)
    if X_next.empty:
        print("No target ISP has complete features — nothing logged.")
        return
    print(f"Forecasting {len(X_next)} ISPs from {X_next.index[0]}")

    q_pred = model.predict_quantiles(X_next, QUANTILES)

    taus = list(QUANTILES)
    forecasts = []
    for i, isp_ts in enumerate(X_next.index):
        quantile_dict = {f"{t:.2f}": float(q_pred[i][j]) for j, t in enumerate(taus)}
        reg_state = _regulation_state_probs(quantile_dict)
        dispatch = _dispatch_recommendation(quantile_dict)
        entry = {
            "forecast_issued_at": run_ts.isoformat(),
            "target_isp": isp_ts.isoformat(),
            "quantiles": quantile_dict,
            "median": float(q_pred[i][taus.index(0.5)]),
            "regulation_state": reg_state,
            "dispatch_recommendation": dispatch,
        }
        forecasts.append(entry)

    with open(LOG_FILE, "a") as f:
        for fc in forecasts:
            f.write(json.dumps(fc, default=str) + "\n")

    print(f"Logged {len(forecasts)} forecasts to {LOG_FILE}")
    print(f"  Latest settled ISP: {prices.index[-1]}")
    print(f"  Median forecast: EUR {forecasts[0]['median']:.1f}/MWh")
    print(f"  Regulation state: {forecasts[0]['regulation_state']}")
    print(f"  Dispatch: {forecasts[0]['dispatch_recommendation']}")
    with open(LOG_FILE) as f:
        n_total = sum(1 for _ in f)
    print(f"  Total logged forecasts: {n_total}")


if __name__ == "__main__":
    main()
