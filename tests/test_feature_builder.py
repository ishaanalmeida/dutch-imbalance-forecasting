from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pytest

from src.features.builder import CATALOGUE, _masked_lag, build_features, render_catalogue_markdown

_FEATURES_MD = Path(__file__).resolve().parents[1] / "docs" / "FEATURES.md"


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


def test_no_catalogued_feature_is_ever_unavailable_for_every_row() -> None:
    """A feature that is NaN everywhere is not conservative, it is broken --
    and lag_price_short_1 was exactly that before this fix.

    day_ahead_price is the one column that is legitimately all-NaN when no
    day_ahead series is supplied at all (ADR-023): that is a caller omitting
    an optional input, not an impossible feature, so it is supplied here.
    """
    prices = _prices(96 * 30)
    day_ahead = pd.Series(50.0, index=prices.index)
    out = build_features(prices, day_ahead=day_ahead)
    for col in out.columns:
        assert out[col].notna().any(), f"{col} is unavailable for every row"


def test_same_day_settled_price_is_never_used() -> None:
    """Settled prices publish D+1 10:00, so no settled value from the target's
    own delivery day can ever be a feature."""
    from datetime import timedelta

    from src.data.data_availability import is_available

    isp = pd.Timestamp("2026-06-17 12:00", tz="UTC").to_pydatetime()
    assert not is_available("imbalance_price_settled", isp - timedelta(minutes=15), isp)


def test_yesterdays_price_is_masked_before_the_settlement_run() -> None:
    """Available for afternoon decisions, not early-morning ones. The mask is
    the point: ~43% of rows genuinely cannot see it."""
    out = build_features(_prices(96 * 5))
    col = out["lag_price_short_96"].dropna()
    assert 0 < len(col) < len(out), "expected a partial mask, not all-or-nothing"


def test_as_of_caps_the_decision_time_for_rows_after_it() -> None:
    """A live forecast is decided when the job runs, not at each target's own
    ISP start. Run at 09:00 CET: yesterday's settled price (D+1 10:00) is not
    yet published, so an 11:00 CET target must not see it -- even though a
    decision taken at 11:00 could."""
    prices = _prices(96 * 5)
    as_of = pd.Timestamp("2025-01-04 08:00", tz="UTC")  # 09:00 CET
    target = pd.Timestamp("2025-01-04 10:00", tz="UTC")  # 11:00 CET

    plain = build_features(prices)
    capped = build_features(prices, as_of=as_of.to_pydatetime())

    assert pd.notna(plain.at[target, "lag_price_short_96"]), "precondition"
    assert pd.isna(capped.at[target, "lag_price_short_96"])
    assert pd.isna(capped.at[target, "lag_spread_96"])
    assert capped.at[target, "lag_price_short_freshest"] == capped.at[target, "lag_price_short_192"]
    # Rows up to as_of are decided at their own ISP start, exactly as before.
    upto = plain.index <= as_of
    pd.testing.assert_frame_equal(plain[upto], capped[upto])


def test_masked_lag_raises_when_never_available_across_a_multi_day_window() -> None:
    """The general form of the lag_price_short_1 bug ADR-023 found and removed:
    a lag unavailable for every row of a multi-day window is a catalogue bug,
    not a per-row state, and must raise rather than silently produce an
    all-NaN column (docs/DECISIONS.md ADR-025)."""
    idx = pd.date_range("2025-01-01", periods=96 * 2, freq="15min", tz="UTC")
    series = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    never_available = pd.Series(False, index=idx)
    with pytest.raises(ValueError, match="structurally impossible"):
        _masked_lag(series, never_available, "some_field", 1)


def test_docs_features_md_matches_the_generated_markdown() -> None:
    """docs/FEATURES.md declares itself generated and 'do not edit by hand' --
    nothing else enforced that until now, so a catalogue edit could silently
    leave the checked-in doc stale (docs/DECISIONS.md ADR-025)."""
    assert _FEATURES_MD.read_text(encoding="utf-8") == render_catalogue_markdown()
