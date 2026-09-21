"""Quantile-GBM: gradient boosting with quantile loss.

CLAUDE.md §4, model order #2: "Gradient boosting with quantile loss
(LightGBM objective='quantile', one model per quantile). Expect this to be
the workhorse." One LightGBM model per requested quantile, fit lazily in
predict_quantiles (same pattern as LEARModel — the shared interface has no
quantiles argument at fit time).

Uses the same FEATURE_COLUMNS as LEAR for a fair head-to-head. The GBM can
discover nonlinear interactions the linear model cannot, so any improvement
is attributable to nonlinearity, not to seeing different features.
"""

from __future__ import annotations

from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.evaluation.metrics import enforce_monotone
from src.models.base import FloatArray
from src.models.lear import FEATURE_COLUMNS


class QuantileGBM:
    """One LightGBM model per quantile, objective='quantile'."""

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: int = 6,
        learning_rate: float = 0.05,
        min_child_samples: int = 50,
        subsample: float = 0.8,
        seed: int = 42,
    ) -> None:
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.min_child_samples = min_child_samples
        self.subsample = subsample
        self.seed = seed
        self._models: dict[float, lgb.LGBMRegressor] = {}
        self._fitted = False

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None:
        frame = X[list(FEATURE_COLUMNS)]
        if frame["day_ahead_price"].isna().all():
            raise ValueError("QuantileGBM needs day_ahead_price")
        complete = frame.notna().all(axis=1)
        if not complete.any():
            raise ValueError("QuantileGBM has no complete training row")

        self._train_X = frame[complete]
        self._train_y = y.astype(float)[complete].to_numpy()
        self._models = {}
        self._fitted = True

    def predict_quantiles(
        self, X: pd.DataFrame, quantiles: tuple[float, ...]
    ) -> FloatArray:
        if not self._fitted:
            raise RuntimeError("QuantileGBM used before fit()")
        frame = X[list(FEATURE_COLUMNS)]
        if bool(frame.isna().any().any()):
            raise ValueError(
                "QuantileGBM cannot predict with missing features"
            )
        for tau in quantiles:
            if tau not in self._models:
                model = lgb.LGBMRegressor(
                    objective="quantile",
                    alpha=tau,
                    n_estimators=self.n_estimators,
                    max_depth=self.max_depth,
                    learning_rate=self.learning_rate,
                    min_child_samples=self.min_child_samples,
                    subsample=self.subsample,
                    subsample_freq=1,
                    random_state=self.seed,
                    verbose=-1,
                )
                model.fit(self._train_X, self._train_y)
                self._models[tau] = model

        columns = [self._models[tau].predict(frame) for tau in quantiles]
        return enforce_monotone(np.column_stack(columns))
