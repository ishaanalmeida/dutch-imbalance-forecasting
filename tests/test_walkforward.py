# tests/test_walkforward.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.evaluation.walkforward import Fold, generate_folds
from src.features.targets import HOLDOUT_START, PICASSO_START


def test_folds_are_generated_in_chronological_order() -> None:
    folds = generate_folds(datetime(2025, 4, 1, tzinfo=UTC), datetime(2025, 7, 1, tzinfo=UTC))
    assert [f.test_start for f in folds] == sorted(f.test_start for f in folds)


def test_training_window_expands_rather_than_rolls() -> None:
    """R2 allows either, and the spec chose expanding: with only 22 months of
    post-PICASSO data, discarding early folds is a luxury we cannot afford."""
    folds = generate_folds(datetime(2025, 4, 1, tzinfo=UTC), datetime(2025, 8, 1, tzinfo=UTC))
    assert all(f.train_start == PICASSO_START for f in folds)
    assert [f.train_end for f in folds] == sorted(f.train_end for f in folds)


def test_a_purge_gap_separates_train_end_from_test_start() -> None:
    """The gap must cover the D+1 settlement publication, or a training label
    reaches a test feature through the lagged-target path."""
    folds = generate_folds(datetime(2025, 4, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC))
    for f in folds:
        assert f.test_start - f.train_end >= timedelta(days=1)


def test_train_and_test_never_overlap() -> None:
    folds = generate_folds(datetime(2025, 4, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    for f in folds:
        assert f.train_end <= f.test_start


def test_no_fold_ever_touches_the_holdout() -> None:
    """R2. If this fails the holdout is contaminated and the project's headline
    number is worthless."""
    folds = generate_folds(datetime(2025, 4, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
    for f in folds:
        assert f.test_end <= HOLDOUT_START
        assert f.train_end <= HOLDOUT_START


def test_no_fold_starts_before_picasso() -> None:
    folds = generate_folds(datetime(2025, 4, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC))
    assert all(f.train_start >= PICASSO_START for f in folds)


def test_test_windows_tile_without_gaps_or_overlaps() -> None:
    folds = generate_folds(datetime(2025, 4, 1, tzinfo=UTC), datetime(2025, 9, 1, tzinfo=UTC))
    for earlier, later in zip(folds, folds[1:], strict=False):
        assert earlier.test_end == later.test_start


def test_requesting_a_first_test_month_before_picasso_raises() -> None:
    with pytest.raises(ValueError, match="before PICASSO"):
        generate_folds(datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC))


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        generate_folds(datetime(2025, 4, 1), datetime(2025, 6, 1, tzinfo=UTC))


def test_fold_is_immutable() -> None:
    """A fold mutated mid-run silently changes what a result refers to."""
    folds = generate_folds(datetime(2025, 4, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC))
    with pytest.raises((AttributeError, TypeError)):
        folds[0].test_start = datetime(2030, 1, 1, tzinfo=UTC)  # type: ignore[misc]


def test_a_fold_knows_its_own_label() -> None:
    fold = Fold(
        train_start=PICASSO_START,
        train_end=datetime(2025, 3, 31, tzinfo=UTC),
        test_start=datetime(2025, 4, 1, tzinfo=UTC),
        test_end=datetime(2025, 5, 1, tzinfo=UTC),
    )
    assert fold.label == "2025-04"
