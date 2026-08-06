"""Data quality diagnostics.

RIGOUR ZONE (CLAUDE.md §12) -- this feeds docs/DATA_QUALITY.md and the
segmented-reporting machinery the project's credibility rests on. Ponytail
simplification does not apply here and `ponytail:` shortcut comments are
prohibited (see this repo's CLAUDE.md §12).

All checks operate on the frame's UTC index, which is what makes them correct
across DST boundaries -- see src/data/timebase.py's module docstring on why a
local-calendar check silently breaks twice a year.

TOKEN-GATED (2026-08-06): no ENTSO-E token or TenneT registration exists yet,
so these functions are built and unit-tested against synthetic frames
constructed in tests/test_quality.py. docs/DATA_QUALITY.md is NOT generated
from real numbers here -- see scripts/build_quality_report.py, which refuses
to write it until real cached data exists (CLAUDE.md R3).
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from src.data.timebase import ISP_MINUTES

_STEP = pd.Timedelta(minutes=ISP_MINUTES)

_STRUCTURAL_BREAK_COLUMNS = [
    "date",
    "what",
    "state2_share_before",
    "state2_share_after",
    "n_before",
    "n_after",
]


def gap_report(df: pd.DataFrame) -> pd.DataFrame:
    """Contiguous runs of missing ISPs in a UTC-indexed frame.

    Compares consecutive *present* timestamps only, so a gap at the very
    start or end of the frame (relative to some external expected range) is
    invisible by construction -- this function has no notion of an expected
    range, only of gaps between rows that are actually present.
    """
    if df.empty:
        return pd.DataFrame(columns=["gap_start", "gap_end", "missing_isps"])
    idx = cast(pd.DatetimeIndex, df.index.sort_values().unique())
    deltas = pd.Series(idx).diff()
    breaks = deltas[deltas > _STEP]
    rows = [
        {
            "gap_start": idx[i - 1] + _STEP,
            "gap_end": idx[i],
            "missing_isps": int(deltas.iloc[i] / _STEP) - 1,
        }
        for i in breaks.index
    ]
    return pd.DataFrame(rows, columns=["gap_start", "gap_end", "missing_isps"])


def duplicate_report(df: pd.DataFrame) -> pd.DataFrame:
    """Every timestamp that appears more than once in the index, with its
    repeat count."""
    dupes = df.index[df.index.duplicated(keep=False)].unique()
    return pd.DataFrame(
        {"timestamp": dupes, "count": [int((df.index == t).sum()) for t in dupes]}
    )


def regulation_state_distribution(df: pd.DataFrame, by: str = "hour") -> pd.DataFrame:
    """Share of each regulation state, grouped by hour / month / year.

    Every row sums to 1.0, including a group where only a single state was
    ever observed (the single non-zero column carries the whole share).

    `by="month"` deliberately groups on a formatted string
    (``index.strftime("%Y-%m")``) rather than ``index.to_period("M")``: the
    latter silently drops tz on a tz-aware index and emits a UserWarning (see
    src/data/cache.py's write_frame, which hit the same issue) -- this
    function's whole point is to be trustworthy on a UTC-indexed frame, so it
    must not carry a hidden warning-worthy tz drop of its own.
    """
    idx = cast(pd.DatetimeIndex, df.index)
    key: pd.Index[Any]
    if by == "hour":
        key = idx.hour
    elif by == "month":
        key = pd.Index(idx.strftime("%Y-%m"))
    elif by == "year":
        key = idx.year
    else:
        raise ValueError(f"by must be one of 'hour', 'month', 'year'; got {by!r}")

    # groupby(...).size() is typed DataFrame | Series[int] by pandas-stubs
    # (a conservative over-approximation for a DataFrameGroupBy); at runtime
    # it is always a Series here since we group a Series-shaped key pair.
    # Cast so `.unstack()` resolves to its single-return-type Series overload
    # instead of DataFrame's union overload, which is what actually produces
    # a plain DataFrame instead of DataFrame | Series below.
    sizes = cast("pd.Series[int]", df.groupby([key, df["regulation_state"]]).size())
    counts = sizes.unstack(fill_value=0)
    return counts.div(counts.sum(axis=1), axis=0)


def structural_break_check(df: pd.DataFrame) -> pd.DataFrame:
    """State-2 frequency before vs after each structural break in config.

    Specifically tests the ADR-007 prediction (docs/DECISIONS.md): state 2
    should become MORE frequent after 2026-02-03, when the state-determination
    input changed from the 1-minute to the 12-second balance delta (15 -> 75
    samples per ISP), with no change in the physical system. Exact
    monotonicity of the intra-ISP series is strictly less likely over more
    samples, so a rise in state-2 share is the predicted, falsifiable outcome.

    This function reports state2_share_before/after PLAINLY, whichever way it
    goes -- it does not editorialise. If state-2 share does not rise at
    2026-02-03 once real data lands, the reasoning in DOMAIN_NOTES.md Q7 is
    wrong and must be corrected, not the report.

    A break the frame does not span on either side (no data strictly before
    it, or no data at or after it) is skipped rather than raising, since
    "before vs after" is meaningless without both sides.
    """
    from src.market import load_rules

    rows = []
    for brk in load_rules()["structural_breaks"]:
        when = pd.Timestamp(str(brk["date"])).tz_localize("UTC")
        before, after = df[df.index < when], df[df.index >= when]
        if before.empty or after.empty:
            continue
        rows.append(
            {
                "date": brk["date"],
                "what": brk["what"],
                "state2_share_before": float((before["regulation_state"] == 2).mean()),
                "state2_share_after": float((after["regulation_state"] == 2).mean()),
                "n_before": len(before),
                "n_after": len(after),
            }
        )
    return pd.DataFrame(rows, columns=_STRUCTURAL_BREAK_COLUMNS)
