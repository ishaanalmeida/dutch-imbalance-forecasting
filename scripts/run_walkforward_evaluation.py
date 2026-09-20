"""Walk-forward evaluation: every mandatory baseline plus LEAR, on identical
folds and identical data (CLAUDE.md R4). Phase 2's first real, measured
result -- prints mean pinball loss per model per fold and overall.

T2 target: `price_short`, the settlement price a BRP shortage pays -- the
concrete column every existing baseline's feature naming already points at
(`lag_price_short_*`, `DayAheadBaseline` predicting the same level).

Segmentation this does NOT yet do, and why that is a known limitation rather
than an oversight: the day-ahead MTU changed from 60 to 15 minutes on
2025-10-01 (docs/DOMAIN_NOTES.md Q10), so `day_ahead_price` is a broadcast
hourly value before that date and a native 15-minute value after. Both are
resampled onto the ISP grid the same way (forward-fill), which is the
economically correct value in both regimes, but a result spanning that
boundary is not yet segmented at it the way CLAUDE.md's "segmented reporting"
asks for -- that is the natural next slice, not done here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

import numpy as np
import pandas as pd

from src.data import cache
from src.evaluation.metrics import QUANTILES, mean_pinball
from src.evaluation.walkforward import generate_folds
from src.features.builder import build_features
from src.features.targets import HOLDOUT_START, PICASSO_START, build_targets
from src.models.base import FloatArray, QuantileModel
from src.models.baselines import (
    ClimatologyBaseline,
    DayAheadBaseline,
    PersistenceBaseline,
    SeasonalNaiveBaseline,
)
from src.models.lear import LEARModel

MODEL_FACTORIES: dict[str, Callable[[], QuantileModel]] = {
    "persistence": PersistenceBaseline,
    "seasonal_naive_1d": lambda: SeasonalNaiveBaseline(period_isps=96),
    "seasonal_naive_1w": lambda: SeasonalNaiveBaseline(period_isps=672),
    "climatology": ClimatologyBaseline,
    "day_ahead": DayAheadBaseline,
    "lear": LEARModel,
}


def _resample_day_ahead(
    day_ahead_raw: pd.Series[float], isp_index: pd.DatetimeIndex
) -> pd.Series[float]:
    """Forward-fill onto the 15-min ISP grid. Correct in both MTU regimes
    (docs/DOMAIN_NOTES.md Q10): pre-2025-10-01 this broadcasts each hourly
    auction price across its four ISPs; post-2025-10-01 the source is already
    15-minute and this is an exact-match reindex."""
    return day_ahead_raw.reindex(isp_index, method="ffill")


_F = TypeVar("_F", pd.DataFrame, "pd.Series[float]")


def _slice(frame: _F, start: datetime, end: datetime) -> _F:
    return frame[(frame.index >= start) & (frame.index < end)]


def main() -> int:
    prices = cache.read_frame("imbalance_prices", PICASSO_START, HOLDOUT_START)
    day_ahead_cached = cache.read_frame("day_ahead_price", PICASSO_START, HOLDOUT_START)
    day_ahead_raw = day_ahead_cached["day_ahead_price"]
    day_ahead = _resample_day_ahead(day_ahead_raw, pd.DatetimeIndex(prices.index))

    X = build_features(prices, day_ahead=day_ahead)
    y = build_targets(prices)["price_short"]

    folds = generate_folds(datetime(2024, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
    print(f"{len(folds)} folds: {folds[0].label} .. {folds[-1].label}\n")

    losses: dict[str, list[float]] = {name: [] for name in MODEL_FACTORIES}
    for fold in folds:
        X_train = _slice(X, fold.train_start, fold.train_end)
        y_train = _slice(y, fold.train_start, fold.train_end)
        X_test = _slice(X, fold.test_start, fold.test_end)
        y_test = _slice(y, fold.test_start, fold.test_end)

        for name, make_model in MODEL_FACTORIES.items():
            model = make_model()
            try:
                model.fit(X_train, y_train)
                pred: FloatArray = model.predict_quantiles(X_test, QUANTILES)
                loss = mean_pinball(y_test.to_numpy(), pred, QUANTILES)
            except Exception as exc:  # a model failing one fold must not abort the run
                print(f"  {fold.label} {name:18s} FAILED: {exc!r}")
                continue
            losses[name].append(loss)
        print(f"  {fold.label} done")

    print("\n=== Mean pinball loss across folds (EUR/MWh, lower is better) ===")
    for name, values in losses.items():
        if values:
            mean, std = np.mean(values), np.std(values)
            print(f"{name:18s} n={len(values):2d}  mean={mean:.4f}  std={std:.4f}")
        else:
            print(f"{name:18s} n=0   (never produced a prediction)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
