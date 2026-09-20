"""Scheduled job: produce and log a live forecast.

CLAUDE.md §7: "logs the forecast at the time it was made, so that after
a few weeks you have a genuine, unfalsifiable out-of-sample track record."

Each run:
  1. Loads the latest cached imbalance price data
  2. Trains a GBM model on all available data
  3. Produces quantile forecasts for the next 8 ISPs (2 hours)
  4. Logs the forecast with a timestamp to forecast_log/forecasts.jsonl

The JSONL log is the evidence: each line is a forecast as-issued, with
the run timestamp. It cannot be back-fitted. After realised prices arrive,
the track-record screen compares forecasts to outcomes.
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
        entry = {
            "forecast_issued_at": run_ts.isoformat(),
            "target_isp": isp_ts.isoformat(),
            "quantiles": {f"{t:.2f}": float(q_pred[i][j]) for j, t in enumerate(taus)},
            "median": float(q_pred[i][taus.index(0.5)]),
        }
        forecasts.append(entry)

    with open(LOG_FILE, "a") as f:
        for fc in forecasts:
            f.write(json.dumps(fc, default=str) + "\n")

    print(f"Logged {len(forecasts)} forecasts to {LOG_FILE}")
    print(f"  Latest ISP: {last_idx}")
    print(f"  Median forecast: EUR {forecasts[0]['median']:.1f}/MWh")
    with open(LOG_FILE) as f:
        n_total = sum(1 for _ in f)
    print(f"  Total logged forecasts: {n_total}")


if __name__ == "__main__":
    main()
