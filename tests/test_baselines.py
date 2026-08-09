from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluation.metrics import QUANTILES
from src.models.baselines import (
    ClimatologyBaseline,
    ConditionalFrequencyBaseline,
    DayAheadBaseline,
    MajorityClassBaseline,
    PersistenceBaseline,
    SeasonalNaiveBaseline,
)


def _frame(n: int = 96 * 14) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "lag_price_short_1": rng.normal(50, 20, n),
            "day_ahead_price": rng.normal(50, 10, n),
            "hour": idx.hour,
            "dayofweek": idx.dayofweek,
        },
        index=idx,
    )


def test_persistence_predicts_the_last_observed_value_at_every_quantile() -> None:
    X = _frame(10)
    model = PersistenceBaseline()
    model.fit(X, pd.Series(np.zeros(len(X)), index=X.index))
    pred = model.predict_quantiles(X, QUANTILES)
    assert pred.shape == (len(X), len(QUANTILES))
    # A point forecast broadcast across quantiles: every column identical.
    assert np.allclose(pred, pred[:, [0]])


def test_climatology_quantiles_are_monotone() -> None:
    X = _frame()
    y = pd.Series(np.random.default_rng(1).normal(50, 20, len(X)), index=X.index)
    model = ClimatologyBaseline()
    model.fit(X, y)
    pred = model.predict_quantiles(X, QUANTILES)
    assert np.all(np.diff(pred, axis=1) >= -1e-9), "quantiles must not cross"


def test_climatology_conditions_on_hour_and_dayofweek() -> None:
    """Two groups with very different levels must get very different forecasts.
    CLAUDE.md §4 warns this baseline is stronger than papers admit."""
    idx = pd.date_range("2025-01-01", periods=96 * 28, freq="15min", tz="UTC")
    X = pd.DataFrame({"hour": idx.hour, "dayofweek": idx.dayofweek}, index=idx)
    y = pd.Series(np.where(idx.hour < 12, 10.0, 100.0), index=idx)
    model = ClimatologyBaseline()
    model.fit(X, y)
    pred = model.predict_quantiles(X, (0.5,))[:, 0]
    assert pred[idx.hour < 12].mean() < 20
    assert pred[idx.hour >= 12].mean() > 90


def test_climatology_falls_back_to_the_global_distribution_for_unseen_groups() -> None:
    """An unseen hour-of-week must not produce NaN and poison the metrics."""
    train_idx = pd.date_range("2025-01-06", periods=96, freq="15min", tz="UTC")
    X_train = pd.DataFrame(
        {"hour": train_idx.hour, "dayofweek": train_idx.dayofweek}, index=train_idx
    )
    y_train = pd.Series(np.linspace(1, 100, len(train_idx)), index=train_idx)
    model = ClimatologyBaseline()
    model.fit(X_train, y_train)

    test_idx = pd.date_range("2025-01-11", periods=4, freq="15min", tz="UTC")
    X_test = pd.DataFrame({"hour": test_idx.hour, "dayofweek": test_idx.dayofweek}, index=test_idx)
    pred = model.predict_quantiles(X_test, QUANTILES)
    assert np.isfinite(pred).all()


def test_seasonal_naive_uses_the_requested_lag() -> None:
    X = _frame()
    y = pd.Series(np.arange(len(X), dtype=float), index=X.index)
    model = SeasonalNaiveBaseline(period_isps=96)
    model.fit(X, y)
    pred = model.predict_quantiles(X, (0.5,))[:, 0]
    assert np.allclose(pred[96:], y.to_numpy()[:-96])


def test_seasonal_naive_has_no_nan_at_the_start() -> None:
    """The first `period` rows have no history; they must be filled, not NaN."""
    X = _frame(200)
    y = pd.Series(np.arange(len(X), dtype=float), index=X.index)
    model = SeasonalNaiveBaseline(period_isps=96)
    model.fit(X, y)
    assert np.isfinite(model.predict_quantiles(X, (0.5,))).all()


def test_day_ahead_baseline_predicts_the_day_ahead_price() -> None:
    X = _frame(10)
    model = DayAheadBaseline()
    model.fit(X, pd.Series(np.zeros(len(X)), index=X.index))
    pred = model.predict_quantiles(X, (0.5,))[:, 0]
    assert np.allclose(pred, X["day_ahead_price"].to_numpy())


def test_majority_class_predicts_the_training_base_rate() -> None:
    X = _frame(100)
    y = pd.Series([True] * 30 + [False] * 70, index=X.index)
    model = MajorityClassBaseline()
    model.fit(X, y)
    assert np.allclose(model.predict_proba(X), 0.30)


def test_conditional_frequency_learns_per_hour_rates() -> None:
    idx = pd.date_range("2025-01-01", periods=96 * 28, freq="15min", tz="UTC")
    X = pd.DataFrame({"hour": idx.hour, "dayofweek": idx.dayofweek}, index=idx)
    y: pd.Series[bool] = pd.Series(idx.hour < 6, index=idx)
    model = ConditionalFrequencyBaseline()
    model.fit(X, y)
    p = model.predict_proba(X)
    assert p[idx.hour < 6].mean() > 0.9
    assert p[idx.hour >= 6].mean() < 0.1


def test_probabilities_stay_inside_the_unit_interval() -> None:
    X = _frame(200)
    y: pd.Series[bool] = pd.Series(np.random.default_rng(2).random(len(X)) > 0.5, index=X.index)
    for model in (MajorityClassBaseline(), ConditionalFrequencyBaseline()):
        model.fit(X, y)
        p = model.predict_proba(X)
        assert ((p >= 0.0) & (p <= 1.0)).all()


def test_predicting_before_fitting_raises() -> None:
    """A model used unfitted returns silent garbage that looks like a result."""
    with pytest.raises(RuntimeError, match="fit"):
        ClimatologyBaseline().predict_quantiles(_frame(4), QUANTILES)
