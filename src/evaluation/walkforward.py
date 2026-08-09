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
