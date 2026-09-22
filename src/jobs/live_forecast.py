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
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from src.data import cache
from src.evaluation.metrics import QUANTILES
from src.features.builder import build_features
from src.features.targets import PICASSO_START, build_targets
from src.models.gbm import QuantileGBM

LOG_DIR = Path("forecast_log")
LOG_FILE = LOG_DIR / "forecasts.jsonl"


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

    end = run_ts
    prices = cache.read_frame("imbalance_prices", PICASSO_START, end)
    if prices.empty:
        print("No cached price data available. Run data fetchers first.")
        return

    da_cached = cache.read_frame("day_ahead_price", PICASSO_START, end)
    if da_cached.empty or "day_ahead_price" not in da_cached.columns:
        print("No day-ahead price data. Run data fetchers first.")
        return

    da_raw = da_cached["day_ahead_price"]
    da = da_raw.reindex(pd.DatetimeIndex(prices.index), method="ffill")

    X = build_features(prices, day_ahead=da)
    targets = build_targets(prices)
    y = targets["price_short"]

    if len(X) < 1000:
        print(f"Only {len(X)} ISPs available — need at least 1000 for training.")
        return

    print(f"Training GBM on {len(X)} ISPs...")
    model = QuantileGBM()
    model.fit(X, y)

    last_idx = X.index[-1]
    forecast_start = last_idx + pd.Timedelta(minutes=15)
    n_ahead = 8
    print(f"Forecasting {n_ahead} ISPs from {forecast_start}")

    last_row = X.iloc[-1:]
    q_pred = model.predict_quantiles(last_row, QUANTILES)

    taus = list(QUANTILES)
    forecasts = []
    for i in range(min(n_ahead, len(q_pred))):
        isp_ts = forecast_start + pd.Timedelta(minutes=15 * i)
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
    print(f"  Latest ISP: {last_idx}")
    print(f"  Median forecast: EUR {forecasts[0]['median']:.1f}/MWh")
    print(f"  Regulation state: {forecasts[0]['regulation_state']}")
    print(f"  Dispatch: {forecasts[0]['dispatch_recommendation']}")
    with open(LOG_FILE) as f:
        n_total = sum(1 for _ in f)
    print(f"  Total logged forecasts: {n_total}")


if __name__ == "__main__":
    main()
