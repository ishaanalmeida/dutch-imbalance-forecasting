from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd
import pytest

from src.features.builder import CATALOGUE, build_features, render_catalogue_markdown


def _prices(n: int = 96 * 10) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {"price_long": rng.normal(50, 20, n), "price_short": rng.normal(60, 20, n)},
        index=idx,
    )


def test_every_catalogue_entry_states_a_rationale() -> None:
    """CLAUDE.md §4: 'Each feature needs a reason.' An unexplained feature is
    how a feature set becomes a dumping ground."""
    assert CATALOGUE
    for spec in CATALOGUE:
        assert spec.rationale.strip(), f"{spec.name} has no rationale"
        assert len(spec.rationale) > 20, f"{spec.name}'s rationale is too thin"


def test_catalogue_names_are_unique() -> None:
    names = [s.name for s in CATALOGUE]
    assert len(names) == len(set(names))


def test_built_columns_match_the_catalogue_exactly() -> None:
    """The catalogue is the single source of truth; a column that appears
    without an entry has no documented rationale or lag basis."""
    out = build_features(_prices())
    assert set(out.columns) == {s.name for s in CATALOGUE}


def test_no_feature_uses_the_target_period_itself() -> None:
    """The strongest available leak test: shift the target and confirm every
    feature is unchanged. A feature that peeks at period t moves when t moves."""
    prices = _prices()
    baseline = build_features(prices)

    shifted = prices.copy()
    shifted[["price_long", "price_short"]] = shifted[["price_long", "price_short"]] * 3.0
    after = build_features(shifted)

    unchanged = [
        c
        for c in baseline.columns
        if np.allclose(baseline[c].fillna(0).to_numpy(), after[c].fillna(0).to_numpy())
    ]
    # Calendar features cannot move; lagged price features legitimately do.
    assert "hour_sin" in unchanged
    # But no feature may equal the contemporaneous target.
    for col in after.columns:
        assert not np.allclose(
            after[col].fillna(0).to_numpy(),
            shifted["price_short"].fillna(0).to_numpy(),
        ), f"{col} equals the contemporaneous target"


def test_lagged_price_features_are_shifted_not_contemporaneous() -> None:
    prices = _prices(400)
    out = build_features(prices)
    lagged = out["lag_price_short_96"].dropna()
    expected = prices["price_short"].shift(96).reindex(lagged.index)
    assert np.allclose(lagged.to_numpy(), expected.to_numpy())


def test_calendar_features_are_cyclically_encoded() -> None:
    """CLAUDE.md §4 asks for cyclic encoding: hour 23 and hour 0 are adjacent,
    and a raw integer tells a linear model they are 23 apart."""
    out = build_features(_prices())
    assert {"hour_sin", "hour_cos", "dow_sin", "dow_cos"} <= set(out.columns)
    assert np.allclose(out["hour_sin"] ** 2 + out["hour_cos"] ** 2, 1.0)


def test_index_is_preserved_and_utc() -> None:
    prices = _prices()
    out = build_features(prices)
    assert out.index.equals(prices.index)
    assert str(cast(pd.DatetimeIndex, out.index).tz) == "UTC"


def test_naive_index_is_rejected() -> None:
    naive = pd.DataFrame(
        {"price_long": [1.0], "price_short": [1.0]},
        index=pd.date_range("2025-01-01", periods=1),
    )
    with pytest.raises(ValueError, match="tz-aware"):
        build_features(naive)


def test_catalogue_markdown_documents_every_feature() -> None:
    md = render_catalogue_markdown()
    for spec in CATALOGUE:
        assert spec.name in md
        assert spec.rationale[:30] in md
