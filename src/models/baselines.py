"""The mandatory baselines (CLAUDE.md §4).

R4: no model is reported without being compared to these on identical data and
identical evaluation windows. If a model does not beat them, that is the
finding and it gets reported as such.

The climatological baseline deserves particular respect -- the brief notes it
is "surprisingly strong" and under-reported in the literature. Treat it as the
one to beat, not as a formality.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.metrics import enforce_monotone
from src.models.base import FloatArray

_GROUP = ["hour", "dayofweek"]


def _broadcast(point: FloatArray, n_quantiles: int) -> FloatArray:
    """A point forecast, repeated across every quantile.

    Deliberately not widened into a fake interval: a point forecast has no
    spread, and inventing one would flatter its calibration scores.
    """
    return np.repeat(point[:, None], n_quantiles, axis=1)


class PersistenceBaseline:
    """Last observed settled value, carried forward.

    "Last observed" means last *available*: settled prices publish once
    daily at D+1 10:00, so the freshest settled value a decision at ISP start
    can ever see is 1-2 days old, never one ISP old (ADR-023). That is a
    property of this market's settlement mechanics, not a weakness of the
    baseline -- reads `lag_price_short_freshest`, which is exactly that
    freshest-available value, already masked to NaN where nothing is yet
    known.
    """

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None:
        self._fitted = True

    def predict_quantiles(self, X: pd.DataFrame, quantiles: tuple[float, ...]) -> FloatArray:
        return _broadcast(X["lag_price_short_freshest"].to_numpy(dtype=float), len(quantiles))


class SeasonalNaiveBaseline:
    """Same ISP, `period_isps` ago. 96 = yesterday, 672 = last week."""

    def __init__(self, period_isps: int = 96) -> None:
        self.period_isps = period_isps

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None:
        self._train_y = y.astype(float)

    def predict_quantiles(self, X: pd.DataFrame, quantiles: tuple[float, ...]) -> FloatArray:
        shifted = self._train_y.shift(self.period_isps)
        # bfill then a global mean: the first `period` rows have no history,
        # and leaving them NaN would silently drop rows from every metric.
        filled = shifted.bfill().fillna(float(self._train_y.mean()))
        return _broadcast(filled.reindex(X.index).to_numpy(dtype=float), len(quantiles))


class ClimatologyBaseline:
    """Empirical quantiles conditioned on hour-of-day x day-of-week.

    Fitted on the training fold only. This is the strong one.
    """

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None:
        frame = X[_GROUP].copy()
        frame["_y"] = y.astype(float).to_numpy()
        self._groups = frame.groupby(_GROUP)["_y"]
        self._global = frame["_y"]
        self._fitted = True

    def predict_quantiles(self, X: pd.DataFrame, quantiles: tuple[float, ...]) -> FloatArray:
        if not getattr(self, "_fitted", False):
            raise RuntimeError("ClimatologyBaseline used before fit()")

        # groupby(...).quantile(array).unstack() puts the quantile levels on
        # columns in the order they were requested (verified empirically,
        # not just assumed) -- reindexed explicitly below as a belt-and-
        # braces guard against a pandas version that sorts them instead.
        taus = np.asarray(quantiles, dtype=float)
        table = self._groups.quantile(taus).unstack().reindex(columns=list(quantiles))
        fallback = self._global.quantile(taus).to_numpy(dtype=float)

        keys = pd.MultiIndex.from_frame(X[_GROUP])
        out = table.reindex(keys).to_numpy(dtype=float)
        missing = np.isnan(out).any(axis=1)
        out[missing] = fallback
        return enforce_monotone(out)


class DayAheadBaseline:
    """The day-ahead price as a direct predictor of the imbalance price."""

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None:
        self._fitted = True

    def predict_quantiles(self, X: pd.DataFrame, quantiles: tuple[float, ...]) -> FloatArray:
        return _broadcast(X["day_ahead_price"].to_numpy(dtype=float), len(quantiles))


class MajorityClassBaseline:
    """The training base rate, predicted for every row."""

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None:
        self._rate = float(y.astype(float).mean())

    def predict_proba(self, X: pd.DataFrame) -> FloatArray:
        return np.full(len(X), self._rate, dtype=float)


class ConditionalFrequencyBaseline:
    """Base rate conditioned on hour-of-day x day-of-week."""

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None:
        frame = X[_GROUP].copy()
        frame["_y"] = y.astype(float).to_numpy()
        self._table = frame.groupby(_GROUP)["_y"].mean()
        self._global = float(frame["_y"].mean())

    def predict_proba(self, X: pd.DataFrame) -> FloatArray:
        keys = pd.MultiIndex.from_frame(X[_GROUP])
        out = self._table.reindex(keys).to_numpy(dtype=float)
        return np.asarray(np.nan_to_num(out, nan=self._global))
