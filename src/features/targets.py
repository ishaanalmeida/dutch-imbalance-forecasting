"""Prediction targets, and the sample boundaries that guard them.

RIGOUR ZONE. The holdout boundary lives here and nowhere else: R2 says it is
evaluated exactly once, at the very end, and a boundary duplicated across
modules is a boundary that eventually disagrees with itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.data.timebase import require_aware_index as _require_aware

# ADR-022: dual pricing runs ~6% of ISPs before PICASSO and 26-40% after.
# Training across the boundary miscalibrates P(dual), which is the quantity
# deciding whether risk-aware dispatch beats deterministic.
PICASSO_START = datetime(2024, 10, 18, tzinfo=UTC)

# R2 / CLAUDE.md §6: one contiguous, most-recent period, touched exactly once.
HOLDOUT_START = datetime(2026, 5, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 8, 1, tzinfo=UTC)

_PRICE_EPS = 1e-9


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
    _require_aware(df, "targets")
    out = df.copy()
    out["is_dual_priced"] = (out["price_long"] - out["price_short"]).abs() > _PRICE_EPS
    if "day_ahead_price" in out.columns:
        # T3: more stationary and more directly tradeable than the level.
        out["spread_short_vs_da"] = out["price_short"] - out["day_ahead_price"]
    return out


def training_slice(df: pd.DataFrame) -> pd.DataFrame:
    """Post-PICASSO, holdout removed. The only frame training code may see."""
    _require_aware(df, "targets")
    return df[(df.index >= PICASSO_START) & (df.index < HOLDOUT_START)]


def holdout_slice(df: pd.DataFrame) -> pd.DataFrame:
    """The reserved window. Calling this outside the final evaluation is a
    protocol violation, not a convenience."""
    _require_aware(df, "targets")
    return df[(df.index >= HOLDOUT_START) & (df.index < HOLDOUT_END)]
