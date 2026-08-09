from __future__ import annotations

import numpy as np
import pytest

from src.evaluation.metrics import (
    QUANTILES,
    brier_score,
    crps_from_quantiles,
    empirical_coverage,
    enforce_monotone,
    log_loss_binary,
    mean_pinball,
    pinball_loss,
    pit_values,
)


def test_quantile_grid_matches_the_brief() -> None:
    """CLAUDE.md §4: 'at minimum 0.05 ... 0.95 in steps of 0.05'."""
    assert len(QUANTILES) == 19
    assert QUANTILES[0] == pytest.approx(0.05)
    assert QUANTILES[-1] == pytest.approx(0.95)


def test_pinball_loss_hand_worked() -> None:
    """q=0.5, y=10, pred=8 -> 0.5 * (10-8) = 1.0 (under-prediction).
    q=0.5, y=10, pred=12 -> (1-0.5) * (12-10) = 1.0 (over-prediction)."""
    y = np.array([10.0])
    assert pinball_loss(y, np.array([[8.0]]), (0.5,))[0] == pytest.approx(1.0)
    assert pinball_loss(y, np.array([[12.0]]), (0.5,))[0] == pytest.approx(1.0)


def test_pinball_penalises_asymmetrically_at_extreme_quantiles() -> None:
    """At q=0.95, under-predicting must cost far more than over-predicting."""
    y = np.array([10.0])
    under = pinball_loss(y, np.array([[8.0]]), (0.95,))[0]
    over = pinball_loss(y, np.array([[12.0]]), (0.95,))[0]
    assert under == pytest.approx(0.95 * 2)
    assert over == pytest.approx(0.05 * 2)
    assert under > over


def test_pinball_is_zero_for_a_perfect_prediction() -> None:
    y = np.array([10.0, 20.0])
    preds = np.array([[10.0], [20.0]])
    assert mean_pinball(y, preds, (0.5,)) == pytest.approx(0.0)


def test_crps_is_zero_for_a_perfect_deterministic_forecast() -> None:
    y = np.array([5.0])
    q_pred = np.full((1, len(QUANTILES)), 5.0)
    assert crps_from_quantiles(y, q_pred, QUANTILES) == pytest.approx(0.0, abs=1e-9)


def test_crps_grows_as_the_forecast_moves_away() -> None:
    y = np.array([5.0])
    near = crps_from_quantiles(y, np.full((1, len(QUANTILES)), 6.0), QUANTILES)
    far = crps_from_quantiles(y, np.full((1, len(QUANTILES)), 20.0), QUANTILES)
    assert far > near > 0


def test_empirical_coverage_of_a_perfectly_calibrated_forecast() -> None:
    """Draw from a known uniform, predict its true quantiles: empirical
    coverage must track nominal within sampling error."""
    rng = np.random.default_rng(0)
    y = rng.uniform(0.0, 1.0, size=20_000)
    q_pred = np.tile(np.array(QUANTILES), (len(y), 1))
    coverage = empirical_coverage(y, q_pred, QUANTILES)
    assert np.allclose(coverage, np.array(QUANTILES), atol=0.02)


def test_pit_of_a_calibrated_forecast_is_approximately_uniform() -> None:
    rng = np.random.default_rng(1)
    y = rng.uniform(0.0, 1.0, size=20_000)
    q_pred = np.tile(np.array(QUANTILES), (len(y), 1))
    pit = pit_values(y, q_pred, QUANTILES)
    counts, _ = np.histogram(pit, bins=10, range=(0.0, 1.0))
    assert counts.std() / counts.mean() < 0.15


def test_enforce_monotone_sorts_crossed_quantiles() -> None:
    """CLAUDE.md §4 requires quantile crossing be addressed explicitly. We sort
    post hoc, and say so."""
    crossed = np.array([[10.0, 8.0, 12.0]])
    assert enforce_monotone(crossed).tolist() == [[8.0, 10.0, 12.0]]


def test_enforce_monotone_leaves_already_sorted_rows_untouched() -> None:
    ok = np.array([[1.0, 2.0, 3.0]])
    assert enforce_monotone(ok).tolist() == ok.tolist()


def test_brier_score_hand_worked() -> None:
    """Perfect confident prediction scores 0; maximally wrong scores 1."""
    assert brier_score(np.array([1.0]), np.array([1.0])) == pytest.approx(0.0)
    assert brier_score(np.array([1.0]), np.array([0.0])) == pytest.approx(1.0)
    assert brier_score(np.array([1.0, 0.0]), np.array([0.5, 0.5])) == pytest.approx(0.25)


def test_log_loss_penalises_confident_errors_severely() -> None:
    mild = log_loss_binary(np.array([1.0]), np.array([0.4]))
    severe = log_loss_binary(np.array([1.0]), np.array([0.01]))
    assert severe > mild > 0


def test_log_loss_is_finite_for_a_zero_probability() -> None:
    """An unclipped log loss returns inf and destroys a whole run's mean."""
    assert np.isfinite(log_loss_binary(np.array([1.0]), np.array([0.0])))


def test_mismatched_shapes_raise() -> None:
    with pytest.raises(ValueError, match="shape"):
        pinball_loss(np.array([1.0, 2.0]), np.array([[1.0]]), (0.5,))
