"""Local cache: raw-response store plus month-partitioned Parquet.

Lean zone. Tests cover the documented interface plus two hazards the brief
calls out explicitly: tz-aware month partitioning (pandas drops tz info on
`to_period`) and Windows-illegal characters (`:`, `/`) inside cache keys.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from src.data import cache


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "DATA_ROOT", tmp_path)


def test_raw_round_trip() -> None:
    at = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    cache.store_raw("entsoe", "imbalance_2026-06", b"<xml/>", at)
    assert cache.load_raw("entsoe", "imbalance_2026-06") == b"<xml/>"


def test_load_raw_returns_none_when_absent() -> None:
    assert cache.load_raw("entsoe", "nope") is None


def test_raw_store_is_idempotent() -> None:
    at = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    first = cache.store_raw("entsoe", "k", b"a", at)
    second = cache.store_raw("entsoe", "k", b"a", at)
    assert first == second


def test_raw_key_with_windows_illegal_characters_round_trips() -> None:
    """Real keys are ISO timestamps and paths: `:` and `/` are illegal in
    Windows filenames and must not reach the filesystem raw."""
    at = datetime(2026, 6, 1, tzinfo=UTC)
    key = "imbalance/2026-06-01T00:00Z"
    path = cache.store_raw("entsoe", key, b"payload", at)
    assert path.exists()
    assert cache.load_raw("entsoe", key) == b"payload"


def test_frame_partitions_by_month() -> None:
    idx = pd.date_range("2026-05-30", "2026-06-02", freq="15min", tz="UTC")
    df = pd.DataFrame({"value": range(len(idx))}, index=idx)
    paths = cache.write_frame("prices", df)
    assert {p.name for p in paths} == {"2026-05.parquet", "2026-06.parquet"}


def test_frame_round_trip_preserves_utc_index() -> None:
    idx = pd.date_range("2026-06-01", periods=96, freq="15min", tz="UTC")
    df = pd.DataFrame({"value": range(96)}, index=idx)
    cache.write_frame("prices", df)
    got = cache.read_frame(
        "prices",
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 6, 2, tzinfo=UTC),
    )
    assert str(cast(pd.DatetimeIndex, got.index).tz) == "UTC"
    # Parquet does not persist the DatetimeIndex.freq cache attribute, only
    # the tz-aware instants themselves -- check_freq=False reflects that,
    # it does not weaken the tz assertion above.
    pd.testing.assert_frame_equal(got, df, check_freq=False)


def test_read_frame_rejects_naive_start() -> None:
    with pytest.raises(ValueError, match="naive"):
        cache.read_frame("prices", datetime(2026, 6, 1), datetime(2026, 6, 2, tzinfo=UTC))


def test_read_frame_rejects_naive_end() -> None:
    with pytest.raises(ValueError, match="naive"):
        cache.read_frame("prices", datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2))


def test_rewriting_a_month_replaces_not_duplicates() -> None:
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    cache.write_frame("prices", pd.DataFrame({"value": [1, 2, 3, 4]}, index=idx))
    cache.write_frame("prices", pd.DataFrame({"value": [9, 9, 9, 9]}, index=idx))
    got = cache.read_frame(
        "prices",
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 6, 2, tzinfo=UTC),
    )
    assert len(got) == 4
    assert got["value"].tolist() == [9, 9, 9, 9]
