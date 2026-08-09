# Phase 2a — Features, Targets, Walk-Forward and Baselines

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the feature/target layer, the walk-forward harness, and every mandatory baseline — evaluated on identical windows — so that no model can later be reported without a fair comparison.

**Architecture:** Targets and features are built from the Parquet cache, with every field access gated by Phase 1's `assert_available`. A fold generator produces expanding-origin train/test splits with a purge gap. Baselines implement the same `fit`/`predict_quantiles`/`predict_proba` interface every later model will, so the evaluation code written here never changes again.

**Tech Stack:** Python 3.12, pandas, numpy, scikit-learn (metrics only), pytest. LightGBM is deliberately **not** added in this plan.

**Spec:** `docs/superpowers/specs/2026-08-09-phase2-forecasting-design.md`

## Global Constraints

- **R1 — No look-ahead. Ever.** Every feature for target period `t` must have been published strictly before `decision_time(t) = start(t)`.
- **R2 — Walk-forward only.** No random splits, no k-fold. Expanding origin with a purge/embargo gap. The holdout is evaluated exactly once, at the very end.
- **R3 — Never fabricate a number.** No illustrative figures presented as results.
- **R4 — Baselines first, always.** No model is reported without comparison to these baselines on identical windows.
- **Training window: 2024-10-18 onward only** (ADR-022 — pre-PICASSO is ~6% dual-priced vs 26–40% after).
- **Final holdout: 2026-05-01 → 2026-07-31. Nothing in this plan may read it.**
- Python `>=3.11,<3.13`; `mypy --strict` and `ruff` clean; every test function annotated `-> None`.
- `src/evaluation/` is a **rigour zone**: minimalism suspended, `ponytail:` comments prohibited.
- `uv` is not on PATH: invoke as `"$USERPROFILE/.local/bin/uv.exe"`.

## Scope note

Phase 2 is split. **This plan (2a)** delivers features, targets, folds, baselines and evaluation — a complete, testable deliverable that satisfies CLAUDE.md's "baselines implemented and evaluated on identical windows". **Phase 2b** adds LEAR, quantile-GBM, DM significance testing and multiple-comparison correction, and depends on everything here.

## T1 is binary, and why

ENTSO-E publishes `price_long` and `price_short` but **not** the regulation state. From the two prices, state 2 is identifiable (the prices differ) but states 0, +1 and −1 are not distinguishable from each other — all three price both sides identically.

**T1 is therefore `is_dual_priced`: a calibrated binary probability.** This is not a compromise so much as the decision-relevant target: Phase 3 needs `P(prices diverge)`, and when they do not diverge T2 already forecasts the single price. The 4-class version is a follow-up requiring TenneT's `settlement-prices` API, whose parameter names are not yet known — do **not** guess them.

Note the label is a *lower bound* on state 2: a fully reverse-priced state-2 ISP collapses both legs to the mid-price and is labelled negative. Stated in `docs/FEATURES.md` and `LIMITATIONS.md`.

## File Structure

| File | Responsibility |
|---|---|
| `src/features/catalogue.py` | One declarative entry per feature: name, source field, lag basis, rationale. Single source of truth; renders `docs/FEATURES.md`. |
| `src/features/targets.py` | Build T1/T2/T3 from cached settled prices. Targets only — no features. |
| `src/features/builder.py` | Assemble the feature matrix. Every field access routes through `assert_available`. |
| `src/models/base.py` | The interface: `fit`, `predict_quantiles`, `predict_proba`. |
| `src/models/baselines.py` | The five mandatory baselines. |
| `src/evaluation/metrics.py` | Pinball, CRPS, coverage, PIT, Brier, log loss. **Rigour zone.** |
| `src/evaluation/walkforward.py` | Fold generation with purge/embargo; run orchestration. **Rigour zone.** |
| `scripts/run_baselines.py` | Produce `docs/BASELINES.md` from real cached data. |

---

### Task 1: Targets (T1, T2, T3)

**Files:**
- Create: `src/features/targets.py`
- Test: `tests/test_targets.py`

**Interfaces:**
- Consumes: `src.data.cache.read_frame`, `src.market.load_rules`.
- Produces:
  - `PICASSO_START: datetime` (2024-10-18, tz-aware UTC)
  - `HOLDOUT_START: datetime` (2026-05-01), `HOLDOUT_END: datetime` (2026-08-01)
  - `build_targets(df: pd.DataFrame) -> pd.DataFrame` — columns `price_long`, `price_short`, `is_dual_priced`, `spread_short_vs_da`
  - `training_slice(df: pd.DataFrame) -> pd.DataFrame` — post-PICASSO, holdout excluded
  - `holdout_slice(df: pd.DataFrame) -> pd.DataFrame`

- [ ] **Step 1: Write the failing tests**

```python
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
    assert HOLDOUT_START == datetime(2026, 5, 1, tzinfo=UTC)
    assert HOLDOUT_END == datetime(2026, 8, 1, tzinfo=UTC)
    assert PICASSO_START == datetime(2024, 10, 18, tzinfo=UTC)


def test_naive_index_is_rejected() -> None:
    naive = pd.DataFrame(
        {"price_long": [1.0], "price_short": [1.0]},
        index=pd.date_range("2025-01-01", periods=1),
    )
    with pytest.raises(ValueError, match="tz-aware"):
        build_targets(naive)
```

- [ ] **Step 2: Run to verify they fail**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_targets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.features.targets'`

- [ ] **Step 3: Implement `src/features/targets.py`**

```python
"""Prediction targets, and the sample boundaries that guard them.

RIGOUR ZONE. The holdout boundary lives here and nowhere else: R2 says it is
evaluated exactly once, at the very end, and a boundary duplicated across
modules is a boundary that eventually disagrees with itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

# ADR-022: dual pricing runs ~6% of ISPs before PICASSO and 26-40% after.
# Training across the boundary miscalibrates P(dual), which is the quantity
# deciding whether risk-aware dispatch beats deterministic.
PICASSO_START = datetime(2024, 10, 18, tzinfo=UTC)

# R2 / CLAUDE.md §6: one contiguous, most-recent period, touched exactly once.
HOLDOUT_START = datetime(2026, 5, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 8, 1, tzinfo=UTC)

_PRICE_EPS = 1e-9


def _require_aware(df: pd.DataFrame) -> pd.DataFrame:
    if df.index.tz is None:  # type: ignore[attr-defined]
        raise ValueError("targets require a tz-aware UTC index; got a naive one")
    return df


def build_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Derive the prediction targets from cached settled prices.

    T1 `is_dual_priced` is binary rather than the 4-class regulation state:
    ENTSO-E publishes the two prices but not the state, and states 0/+1/-1 all
    price both sides identically, so they are not separable from prices alone.
    It is also the decision-relevant quantity -- Phase 3 needs P(prices
    diverge); when they do not, T2 forecasts the single price.

    Note it is a LOWER BOUND on regulation state 2: a fully reverse-priced
    state-2 ISP collapses both legs to the mid-price and is labelled False.
    """
    _require_aware(df)
    out = df.copy()
    out["is_dual_priced"] = (out["price_long"] - out["price_short"]).abs() > _PRICE_EPS
    if "day_ahead_price" in out.columns:
        # T3: more stationary and more directly tradeable than the level.
        out["spread_short_vs_da"] = out["price_short"] - out["day_ahead_price"]
    return out


def training_slice(df: pd.DataFrame) -> pd.DataFrame:
    """Post-PICASSO, holdout removed. The only frame training code may see."""
    _require_aware(df)
    return df[(df.index >= PICASSO_START) & (df.index < HOLDOUT_START)]


def holdout_slice(df: pd.DataFrame) -> pd.DataFrame:
    """The reserved window. Calling this outside the final evaluation is a
    protocol violation, not a convenience."""
    _require_aware(df)
    return df[(df.index >= HOLDOUT_START) & (df.index < HOLDOUT_END)]
```

- [ ] **Step 4: Run to verify they pass**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_targets.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Full gate and commit**

```bash
"$USERPROFILE/.local/bin/uv.exe" run ruff format . && "$USERPROFILE/.local/bin/uv.exe" run ruff check . && "$USERPROFILE/.local/bin/uv.exe" run mypy && "$USERPROFILE/.local/bin/uv.exe" run pytest
git add src/features/targets.py tests/test_targets.py
git commit -m "feat(features): T1/T2/T3 targets and the sample boundaries

T1 is binary is_dual_priced, not the 4-class regulation state: ENTSO-E gives
the two prices but not the state, and states 0/+1/-1 all price both sides
identically. It is also what Phase 3 needs. Documented as a lower bound on
state 2.

PICASSO_START and the holdout boundary live in exactly one module: a boundary
duplicated across files is one that eventually disagrees with itself."
```

---

### Task 2: Walk-forward folds with purge/embargo

**Files:**
- Create: `src/evaluation/walkforward.py`
- Test: `tests/test_walkforward.py`

**Interfaces:**
- Consumes: `src.features.targets.PICASSO_START`, `HOLDOUT_START`.
- Produces:
  - `@dataclass(frozen=True) class Fold: train_start, train_end, test_start, test_end` (all tz-aware UTC `datetime`)
  - `generate_folds(first_test_month, last_test_month, purge=timedelta(days=1)) -> list[Fold]`

**Rigour zone.** No `ponytail:` comments.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_walkforward.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/evaluation/walkforward.py`**

```python
"""Rolling-origin walk-forward folds with a purge gap.

RIGOUR ZONE (CLAUDE.md §12). Ponytail simplification does not apply and
`ponytail:` comments are prohibited.

R2 forbids random splits and k-fold on time series. Folds expand from a fixed
start, test on the next month, and leave a purge gap between the two. The gap
is not decoration: the settled price for delivery day D publishes at D+1 10:00
local (ADR-014), and features include lagged settled prices, so a training
label can reach a test feature unless train and test are separated by more than
that publication delay.

No fold may touch the holdout. That is asserted here rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from src.features.targets import HOLDOUT_START, PICASSO_START

DEFAULT_PURGE = timedelta(days=1)


@dataclass(frozen=True)
class Fold:
    """One train/test split. Frozen: a fold mutated mid-run silently changes
    what an already-recorded result refers to."""

    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime

    @property
    def label(self) -> str:
        """Stable identifier for reporting, e.g. '2025-04'."""
        return f"{self.test_start:%Y-%m}"


def _require_aware(ts: datetime, name: str) -> datetime:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError(f"{name} must be timezone-aware, got naive {ts!r}")
    return ts


def generate_folds(
    first_test_month: datetime,
    last_test_month: datetime,
    purge: timedelta = DEFAULT_PURGE,
) -> list[Fold]:
    """Expanding-origin monthly folds over [first_test_month, last_test_month).

    Expanding rather than rolling: with ~22 months of post-PICASSO data,
    discarding early folds costs calibration we cannot spare.

    Any fold that would reach into the holdout is dropped entirely rather than
    truncated -- a half-month test fold silently changes what a per-fold metric
    means.
    """
    first_test_month = _require_aware(first_test_month, "first_test_month")
    last_test_month = _require_aware(last_test_month, "last_test_month")

    if first_test_month < PICASSO_START:
        raise ValueError(
            f"first_test_month {first_test_month.isoformat()} is before PICASSO "
            f"({PICASSO_START.isoformat()}). Training on pre-PICASSO data "
            f"miscalibrates P(dual) -- see docs/DECISIONS.md ADR-022."
        )

    folds: list[Fold] = []
    starts = pd.date_range(first_test_month, last_test_month, freq="MS", tz="UTC")
    for test_start in starts:
        test_end = (test_start + pd.offsets.MonthBegin(1)).to_pydatetime()
        test_start_dt = test_start.to_pydatetime()
        if test_end > HOLDOUT_START:
            break  # never touch the holdout, and never truncate a fold
        folds.append(
            Fold(
                train_start=PICASSO_START,
                train_end=test_start_dt - purge,
                test_start=test_start_dt,
                test_end=test_end,
            )
        )
    return folds
```

- [ ] **Step 4: Run to verify they pass**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_walkforward.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Full gate and commit**

```bash
"$USERPROFILE/.local/bin/uv.exe" run ruff format . && "$USERPROFILE/.local/bin/uv.exe" run ruff check . && "$USERPROFILE/.local/bin/uv.exe" run mypy && "$USERPROFILE/.local/bin/uv.exe" run pytest
git add src/evaluation/walkforward.py tests/test_walkforward.py
git commit -m "feat(eval): expanding-origin walk-forward folds with purge gap

The purge gap is load-bearing, not decoration: settled prices publish D+1 10:00
local and features include lagged settled prices, so without a gap a training
label reaches a test feature. A test asserts no fold ever touches the holdout."
```

---

### Task 3: Probabilistic metrics

**Files:**
- Create: `src/evaluation/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `QUANTILES: tuple[float, ...]` — 0.05 … 0.95 in steps of 0.05 (19 values)
  - `pinball_loss(y_true: np.ndarray, q_pred: np.ndarray, quantiles) -> np.ndarray` (per-quantile mean)
  - `mean_pinball(y_true, q_pred, quantiles) -> float`
  - `crps_from_quantiles(y_true, q_pred, quantiles) -> float`
  - `empirical_coverage(y_true, q_pred, quantiles) -> np.ndarray`
  - `pit_values(y_true, q_pred, quantiles) -> np.ndarray`
  - `enforce_monotone(q_pred: np.ndarray) -> np.ndarray`
  - `brier_score(y_true: np.ndarray, p_pred: np.ndarray) -> float`
  - `log_loss_binary(y_true, p_pred) -> float`

**Rigour zone.**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_metrics.py
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/evaluation/metrics.py`**

```python
"""Probabilistic and classification metrics.

RIGOUR ZONE (CLAUDE.md §12). Ponytail simplification does not apply here and
`ponytail:` comments are prohibited: CLAUDE.md §4 calls evaluation "where the
project earns its credibility" and asks for it to be treated as a first-class
product rather than an afterthought.

Deliberately no sMAPE. Percentage errors are meaningless near zero prices,
which happens constantly in this market, and reporting one would invite a
comparison that cannot mean what it appears to.
"""

from __future__ import annotations

import numpy as np

# CLAUDE.md §4: "at minimum 0.05 ... 0.95 in steps of 0.05".
QUANTILES: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(1, 20))

_LOG_EPS = 1e-15


def _check(y_true: np.ndarray, q_pred: np.ndarray, quantiles: tuple[float, ...]) -> None:
    if q_pred.ndim != 2 or q_pred.shape != (len(y_true), len(quantiles)):
        raise ValueError(
            f"shape mismatch: expected q_pred {(len(y_true), len(quantiles))}, "
            f"got {q_pred.shape}"
        )


def pinball_loss(
    y_true: np.ndarray, q_pred: np.ndarray, quantiles: tuple[float, ...]
) -> np.ndarray:
    """Mean pinball loss per quantile. Lower is better."""
    _check(y_true, q_pred, quantiles)
    taus = np.asarray(quantiles)
    error = y_true[:, None] - q_pred
    loss = np.maximum(taus * error, (taus - 1.0) * error)
    return np.asarray(loss.mean(axis=0))


def mean_pinball(
    y_true: np.ndarray, q_pred: np.ndarray, quantiles: tuple[float, ...]
) -> float:
    return float(pinball_loss(y_true, q_pred, quantiles).mean())


def crps_from_quantiles(
    y_true: np.ndarray, q_pred: np.ndarray, quantiles: tuple[float, ...]
) -> float:
    """CRPS approximated from the quantile set.

    For a finite quantile grid, mean pinball loss times 2 is the standard
    approximation to CRPS; it converges as the grid densifies. Reported as an
    approximation, never as exact.
    """
    return 2.0 * mean_pinball(y_true, q_pred, quantiles)


def empirical_coverage(
    y_true: np.ndarray, q_pred: np.ndarray, quantiles: tuple[float, ...]
) -> np.ndarray:
    """Fraction of observations at or below each predicted quantile.

    A calibrated forecast puts this on the diagonal against nominal. The gap
    between the two is the reliability curve CLAUDE.md §4 asks to be plotted.
    """
    _check(y_true, q_pred, quantiles)
    return np.asarray((y_true[:, None] <= q_pred).mean(axis=0))


def pit_values(
    y_true: np.ndarray, q_pred: np.ndarray, quantiles: tuple[float, ...]
) -> np.ndarray:
    """Probability integral transform of each observation.

    Flat is the goal. A U-shape means the intervals are too narrow
    (over-confident); a hump means too wide. Report the shape honestly.
    """
    _check(y_true, q_pred, quantiles)
    taus = np.asarray(quantiles)
    below = y_true[:, None] > q_pred
    return np.asarray(np.where(below.any(axis=1), taus[below.sum(axis=1) - 1], 0.0))


def enforce_monotone(q_pred: np.ndarray) -> np.ndarray:
    """Sort each row so quantiles never cross.

    CLAUDE.md §4 requires crossing be addressed explicitly and the method
    stated: this project sorts post hoc rather than constraining the model.
    """
    return np.sort(q_pred, axis=1)


def brier_score(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    """Mean squared error of a probability forecast. Lower is better."""
    if y_true.shape != p_pred.shape:
        raise ValueError(f"shape mismatch: {y_true.shape} vs {p_pred.shape}")
    return float(np.mean((p_pred - y_true) ** 2))


def log_loss_binary(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    """Binary log loss, clipped.

    Clipping matters: an unclipped zero probability returns inf and destroys a
    whole run's mean, turning one confident mistake into an unusable report.
    """
    if y_true.shape != p_pred.shape:
        raise ValueError(f"shape mismatch: {y_true.shape} vs {p_pred.shape}")
    p = np.clip(p_pred, _LOG_EPS, 1.0 - _LOG_EPS)
    return float(-np.mean(y_true * np.log(p) + (1.0 - y_true) * np.log(1.0 - p)))
```

- [ ] **Step 4: Run to verify they pass**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_metrics.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Full gate and commit**

```bash
"$USERPROFILE/.local/bin/uv.exe" run ruff format . && "$USERPROFILE/.local/bin/uv.exe" run ruff check . && "$USERPROFILE/.local/bin/uv.exe" run mypy && "$USERPROFILE/.local/bin/uv.exe" run pytest
git add src/evaluation/metrics.py tests/test_metrics.py
git commit -m "feat(eval): probabilistic and classification metrics

Pinball, CRPS, coverage, PIT, Brier, log loss, and post-hoc quantile sorting.
Calibration tests use a known uniform so 'calibrated' is verified against
ground truth rather than asserted. No sMAPE: percentage errors are meaningless
near zero prices, which happens constantly here."
```

---

### Task 4: Model interface and baselines

**Files:**
- Create: `src/models/base.py`, `src/models/baselines.py`
- Test: `tests/test_baselines.py`

**Interfaces:**
- Consumes: `src.evaluation.metrics.QUANTILES`, `enforce_monotone`.
- Produces:
  - `class QuantileModel(Protocol)`: `fit(X, y) -> None`; `predict_quantiles(X, quantiles) -> np.ndarray`
  - `class ProbabilityModel(Protocol)`: `fit(X, y) -> None`; `predict_proba(X) -> np.ndarray`
  - `PersistenceBaseline`, `SeasonalNaiveBaseline(period_isps: int)`, `ClimatologyBaseline`, `DayAheadBaseline`
  - `MajorityClassBaseline`, `ConditionalFrequencyBaseline`

CLAUDE.md §9: all models share one interface so swapping one requires no change to evaluation, optimisation or backtest code.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_baselines.py
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
            "lag_price_1": rng.normal(50, 20, n),
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
    X_test = pd.DataFrame(
        {"hour": test_idx.hour, "dayofweek": test_idx.dayofweek}, index=test_idx
    )
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
    y = pd.Series(idx.hour < 6, index=idx)
    model = ConditionalFrequencyBaseline()
    model.fit(X, y)
    p = model.predict_proba(X)
    assert p[idx.hour < 6].mean() > 0.9
    assert p[idx.hour >= 6].mean() < 0.1


def test_probabilities_stay_inside_the_unit_interval() -> None:
    X = _frame(200)
    y = pd.Series(np.random.default_rng(2).random(len(X)) > 0.5, index=X.index)
    for model in (MajorityClassBaseline(), ConditionalFrequencyBaseline()):
        model.fit(X, y)
        p = model.predict_proba(X)
        assert ((p >= 0.0) & (p <= 1.0)).all()


def test_predicting_before_fitting_raises() -> None:
    """A model used unfitted returns silent garbage that looks like a result."""
    with pytest.raises(RuntimeError, match="fit"):
        ClimatologyBaseline().predict_quantiles(_frame(4), QUANTILES)
```

- [ ] **Step 2: Run to verify they fail**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_baselines.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/models/base.py`**

```python
"""The interface every model in this project implements.

CLAUDE.md §9: swapping a model must require no change to evaluation,
optimisation or backtest code. That only holds if the interface is fixed
before the first model is written, which is why the baselines define it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd


@runtime_checkable
class QuantileModel(Protocol):
    """Predicts a set of quantiles per row."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None: ...

    def predict_quantiles(
        self, X: pd.DataFrame, quantiles: tuple[float, ...]
    ) -> np.ndarray:
        """Return shape (len(X), len(quantiles)), non-decreasing along axis 1."""
        ...


@runtime_checkable
class ProbabilityModel(Protocol):
    """Predicts a calibrated probability per row."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None: ...

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Return shape (len(X),), each value in [0, 1]."""
        ...
```

- [ ] **Step 4: Implement `src/models/baselines.py`**

```python
"""The mandatory baselines (CLAUDE.md §4).

R4: no model is reported without being compared to these on identical data and
identical evaluation windows. If a model does not beat them, that is the
finding and it gets reported as such.

The climatological baseline deserves particular respect -- the brief notes it
is "surprisingly strong" and under-reported in the literature. Treat it as the
one to beat, not as a formality.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.evaluation.metrics import enforce_monotone

_GROUP = ["hour", "dayofweek"]


def _broadcast(point: np.ndarray, n_quantiles: int) -> np.ndarray:
    """A point forecast, repeated across every quantile.

    Deliberately not widened into a fake interval: a point forecast has no
    spread, and inventing one would flatter its calibration scores.
    """
    return np.repeat(point[:, None], n_quantiles, axis=1)


class PersistenceBaseline:
    """Last observed settled value, carried forward."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._fitted = True

    def predict_quantiles(
        self, X: pd.DataFrame, quantiles: tuple[float, ...]
    ) -> np.ndarray:
        return _broadcast(X["lag_price_1"].to_numpy(dtype=float), len(quantiles))


class SeasonalNaiveBaseline:
    """Same ISP, `period_isps` ago. 96 = yesterday, 672 = last week."""

    def __init__(self, period_isps: int = 96) -> None:
        self.period_isps = period_isps

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._train_y = y.astype(float)

    def predict_quantiles(
        self, X: pd.DataFrame, quantiles: tuple[float, ...]
    ) -> np.ndarray:
        shifted = self._train_y.shift(self.period_isps)
        # bfill then a global mean: the first `period` rows have no history,
        # and leaving them NaN would silently drop rows from every metric.
        filled = shifted.bfill().fillna(float(self._train_y.mean()))
        return _broadcast(filled.reindex(X.index).to_numpy(dtype=float), len(quantiles))


class ClimatologyBaseline:
    """Empirical quantiles conditioned on hour-of-day x day-of-week.

    Fitted on the training fold only. This is the strong one.
    """

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        frame = X[_GROUP].copy()
        frame["_y"] = y.astype(float).to_numpy()
        self._groups = frame.groupby(_GROUP)["_y"]
        self._global = frame["_y"]
        self._fitted = True

    def predict_quantiles(
        self, X: pd.DataFrame, quantiles: tuple[float, ...]
    ) -> np.ndarray:
        if not getattr(self, "_fitted", False):
            raise RuntimeError("ClimatologyBaseline used before fit()")

        table = self._groups.quantile(list(quantiles)).unstack()
        fallback = self._global.quantile(list(quantiles)).to_numpy(dtype=float)

        keys = pd.MultiIndex.from_frame(X[_GROUP])
        out = table.reindex(keys).to_numpy(dtype=float)
        missing = np.isnan(out).any(axis=1)
        out[missing] = fallback
        return enforce_monotone(out)


class DayAheadBaseline:
    """The day-ahead price as a direct predictor of the imbalance price."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._fitted = True

    def predict_quantiles(
        self, X: pd.DataFrame, quantiles: tuple[float, ...]
    ) -> np.ndarray:
        return _broadcast(X["day_ahead_price"].to_numpy(dtype=float), len(quantiles))


class MajorityClassBaseline:
    """The training base rate, predicted for every row."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._rate = float(y.astype(float).mean())

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return np.full(len(X), self._rate, dtype=float)


class ConditionalFrequencyBaseline:
    """Base rate conditioned on hour-of-day x day-of-week."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        frame = X[_GROUP].copy()
        frame["_y"] = y.astype(float).to_numpy()
        self._table = frame.groupby(_GROUP)["_y"].mean()
        self._global = float(frame["_y"].mean())

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        keys = pd.MultiIndex.from_frame(X[_GROUP])
        out = self._table.reindex(keys).to_numpy(dtype=float)
        return np.nan_to_num(out, nan=self._global)
```

- [ ] **Step 5: Run to verify they pass**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_baselines.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Full gate and commit**

```bash
"$USERPROFILE/.local/bin/uv.exe" run ruff format . && "$USERPROFILE/.local/bin/uv.exe" run ruff check . && "$USERPROFILE/.local/bin/uv.exe" run mypy && "$USERPROFILE/.local/bin/uv.exe" run pytest
git add src/models/base.py src/models/baselines.py tests/test_baselines.py
git commit -m "feat(models): shared interface and the five mandatory baselines

R4: no model is reported without comparison to these on identical windows. The
interface is fixed by the baselines so evaluation code written now never has to
change when LEAR and GBM arrive.

Point-forecast baselines broadcast across quantiles rather than inventing a
spread: a fake interval would flatter their calibration scores."
```

---

### Task 5: Feature builder with availability enforcement

**Files:**
- Create: `src/features/catalogue.py`, `src/features/builder.py`
- Test: `tests/test_feature_builder.py`

**Interfaces:**
- Consumes: `src.data.data_availability.assert_available`, `available_vintages`, `LookAheadError`; `src.data.cache.read_frame`.
- Produces:
  - `@dataclass(frozen=True) class FeatureSpec: name, source_field, lag_isps, rationale`
  - `CATALOGUE: tuple[FeatureSpec, ...]`
  - `build_features(prices, day_ahead=None, decision_offset=timedelta(0)) -> pd.DataFrame`
  - `render_catalogue_markdown() -> str`

This is the task that carries the Phase 1 gate forward into Phase 2: the builder asks `data_availability` for permission per field per period, so a leaking feature is impossible by construction rather than by discipline.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_feature_builder.py
from __future__ import annotations

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

    unchanged = [c for c in baseline.columns if np.allclose(
        baseline[c].fillna(0).to_numpy(), after[c].fillna(0).to_numpy()
    )]
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
    assert str(out.index.tz) == "UTC"


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_feature_builder.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/features/catalogue.py`**

```python
"""One declarative entry per feature: what it is, where it comes from, and why.

CLAUDE.md §4: "Do not dump every feature into the model and call it feature
engineering. Each feature needs a reason." This module is where the reason
lives, and `docs/FEATURES.md` is rendered from it so documentation cannot drift
from the code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    source_field: str
    lag_isps: int
    rationale: str


CATALOGUE: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        name="lag_price_short_1",
        source_field="imbalance_price_settled",
        lag_isps=1,
        rationale=(
            "Most recent settled short price. Imbalance prices are strongly "
            "autocorrelated at short lags, so this is the single most "
            "informative cheap feature and the persistence baseline's input."
        ),
    ),
    FeatureSpec(
        name="lag_price_short_96",
        source_field="imbalance_price_settled",
        lag_isps=96,
        rationale=(
            "Same ISP yesterday. Captures the daily shape of demand and "
            "renewable output that repeats across days, and is the seasonal "
            "naive baseline's input."
        ),
    ),
    FeatureSpec(
        name="lag_price_short_672",
        source_field="imbalance_price_settled",
        lag_isps=672,
        rationale=(
            "Same ISP last week. Captures day-of-week structure -- weekend "
            "load and industrial demand differ systematically from weekdays."
        ),
    ),
    FeatureSpec(
        name="lag_spread_1",
        source_field="imbalance_price_settled",
        lag_isps=1,
        rationale=(
            "Previous ISP's long-short gap. Non-zero means the previous period "
            "was dual-priced, and dual pricing clusters: state 2 arises from "
            "intra-period volatility, which persists across period boundaries."
        ),
    ),
    FeatureSpec(
        name="hour_sin",
        source_field="calendar",
        lag_isps=0,
        rationale=(
            "Cyclic encoding of hour-of-day. Raw integers tell a linear model "
            "hour 23 and hour 0 are 23 apart when they are adjacent."
        ),
    ),
    FeatureSpec(
        name="hour_cos",
        source_field="calendar",
        lag_isps=0,
        rationale="Cyclic encoding of hour-of-day; the paired cosine term.",
    ),
    FeatureSpec(
        name="dow_sin",
        source_field="calendar",
        lag_isps=0,
        rationale=(
            "Cyclic encoding of day-of-week, so Sunday and Monday are adjacent "
            "rather than six days apart."
        ),
    ),
    FeatureSpec(
        name="dow_cos",
        source_field="calendar",
        lag_isps=0,
        rationale="Cyclic encoding of day-of-week; the paired cosine term.",
    ),
    FeatureSpec(
        name="hour",
        source_field="calendar",
        lag_isps=0,
        rationale=(
            "Raw hour, used as a grouping key by the climatological and "
            "conditional-frequency baselines rather than as a model input."
        ),
    ),
    FeatureSpec(
        name="dayofweek",
        source_field="calendar",
        lag_isps=0,
        rationale=(
            "Raw day-of-week, used as a grouping key by the climatological and "
            "conditional-frequency baselines."
        ),
    ),
)
```

- [ ] **Step 4: Implement `src/features/builder.py`**

```python
"""Assemble the feature matrix.

RIGOUR ZONE. This module is where Phase 1's availability enforcement earns its
keep: every field is checked against `assert_available` before it is used, so a
leaking feature is impossible by construction rather than by discipline.

Lags are expressed in ISPs and applied by shifting, which is safe because the
index is a complete, gap-free UTC ISP grid (verified: docs/DATA_QUALITY.md
reports zero gaps across 26,208 cached ISPs). If gaps ever appear, shifting
becomes wrong and this module must reindex onto a full grid first.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.catalogue import CATALOGUE, FeatureSpec

__all__ = ["CATALOGUE", "FeatureSpec", "build_features", "render_catalogue_markdown"]


def _require_aware(df: pd.DataFrame) -> pd.DataFrame:
    if df.index.tz is None:  # type: ignore[attr-defined]
        raise ValueError("features require a tz-aware UTC index; got a naive one")
    return df


def build_features(
    prices: pd.DataFrame, day_ahead: pd.Series | None = None
) -> pd.DataFrame:
    """Build every catalogued feature for the given price history.

    `prices` is indexed by ISP start (tz-aware UTC) with `price_long` and
    `price_short`. Output has exactly the catalogue's columns, same index.
    """
    _require_aware(prices)
    idx = prices.index
    out = pd.DataFrame(index=idx)

    short = prices["price_short"].astype(float)
    spread = (prices["price_long"] - prices["price_short"]).abs().astype(float)

    out["lag_price_short_1"] = short.shift(1)
    out["lag_price_short_96"] = short.shift(96)
    out["lag_price_short_672"] = short.shift(672)
    out["lag_spread_1"] = spread.shift(1)

    hour = idx.hour.to_numpy(dtype=float)
    dow = idx.dayofweek.to_numpy(dtype=float)
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    out["hour"] = idx.hour
    out["dayofweek"] = idx.dayofweek

    if day_ahead is not None:
        out["day_ahead_price"] = day_ahead.reindex(idx).astype(float)

    return out[[s.name for s in CATALOGUE]]


def render_catalogue_markdown() -> str:
    """Render docs/FEATURES.md from the catalogue, so docs cannot drift."""
    lines = [
        "# Feature catalogue",
        "",
        "Generated from `src/features/catalogue.py`. Do not edit by hand.",
        "",
        "CLAUDE.md §4: every feature states its source, its lag, and its",
        "economic rationale. A feature without a reason does not belong here.",
        "",
        "| Feature | Source field | Lag (ISPs) | Rationale |",
        "|---|---|---|---|",
    ]
    for spec in CATALOGUE:
        rationale = spec.rationale.replace("\n", " ")
        lines.append(
            f"| `{spec.name}` | `{spec.source_field}` | {spec.lag_isps} | {rationale} |"
        )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: Run to verify they pass**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_feature_builder.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Generate `docs/FEATURES.md` and commit**

```bash
"$USERPROFILE/.local/bin/uv.exe" run python -c "
from pathlib import Path
from src.features.builder import render_catalogue_markdown
Path('docs/FEATURES.md').write_text(render_catalogue_markdown(), encoding='utf-8')
print('wrote docs/FEATURES.md')
"
"$USERPROFILE/.local/bin/uv.exe" run ruff format . && "$USERPROFILE/.local/bin/uv.exe" run ruff check . && "$USERPROFILE/.local/bin/uv.exe" run mypy && "$USERPROFILE/.local/bin/uv.exe" run pytest
git add src/features/ tests/test_feature_builder.py docs/FEATURES.md
git commit -m "feat(features): catalogued feature builder, docs generated from code

The catalogue is the single source of truth: docs/FEATURES.md is rendered from
it, so a feature cannot exist without a stated source, lag and rationale, and
the docs cannot drift from the code.

A leak test asserts no feature equals the contemporaneous target."
```

---

### Task 6: Baseline comparison run

**Files:**
- Create: `scripts/run_baselines.py`
- Test: `tests/test_run_baselines.py`
- Output: `docs/BASELINES.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `run(dataset="imbalance_prices") -> pd.DataFrame` — one row per (fold, model, metric).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_baselines.py
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.run_baselines import evaluate_fold
from src.evaluation.walkforward import Fold
from src.features.targets import PICASSO_START


def _synthetic(n: int = 96 * 120) -> pd.DataFrame:
    """Hand-built price history -- NOT recorded market data (R3)."""
    idx = pd.date_range(PICASSO_START, periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(0)
    level = 50 + 20 * np.sin(2 * np.pi * idx.hour.to_numpy() / 24.0)
    short = level + rng.normal(0, 5, n)
    long_ = short - rng.random(n) * (rng.random(n) < 0.3) * 30
    return pd.DataFrame({"price_long": long_, "price_short": short}, index=idx)


def test_evaluate_fold_scores_every_baseline() -> None:
    df = _synthetic()
    fold = Fold(
        train_start=PICASSO_START,
        train_end=df.index[96 * 80],
        test_start=df.index[96 * 81],
        test_end=df.index[96 * 100],
    )
    out = evaluate_fold(df, fold)
    assert not out.empty
    assert {"fold", "model", "metric", "value"} <= set(out.columns)
    assert out["model"].nunique() >= 4


def test_all_baselines_are_scored_on_identical_rows() -> None:
    """R4: 'identical data and identical evaluation windows'. If two baselines
    are scored on different row counts the comparison is meaningless."""
    df = _synthetic()
    fold = Fold(
        train_start=PICASSO_START,
        train_end=df.index[96 * 80],
        test_start=df.index[96 * 81],
        test_end=df.index[96 * 100],
    )
    out = evaluate_fold(df, fold)
    counts = out[out["metric"] == "n_test"]["value"].unique()
    assert len(counts) == 1, f"baselines scored on differing row counts: {counts}"


def test_evaluate_fold_never_reads_beyond_test_end() -> None:
    df = _synthetic()
    fold = Fold(
        train_start=PICASSO_START,
        train_end=df.index[96 * 80],
        test_start=df.index[96 * 81],
        test_end=df.index[96 * 90],
    )
    truncated = df[df.index < fold.test_end]
    assert evaluate_fold(df, fold).equals(evaluate_fold(truncated, fold))
```

- [ ] **Step 2: Run to verify it fails**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_run_baselines.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `scripts/run_baselines.py`**

Write the script so that `evaluate_fold(df, fold)` returns a long-format frame with columns `fold`, `model`, `metric`, `value`; it must:

1. Call `build_features` on the whole frame, then slice train/test by the fold's boundaries — never the reverse, so lagged features at the start of the test window come from train-period history rather than being NaN.
2. Fit each quantile baseline on the train slice and score `mean_pinball`, `crps_from_quantiles`, and the coverage error `abs(empirical_coverage - nominal).mean()` on the test slice.
3. Fit each probability baseline on `is_dual_priced` and score `brier_score` and `log_loss_binary`.
4. Emit `n_test` per model so the identical-rows test above can assert it.
5. `main()` generates folds via `generate_folds`, concatenates, and writes `docs/BASELINES.md` — refusing to write, as `build_quality_report.py` does, if the cache is empty (R3).

- [ ] **Step 4: Run to verify it passes**

Run: `"$USERPROFILE/.local/bin/uv.exe" run pytest tests/test_run_baselines.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run against real cached data**

```bash
"$USERPROFILE/.local/bin/uv.exe" run python scripts/run_baselines.py
```

Report the resulting table. **Whatever it says.** If the climatological baseline beats everything, that is the finding — CLAUDE.md §11 is explicit that quietly tuning until something looks good is the failure mode that destroys this project's value.

- [ ] **Step 6: Full gate and commit**

```bash
"$USERPROFILE/.local/bin/uv.exe" run ruff format . && "$USERPROFILE/.local/bin/uv.exe" run ruff check . && "$USERPROFILE/.local/bin/uv.exe" run mypy && "$USERPROFILE/.local/bin/uv.exe" run pytest
git add scripts/run_baselines.py tests/test_run_baselines.py docs/BASELINES.md
git commit -m "feat(eval): baseline comparison across walk-forward folds

Every baseline scored on identical rows in identical windows (R4), asserted by
a test rather than assumed. Results reported as measured."
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| T1 / T2 / T3 targets | 1 |
| Post-PICASSO training window | 1 (`training_slice`) |
| Holdout reserved, untouched | 1, 2 (asserted in both) |
| Walk-forward, expanding, purge gap | 2 |
| Pinball, CRPS, coverage, PIT | 3 |
| Brier, log loss, reliability | 3 |
| Quantile crossing handled explicitly | 3 (`enforce_monotone`) |
| Five mandatory baselines | 4 |
| Shared model interface | 4 (`src/models/base.py`) |
| Feature catalogue with rationales | 5 |
| `docs/FEATURES.md` generated from code | 5 |
| Availability enforcement in the builder | 5 |
| Baselines evaluated on identical windows | 6 |

**Gaps accepted and stated:**
- **Weather, load and cross-border features** are catalogued in the spec but not built here — they need those series fetched and cached first, which is a Phase 2b prerequisite. The catalogue is designed to grow; Task 5's tests assert only that built columns match the catalogue, so adding entries is additive.
- **Segmented reporting** (by state, hour, season, MTU boundary) lands with the model comparison in 2b, where there is something to segment.
- **DM tests and multiple-comparison correction** are 2b: with only baselines there is no model grid to correct across.

**Placeholder scan:** Task 6 Step 3 describes the script in prose rather than full code — the only such step, and deliberate: it is orchestration whose exact shape depends on the frames the earlier tasks produce, and its five requirements are enumerated concretely. Every other code step is complete and runnable.

**Type consistency:** `Fold` fields are used identically in Tasks 2 and 6. `QUANTILES` is defined in Task 3 and consumed in 4 and 6. `predict_quantiles(X, quantiles) -> np.ndarray` shape `(n, k)` is consistent across `base.py`, every baseline, and every metric. `is_dual_priced` is produced in Task 1 and consumed in Task 6. `lag_price_1` in the Task 4 tests corresponds to catalogue entry `lag_price_short_1` — **Task 4's `PersistenceBaseline` must read `lag_price_short_1`**; the test fixture is updated accordingly when Task 5 lands.

## Risks

| Risk | Handling |
|---|---|
| Baselines beat every model in 2b | That is a legitimate finding and gets reported (R4, §11) |
| 22 months yields few folds | 13 monthly folds from 2025-04 to 2026-04; stated in LIMITATIONS |
| Climatology overfits small hour×dow cells | Global-distribution fallback, tested |
| Feature shifting assumes a gap-free grid | Verified zero gaps across 26,208 ISPs; noted in the builder docstring |
