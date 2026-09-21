"""The interface every model in this project implements.

CLAUDE.md §9: swapping a model must require no change to evaluation,
optimisation or backtest code. That only holds if the interface is fixed
before the first model is written, which is why the baselines define it.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArray = npt.NDArray[np.float64]


@runtime_checkable
class QuantileModel(Protocol):
    """Predicts a set of quantiles per row."""

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None: ...

    def predict_quantiles(self, X: pd.DataFrame, quantiles: tuple[float, ...]) -> FloatArray:
        """Return shape (len(X), len(quantiles)), non-decreasing along axis 1."""
        ...


@runtime_checkable
class ProbabilityModel(Protocol):
    """Predicts a calibrated probability per row."""

    def fit(self, X: pd.DataFrame, y: pd.Series[Any]) -> None: ...

    def predict_proba(self, X: pd.DataFrame) -> FloatArray:
        """Return shape (len(X),), each value in [0, 1]."""
        ...
