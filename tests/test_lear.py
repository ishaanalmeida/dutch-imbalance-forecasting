from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluation.metrics import QUANTILES, mean_pinball
from src.models.baselines import PersistenceBaseline
from src.models.lear import LEARModel


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


def test_lear_predicts_monotone_quantiles() -> None:
    rng = np.random.default_rng(0)
    X = _frame(300, rng)
    y = pd.Series(X["day_ahead_price"].to_numpy() + rng.normal(0, 2, len(X)), index=X.index)
    model = LEARModel()
    model.fit(X, y)
    pred = model.predict_quantiles(X, QUANTILES)
    assert pred.shape == (len(X), len(QUANTILES))
    assert np.all(np.diff(pred, axis=1) >= -1e-9), "quantiles must not cross"


def test_lear_learns_a_strong_linear_signal_better_than_persistence() -> None:
    """Sanity check, not a proof: with y almost exactly day_ahead_price, LEAR
    (which sees that column) must clearly beat a baseline that does not."""
    rng = np.random.default_rng(1)
    X_train = _frame(500, rng)
    y_train = pd.Series(
        X_train["day_ahead_price"].to_numpy() + rng.normal(0, 0.5, len(X_train)),
        index=X_train.index,
    )
    X_test = _frame(100, rng)
    y_test = X_test["day_ahead_price"].to_numpy() + rng.normal(0, 0.5, len(X_test))

    lear = LEARModel()
    lear.fit(X_train, y_train)
    lear_pinball = mean_pinball(y_test, lear.predict_quantiles(X_test, QUANTILES), QUANTILES)

    X_test_with_persistence_col = X_test.assign(
        lag_price_short_freshest=X_test["lag_price_short_freshest"]
    )
    persistence = PersistenceBaseline()
    persistence.fit(X_train, y_train)
    persistence_pinball = mean_pinball(
        y_test,
        persistence.predict_quantiles(X_test_with_persistence_col, QUANTILES),
        QUANTILES,
    )

    assert lear_pinball < persistence_pinball


def test_lear_requires_day_ahead_price() -> None:
    rng = np.random.default_rng(2)
    X = _frame(50, rng)
    X["day_ahead_price"] = np.nan
    y = pd.Series(rng.normal(50, 10, len(X)), index=X.index)
    with pytest.raises(ValueError, match="day_ahead_price"):
        LEARModel().fit(X, y)


def test_lear_drops_incomplete_rows_at_fit_time() -> None:
    """Some early rows lack lag_price_short_672 (needs 7 days of lookback);
    fit must use the complete rows rather than raising or fabricating a fill."""
    rng = np.random.default_rng(3)
    X = _frame(200, rng)
    X.loc[X.index[:50], "lag_price_short_672"] = np.nan
    y = pd.Series(rng.normal(50, 10, len(X)), index=X.index)
    model = LEARModel()
    model.fit(X, y)  # must not raise
    pred = model.predict_quantiles(X.iloc[50:], (0.5,))
    assert np.isfinite(pred).all()


def test_lear_raises_on_missing_features_at_predict_time() -> None:
    rng = np.random.default_rng(4)
    X = _frame(100, rng)
    y = pd.Series(rng.normal(50, 10, len(X)), index=X.index)
    model = LEARModel()
    model.fit(X, y)
    X_missing = X.copy()
    X_missing.loc[X_missing.index[0], "day_ahead_price"] = np.nan
    with pytest.raises(ValueError, match="missing feature"):
        model.predict_quantiles(X_missing, (0.5,))


def test_lear_raises_before_fit() -> None:
    rng = np.random.default_rng(5)
    X = _frame(10, rng)
    with pytest.raises(RuntimeError, match="fit"):
        LEARModel().predict_quantiles(X, (0.5,))


def test_lear_caps_training_rows_to_the_most_recent() -> None:
    """QuantileRegressor's LP solve scales roughly O(n^1.7-2), not linearly --
    measured at 60s for one quantile on ~50k rows (docs/DECISIONS.md ADR-027).
    A 7-coefficient linear model does not need every row; fit() must cap to
    the most recent max_train_rows rather than pass the whole fold through."""
    rng = np.random.default_rng(6)
    n = 500
    X = _frame(n, rng)
    y = pd.Series(rng.normal(50, 10, n), index=X.index)

    model = LEARModel(max_train_rows=100)
    model.fit(X, y)
    assert len(model._train_y) == 100
    # And it must be the most RECENT 100 rows, not an arbitrary slice.
    np.testing.assert_allclose(model._train_y, y.to_numpy()[-100:])
