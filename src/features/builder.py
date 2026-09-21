"""Assemble the feature matrix.

RIGOUR ZONE. This module carries Phase 1's availability enforcement into
Phase 2 -- see docs/DECISIONS.md ADR-023. Every settled-price-derived lag is
masked to NaN per row, per `src.data.data_availability.is_available`, at the
canonical ISP-start decision time (ADR-005): `imbalance_price_settled`
publishes once daily at D+1 10:00 (config/market_rules.yaml), so a naive
one-ISP lag is *never* available (removed from the catalogue entirely) and
`lag_price_short_96` (yesterday, same ISP) is only available for decisions
taken after ~10:00 local -- roughly 43% of ISPs are masked. NaN is the
truthful encoding of "not yet known", not an error: unavailability is a
legitimate per-row state, so masking a subset of rows never raises. A lag
unavailable for EVERY row of a multi-day window is not a per-row state, it is
a catalogue entry naming a lag the publication rule can never satisfy --
`_masked_lag` raises on that case (ADR-025), which is exactly what
`lag_price_short_1` turned out to be (ADR-023). A catalogue entry naming a
`source_field` that `data_availability` has no basis to serve at all
(`UnresolvedLagError`) is a different, genuine bug and is left to propagate.

Lags are expressed in ISPs and applied by shifting, which is safe because the
index is a complete, gap-free UTC ISP grid (verified: docs/DATA_QUALITY.md
reports zero gaps across 26,208 cached ISPs). If gaps ever appear, shifting
becomes wrong -- shift(96) silently means "96 rows back", not "24 hours back"
-- so `_require_gapfree_grid` below guards the assumption rather than letting
it fail silently.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import numpy as np
import pandas as pd

from src.data.data_availability import is_available
from src.data.quality import gap_report
from src.data.timebase import ISP_MINUTES
from src.data.timebase import require_aware_index as _require_aware
from src.features.catalogue import CATALOGUE, FeatureSpec

__all__ = ["CATALOGUE", "FeatureSpec", "build_features", "render_catalogue_markdown"]

_SETTLED = "imbalance_price_settled"
_ISPS_PER_DAY = 24 * 60 // ISP_MINUTES


def _require_gapfree_grid(idx: pd.DatetimeIndex) -> None:
    """Guard the shifting-is-safe assumption documented above.

    A gap turns `shift(96)` into "96 rows back" rather than "24 hours back" --
    silently a different, wrong feature, with no error to notice. Cheap to
    check up front; expensive to discover as an unexplained model edge later.
    Delegates the actual gap arithmetic to `src.data.quality.gap_report`
    rather than re-deriving it, so a correction there (e.g. for duplicate
    timestamps) is inherited here too (docs/DECISIONS.md ADR-025).
    """
    if not gap_report(pd.DataFrame(index=idx)).empty:
        raise ValueError(
            f"features require a gap-free {ISP_MINUTES}-minute ISP grid; "
            "found irregular spacing in the price index"
        )


def _availability_mask(idx: pd.DatetimeIndex, field: str, lag_isps: int) -> pd.Series[bool]:
    """True where `field`, `lag_isps` ISPs before each row's own ISP, was
    genuinely retrievable strictly before that row's decision time (ISP
    start, ADR-005). One `is_available` call per row: cheap relative to the
    dataset sizes here, and the honest way to answer a question whose answer
    depends on each row's own wall-clock date.

    Lets `UnresolvedLagError` propagate rather than catching it: a catalogue
    entry naming a field `data_availability` refuses outright is a catalogue
    bug, not a per-row state to mask away.
    """
    step = timedelta(minutes=ISP_MINUTES)
    mask = [
        is_available(field, (ts - lag_isps * step).to_pydatetime(), ts.to_pydatetime())
        for ts in idx
    ]
    return pd.Series(mask, index=idx, dtype=bool)


def _masked_lag(
    series: pd.Series[float],
    mask: pd.Series[bool],
    field: str,
    lag_isps: int,
) -> pd.Series[float]:
    """`series` shifted by `lag_isps`, with rows the decision could not yet
    see forced to NaN on top of whatever `shift` already leaves NaN.

    Raises if `mask` is False for every one of a multi-day window: that is
    not a per-row state, it is the catalogue naming a lag the field's
    publication rule can never satisfy for any decision time -- exactly what
    `lag_price_short_1` turned out to be (ADR-023), caught there only by a
    human noticing an all-NaN column. Raising here makes the "impossible by
    construction" guarantee real instead of test-dependent (ADR-025).
    """
    if len(mask) >= _ISPS_PER_DAY and not bool(mask.any()):
        raise ValueError(
            f"{field!r} at lag {lag_isps} ISPs is unavailable for every one of "
            f"{len(mask)} rows spanning at least a day -- this lag is "
            "structurally impossible under the field's publication rule, not "
            "a per-row state. Remove it from the catalogue or fix the lag."
        )
    return series.shift(lag_isps).where(mask)


def build_features(prices: pd.DataFrame, day_ahead: pd.Series[Any] | None = None) -> pd.DataFrame:
    """Build every catalogued feature for the given price history.

    `prices` is indexed by ISP start (tz-aware UTC) with `price_long` and
    `price_short`. Output has exactly the catalogue's columns, same index.
    `day_ahead_price` is always emitted (NaN where `day_ahead` is not
    supplied) so the column set never depends on which optional inputs the
    caller happened to pass -- see docs/DECISIONS.md ADR-023.
    """
    _require_aware(prices)
    idx = cast(pd.DatetimeIndex, prices.index)
    _require_gapfree_grid(idx)
    out = pd.DataFrame(index=idx)

    short = prices["price_short"].astype(float)
    spread = (prices["price_long"] - prices["price_short"]).abs().astype(float)

    # lag_price_short_96 and lag_spread_96 share the same (field, lag) pair,
    # so they share the same availability mask -- computed once and reused
    # rather than re-running the per-row is_available loop twice (ADR-025).
    mask_96 = _availability_mask(idx, _SETTLED, 96)
    out["lag_price_short_96"] = _masked_lag(short, mask_96, _SETTLED, 96)
    out["lag_price_short_192"] = _masked_lag(
        short, _availability_mask(idx, _SETTLED, 192), _SETTLED, 192
    )
    out["lag_price_short_freshest"] = out["lag_price_short_96"].where(
        out["lag_price_short_96"].notna(), out["lag_price_short_192"]
    )
    out["lag_price_short_672"] = _masked_lag(
        short, _availability_mask(idx, _SETTLED, 672), _SETTLED, 672
    )
    out["lag_spread_96"] = _masked_lag(spread, mask_96, _SETTLED, 96)

    hour_int, dow_int = idx.hour, idx.dayofweek
    hour, dow = hour_int.to_numpy(dtype=float), dow_int.to_numpy(dtype=float)
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    out["hour"] = hour_int
    out["dayofweek"] = dow_int

    out["day_ahead_price"] = (
        day_ahead.reindex(idx).astype(float) if day_ahead is not None else np.nan
    )

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
        lines.append(f"| `{spec.name}` | `{spec.source_field}` | {spec.lag_isps} | {rationale} |")
    return "\n".join(lines) + "\n"
