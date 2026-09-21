"""Canonical time base. UTC internally, Europe/Amsterdam only at presentation.

DST is the classic silent failure in energy pipelines: a 23-hour and a 25-hour
day occur every year, and code that assumes 96 ISPs per day is wrong twice a
year in ways that do not raise. Everything here is UTC-first for that reason.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pandas as pd

from src.market import load_rules

ISP_MINUTES: int = int(load_rules()["isp"]["length_minutes"])
LOCAL_TZ = ZoneInfo(load_rules()["meta"]["timezone_presentation"])
UTC = ZoneInfo("UTC")


def _require_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError(f"timestamp must be timezone-aware, got naive {ts!r}")
    return ts


def require_aware_index(df: pd.DataFrame, label: str = "index") -> pd.DataFrame:
    """Shared tz-aware guard for a DataFrame's DatetimeIndex.

    The single home for a check src/features/builder.py and
    src/features/targets.py each reimplemented independently -- a guard
    duplicated across modules can drift when one copy is tightened and the
    other is forgotten (docs/DECISIONS.md ADR-025).
    """
    if cast(pd.DatetimeIndex, df.index).tz is None:
        raise ValueError(f"{label} require a tz-aware UTC index; got a naive one")
    return df


def isp_start_of(ts: datetime) -> datetime:
    """Floor a timestamp to the start of the ISP containing it, in UTC."""
    ts = _require_aware(ts).astimezone(UTC)
    discard = (ts.minute % ISP_MINUTES) * 60 + ts.second
    return (ts - timedelta(seconds=discard)).replace(microsecond=0)


def isp_index(start: datetime, end: datetime) -> pd.DatetimeIndex:
    """UTC ISP starts over the half-open interval [start, end)."""
    return pd.date_range(
        start=isp_start_of(start),
        end=isp_start_of(end),
        freq=f"{ISP_MINUTES}min",
        tz="UTC",
        inclusive="left",
    )


def isps_in_local_day(local_date: date) -> pd.DatetimeIndex:
    """Every ISP in an Amsterdam calendar day, as UTC starts.

    Returns 92 on the spring-forward day and 100 on the autumn-back day. The
    repeated local hour yields two distinct UTC instants, which is why this is
    computed by converting local midnight boundaries to UTC rather than by
    adding 24 hours.
    """
    day_start = datetime.combine(local_date, datetime.min.time(), tzinfo=LOCAL_TZ)
    next_day = datetime.combine(
        local_date + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_TZ
    )
    return isp_index(day_start.astimezone(UTC), next_day.astimezone(UTC))


def to_local(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Presentation only. Never use the result as a join key."""
    if idx.tz is None:
        raise ValueError("refusing to localise a naive index; supply tz-aware input")
    return idx.tz_convert(LOCAL_TZ)


def to_utc(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None:
        raise ValueError("refusing to localise a naive index; supply tz-aware input")
    return idx.tz_convert("UTC")
