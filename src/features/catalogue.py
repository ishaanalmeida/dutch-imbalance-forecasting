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
    FeatureSpec(
        name="day_ahead_price",
        source_field="day_ahead_price",
        lag_isps=0,
        rationale=(
            "The day-ahead auction price for this ISP, published by ~13:00 on "
            "D-1 -- fully available before delivery day D begins, so no extra "
            "shift is needed. Feeds the day-ahead baseline and T3 (spread vs "
            "day-ahead). Always present in the output (NaN when no day-ahead "
            "series is supplied) so the column set never depends on which "
            "optional inputs the caller happened to pass."
        ),
    ),
)
