# tests/test_targets.py
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from src.features.targets import (
    HOLDOUT_END,
    HOLDOUT_START,
    PICASSO_START,
    build_targets,
    holdout_slice,
    training_slice,
)


def _prices(long_: list[float], short: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(long_), freq="15min", tz="UTC")
    return pd.DataFrame({"price_long": long_, "price_short": short}, index=idx)


def test_dual_priced_flag_is_true_only_when_the_prices_differ() -> None:
    df = build_targets(_prices([10.0, 10.0, -5.0], [10.0, 30.0, 40.0]))
    assert df["is_dual_priced"].tolist() == [False, True, True]


def test_dual_priced_ignores_floating_point_noise() -> None:
    df = build_targets(_prices([10.0], [10.0 + 1e-12]))
    assert df["is_dual_priced"].tolist() == [False]


def test_spread_is_short_price_minus_day_ahead() -> None:
    frame = _prices([10.0, 20.0], [15.0, 25.0])
    frame["day_ahead_price"] = [5.0, 5.0]
    out = build_targets(frame)
    assert out["spread_short_vs_da"].tolist() == [10.0, 20.0]


def test_spread_is_absent_when_day_ahead_is_not_supplied() -> None:
    """T3 must be missing, not silently zero, when its input is absent."""
    out = build_targets(_prices([10.0], [15.0]))
    assert "spread_short_vs_da" not in out.columns


def test_training_slice_excludes_everything_before_picasso() -> None:
    """ADR-022: pre-PICASSO is ~6% dual-priced vs 26-40% after. Training across
    that boundary miscalibrates P(dual)."""
    idx = pd.date_range("2024-10-01", periods=96 * 40, freq="15min", tz="UTC")
    df = pd.DataFrame({"price_long": 1.0, "price_short": 1.0}, index=idx)
    out = training_slice(df)
    assert out.index.min() >= PICASSO_START


def test_training_slice_excludes_the_holdout() -> None:
    """R2: the holdout is evaluated exactly once, at the very end. Nothing in
    training may ever see it."""
    idx = pd.date_range("2026-04-25", periods=96 * 20, freq="15min", tz="UTC")
    df = pd.DataFrame({"price_long": 1.0, "price_short": 1.0}, index=idx)
    out = training_slice(df)
    assert out.index.max() < HOLDOUT_START
    assert len(out) > 0


def test_holdout_slice_is_exactly_the_reserved_window() -> None:
    idx = pd.date_range("2026-04-01", periods=96 * 150, freq="15min", tz="UTC")
    df = pd.DataFrame({"price_long": 1.0, "price_short": 1.0}, index=idx)
    out = holdout_slice(df)
    assert out.index.min() >= HOLDOUT_START
    assert out.index.max() < HOLDOUT_END


def test_training_and_holdout_never_overlap() -> None:
    idx = pd.date_range("2024-10-01", periods=96 * 400, freq="15min", tz="UTC")
    df = pd.DataFrame({"price_long": 1.0, "price_short": 1.0}, index=idx)
    assert set(training_slice(df).index) & set(holdout_slice(df).index) == set()


def test_holdout_boundaries_are_the_documented_dates() -> None:
    assert datetime(2026, 5, 1, tzinfo=UTC) == HOLDOUT_START
    assert datetime(2026, 8, 1, tzinfo=UTC) == HOLDOUT_END
    assert datetime(2024, 10, 18, tzinfo=UTC) == PICASSO_START


def test_naive_index_is_rejected() -> None:
    naive = pd.DataFrame(
        {"price_long": [1.0], "price_short": [1.0]},
        index=pd.date_range("2025-01-01", periods=1),
    )
    with pytest.raises(ValueError, match="tz-aware"):
        build_targets(naive)
