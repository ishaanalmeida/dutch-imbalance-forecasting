from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from src.data.timebase import (
    ISP_MINUTES,
    isp_index,
    isp_start_of,
    isps_in_local_day,
    to_local,
    to_utc,
)


def test_isp_length_comes_from_config() -> None:
    assert ISP_MINUTES == 15


def test_normal_local_day_has_96_isps() -> None:
    assert len(isps_in_local_day(date(2026, 6, 17))) == 96


def test_spring_forward_day_has_92_isps() -> None:
    """29 March 2026: Europe/Amsterdam loses an hour. 23 hours = 92 ISPs."""
    idx = isps_in_local_day(date(2026, 3, 29))
    assert len(idx) == 92


def test_autumn_back_day_has_100_isps() -> None:
    """25 October 2026: Europe/Amsterdam repeats an hour. 25 hours = 100 ISPs."""
    idx = isps_in_local_day(date(2026, 10, 25))
    assert len(idx) == 100


def test_all_isp_indices_are_utc_and_unique() -> None:
    """The repeated local hour must NOT collapse: 02:00-03:00 CEST and CET are
    distinct instants and must appear as distinct UTC timestamps."""
    idx = isps_in_local_day(date(2026, 10, 25))
    assert str(idx.tz) == "UTC"
    assert idx.is_unique
    assert idx.is_monotonic_increasing


def test_local_conversion_marks_the_repeated_hour_distinctly() -> None:
    idx = isps_in_local_day(date(2026, 10, 25))
    local = to_local(idx)
    offsets = {t.utcoffset() for t in local}
    assert len(offsets) == 2, "both CEST (+02:00) and CET (+01:00) must appear"


def test_isp_start_floors_to_quarter_hour() -> None:
    ts = datetime(2026, 6, 17, 14, 37, 41, tzinfo=UTC)
    assert isp_start_of(ts) == datetime(2026, 6, 17, 14, 30, tzinfo=UTC)


def test_isp_start_is_idempotent_on_a_boundary() -> None:
    ts = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
    assert isp_start_of(ts) == ts


def test_naive_datetime_is_rejected() -> None:
    """Naive timestamps are the classic silent-DST-bug vector."""
    with pytest.raises(ValueError, match="timezone-aware"):
        isp_start_of(datetime(2026, 6, 17, 14, 37))


def test_isp_index_is_left_closed() -> None:
    idx = isp_index(
        datetime(2026, 6, 17, 0, 0, tzinfo=UTC),
        datetime(2026, 6, 17, 1, 0, tzinfo=UTC),
    )
    assert len(idx) == 4
    assert idx[0] == pd.Timestamp("2026-06-17T00:00Z")
    assert idx[-1] == pd.Timestamp("2026-06-17T00:45Z")


def test_to_utc_converts_aware_index() -> None:
    """to_utc() converts a tz-aware index to UTC."""
    idx = isps_in_local_day(date(2026, 6, 17))
    utc_idx = to_utc(idx)
    assert str(utc_idx.tz) == "UTC"
    assert utc_idx.is_unique


def test_to_utc_rejects_naive_index() -> None:
    """to_utc() rejects a naive (tz-unaware) index."""
    idx = pd.date_range("2026-06-17", periods=4, freq="15min")
    with pytest.raises(ValueError, match="refusing to localise a naive index"):
        to_utc(idx)


def test_to_local_rejects_naive_index() -> None:
    """to_local() rejects a naive (tz-unaware) index."""
    idx = pd.date_range("2026-06-17", periods=4, freq="15min")
    with pytest.raises(ValueError, match="refusing to localise a naive index"):
        to_local(idx)
