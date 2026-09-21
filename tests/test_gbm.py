from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluation.metrics import QUANTILES, mean_pinball
from src.models.baselines import PersistenceBaseline
from src.models.gbm import QuantileGBM


def _frame(n: int, rng: np.random.Generator) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "lag_price_short_freshest": rng.normal(50, 10, n),
            "lag_price_short_672": rng.normal(50, 10, n),
            "hour_sin": np.sin(2 * np.pi * idx.hour.to_numpy() / 24.0),
            "hour_cos": np.cos(2 * np.pi * idx.hour.to_numpy() / 24.0),
            "dow_sin": np.sin(2 * np.pi * idx.dayofweek.to_numpy() / 7.0),
            "dow_cos": np.cos(2 * np.pi * idx.dayofweek.to_numpy() / 7.0),
            "day_ahead_price": rng.normal(50, 10, n),
        },
        index=idx,
    )


def test_gbm_predicts_monotone_quantiles() -> None:
    rng = np.random.default_rng(0)
    X = _frame(300, rng)
    y = pd.Series(X["day_ahead_price"].to_numpy() + rng.normal(0, 2, len(X)), index=X.index)
    model = QuantileGBM(n_estimators=20)
    model.fit(X, y)
    pred = model.predict_quantiles(X, QUANTILES)
    assert pred.shape == (len(X), len(QUANTILES))
    assert np.all(np.diff(pred, axis=1) >= -1e-9)


def test_gbm_learns_a_nonlinear_signal() -> None:
    rng = np.random.default_rng(1)
    X_train = _frame(500, rng)
    y_train = pd.Series(
        X_train["day_ahead_price"].to_numpy() ** 2 / 50 + rng.normal(0, 1, len(X_train)),
        index=X_train.index,
    )
    X_test = _frame(100, rng)
    y_test = X_test["day_ahead_price"].to_numpy() ** 2 / 50 + rng.normal(0, 1, len(X_test))

    gbm = QuantileGBM(n_estimators=50)
    gbm.fit(X_train, y_train)
    gbm_pinball = mean_pinball(y_test, gbm.predict_quantiles(X_test, QUANTILES), QUANTILES)

    persistence = PersistenceBaseline()
    persistence.fit(X_train, y_train)
    pers_pinball = mean_pinball(
        y_test, persistence.predict_quantiles(X_test, QUANTILES), QUANTILES
    )

    assert gbm_pinball < pers_pinball


def test_gbm_raises_before_fit() -> None:
    rng = np.random.default_rng(2)
    X = _frame(10, rng)
    with pytest.raises(RuntimeError, match="fit"):
        QuantileGBM().predict_quantiles(X, (0.5,))


def test_gbm_raises_on_missing_features() -> None:
    rng = np.random.default_rng(3)
    X = _frame(100, rng)
    y = pd.Series(rng.normal(50, 10, len(X)), index=X.index)
    model = QuantileGBM(n_estimators=10)
    model.fit(X, y)
    X_bad = X.copy()
    X_bad.loc[X_bad.index[0], "day_ahead_price"] = np.nan
    with pytest.raises(ValueError, match="missing"):
        model.predict_quantiles(X_bad, (0.5,))


def test_gbm_drops_incomplete_rows_at_fit() -> None:
    rng = np.random.default_rng(4)
    X = _frame(200, rng)
    X.loc[X.index[:50], "lag_price_short_672"] = np.nan
    y = pd.Series(rng.normal(50, 10, len(X)), index=X.index)
    model = QuantileGBM(n_estimators=10)
    model.fit(X, y)
    pred = model.predict_quantiles(X.iloc[50:], (0.5,))
    assert np.isfinite(pred).all()
