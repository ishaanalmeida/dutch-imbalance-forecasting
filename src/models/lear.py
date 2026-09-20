"""LEAR-style quantile regression: the literature's standard benchmark.

CLAUDE.md §4, model order #1: "Quantile regression / LEAR-style regularised
linear model... Any nonlinear model must beat this to justify itself." One
L1-regularised linear model per quantile, fit by pinball loss --
`sklearn.linear_model.QuantileRegressor` solves exactly that LP, so there is
no reason to hand-roll it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import QuantileRegressor
from sklearn.preprocessing import StandardScaler

from src.evaluation.metrics import enforce_monotone
from src.models.base import FloatArray

# Deliberately excludes lag_spread_96 and the raw hour/dayofweek integers.
# lag_spread_96 carries the same ~43% per-row mask as lag_price_short_96
# (config/market_rules.yaml D+1 10:00 settlement rule) -- including it
# honestly needs a missingness indicator, not silent imputation, which is a
# follow-up ablation once this v1 is evaluated, not a v1 requirement
# (docs/DECISIONS.md). hour/dayofweek are catalogued only as grouping keys
# for the climatology baselines (src/features/catalogue.py); a linear model
# gets the same information, correctly encoded, from hour_sin/cos/dow_sin/cos.
FEATURE_COLUMNS: tuple[str, ...] = (
    "lag_price_short_freshest",
    "lag_price_short_672",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "day_ahead_price",
)


class LEARModel:
    """One L1-regularised (LASSO-style) quantile regression per requested tau.

    `alpha` is shared across every quantile for simplicity; CLAUDE.md's
    permutation-importance pass can motivate a per-quantile sweep later if
    this v1 shows it matters. Quantile models are fit lazily in
    `predict_quantiles`, on whichever taus haven't been seen yet, and cached
    -- `fit(X, y)` has no `quantiles` argument (the shared model interface,
    CLAUDE.md §9), so there is nothing else to fit against until a caller
    names the quantiles it wants.

    `max_train_rows` caps training to the most recent N rows. Measured
    directly (docs/DECISIONS.md ADR-027): `QuantileRegressor`'s LP solve
    scales roughly O(n^1.7-2), not linearly -- 2.65s at 10k rows, 60s at the
    full ~50k-row late-fold training set, for ONE of 19 quantiles. A 7-
    coefficient linear model does not need 50,000 rows to fit stably; capping
    to the most recent `max_train_rows` is a tractability choice about this
    one model's training window, not a change to which test months get
    evaluated (`generate_folds`'s expanding-window folds are untouched).
    """

    def __init__(self, alpha: float = 0.01, max_train_rows: int = 8000) -> None:
        self.alpha = alpha
        self.max_train_rows = max_train_rows
        self._scaler: StandardScaler | None = None
        self._models: dict[float, QuantileRegressor] = {}

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None:
        frame = X[list(FEATURE_COLUMNS)]
        if frame["day_ahead_price"].isna().all():
            raise ValueError(
                "LEARModel needs day_ahead_price; got an all-NaN column -- "
                "supply real day-ahead data, do not fit on a caller's omitted input"
            )
        complete = frame.notna().all(axis=1)
        if not complete.any():
            raise ValueError("LEARModel has no training row with every feature present")

        train_X = frame[complete].to_numpy(dtype=float)
        train_y = y.astype(float)[complete].to_numpy()
        if len(train_X) > self.max_train_rows:
            train_X = train_X[-self.max_train_rows :]
            train_y = train_y[-self.max_train_rows :]

        self._scaler = StandardScaler().fit(train_X)
        self._train_X_scaled = self._scaler.transform(train_X)
        self._train_y = train_y
        self._models = {}

    def predict_quantiles(self, X: pd.DataFrame, quantiles: tuple[float, ...]) -> FloatArray:
        if self._scaler is None:
            raise RuntimeError("LEARModel used before fit()")
        frame = X[list(FEATURE_COLUMNS)]
        if bool(frame.isna().any().any()):
            raise ValueError(
                "LEARModel cannot predict a row with a missing feature -- "
                "filter incomplete rows before calling predict_quantiles"
            )
        scaled_X = self._scaler.transform(frame.to_numpy(dtype=float))

        for tau in quantiles:
            if tau not in self._models:
                model = QuantileRegressor(quantile=tau, alpha=self.alpha, solver="highs")
                model.fit(self._train_X_scaled, self._train_y)
                self._models[tau] = model

        columns = [self._models[tau].predict(scaled_X) for tau in quantiles]
        return enforce_monotone(np.column_stack(columns))
