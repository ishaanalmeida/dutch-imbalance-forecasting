"""Append-only vintage store.

RIGOUR ZONE (CLAUDE.md §12): this enforces R1 for revised series. A forecast is
revised repeatedly for the same target period; using the *current* value of a
revised series as a feature is look-ahead, because that value did not exist at
decision time. The store keeps every vintage and `latest_as_of` returns only
what had been observed STRICTLY before a given instant.

This is the one artefact that cannot be reconstructed later: ENTSO-E and
Open-Meteo both serve the current vintage only, so a vintage not captured live
is gone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.data import vintage

T0 = datetime(2026, 6, 17, 6, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vintage, "VINTAGE_ROOT", tmp_path)


def _frame(values: list[float], start: str = "2026-06-18T00:00Z") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(values), freq="h", tz="UTC", name="target_time")
    return pd.DataFrame({"wind_speed_100m": values}, index=idx)


def test_append_then_read_round_trips() -> None:
    vintage.append_vintage("weather", _frame([10.0, 11.0]), T0)
    got = vintage.read_vintages("weather")
    assert len(got) == 2
    assert set(got.columns) >= {"observed_at", "target_time", "wind_speed_100m"}
    assert got["observed_at"].dt.tz is not None


def test_a_later_vintage_does_not_overwrite_an_earlier_one() -> None:
    """The whole point. Two observations of the same target period coexist."""
    vintage.append_vintage("weather", _frame([10.0, 11.0]), T0)
    vintage.append_vintage("weather", _frame([99.0, 98.0]), T0 + timedelta(hours=6))
    got = vintage.read_vintages("weather")
    assert len(got) == 4, "later vintage overwrote the earlier one"
    assert sorted(got["observed_at"].unique()) == sorted(
        [pd.Timestamp(T0), pd.Timestamp(T0 + timedelta(hours=6))]
    )


def test_re_running_the_same_vintage_is_idempotent() -> None:
    """A cron job that fires twice must not double-count."""
    vintage.append_vintage("weather", _frame([10.0, 11.0]), T0)
    vintage.append_vintage("weather", _frame([10.0, 11.0]), T0)
    assert len(vintage.read_vintages("weather")) == 2


def test_re_running_the_same_vintage_with_changed_values_keeps_the_first() -> None:
    """Same observed_at must mean the same observation. If the payload differs,
    the first write wins -- silently replacing it would rewrite history."""
    vintage.append_vintage("weather", _frame([10.0, 11.0]), T0)
    vintage.append_vintage("weather", _frame([77.0, 77.0]), T0)
    got = vintage.read_vintages("weather")
    assert len(got) == 2
    assert sorted(got["wind_speed_100m"].tolist()) == [10.0, 11.0]


# --- latest_as_of: the R1-safe reader ---------------------------------------


def test_latest_as_of_is_strictly_before() -> None:
    """R1 says strictly before. A vintage observed exactly at the decision
    instant is not yet usable."""
    vintage.append_vintage("weather", _frame([10.0, 11.0]), T0)
    assert vintage.latest_as_of("weather", T0).empty
    assert not vintage.latest_as_of("weather", T0 + timedelta(seconds=1)).empty


def test_latest_as_of_picks_the_most_recent_visible_vintage() -> None:
    vintage.append_vintage("weather", _frame([10.0, 11.0]), T0)
    vintage.append_vintage("weather", _frame([20.0, 21.0]), T0 + timedelta(hours=6))
    vintage.append_vintage("weather", _frame([30.0, 31.0]), T0 + timedelta(hours=12))

    # At T0+7h only the first two vintages exist; the second is the latest.
    got = vintage.latest_as_of("weather", T0 + timedelta(hours=7))
    assert got["wind_speed_100m"].tolist() == [20.0, 21.0]
    assert (got["observed_at"] == pd.Timestamp(T0 + timedelta(hours=6))).all()


def test_latest_as_of_returns_one_row_per_target_period() -> None:
    vintage.append_vintage("weather", _frame([10.0, 11.0]), T0)
    vintage.append_vintage("weather", _frame([20.0, 21.0]), T0 + timedelta(hours=6))
    got = vintage.latest_as_of("weather", T0 + timedelta(days=1))
    assert got["target_time"].is_unique
    assert len(got) == 2


def test_latest_as_of_falls_back_per_target_period() -> None:
    """A newer vintage that covers only some target periods must not hide the
    older vintage's coverage of the rest."""
    vintage.append_vintage("weather", _frame([10.0, 11.0, 12.0]), T0)
    # Later vintage covers only the first target period.
    vintage.append_vintage("weather", _frame([99.0]), T0 + timedelta(hours=6))

    got = vintage.latest_as_of("weather", T0 + timedelta(days=1)).sort_values("target_time")
    assert got["wind_speed_100m"].tolist() == [99.0, 11.0, 12.0]


def test_naive_observed_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        vintage.append_vintage("weather", _frame([1.0]), datetime(2026, 6, 17, 6, 0))


def test_naive_as_of_is_rejected() -> None:
    vintage.append_vintage("weather", _frame([1.0]), T0)
    with pytest.raises(ValueError, match="timezone-aware"):
        vintage.latest_as_of("weather", datetime(2026, 6, 18))


def test_naive_frame_index_is_rejected() -> None:
    naive = pd.DataFrame({"x": [1.0]}, index=pd.date_range("2026-06-18", periods=1))
    with pytest.raises(ValueError, match="tz-aware"):
        vintage.append_vintage("weather", naive, T0)


def test_reading_an_unknown_dataset_returns_empty() -> None:
    assert vintage.read_vintages("never_written").empty
    assert vintage.latest_as_of("never_written", T0).empty


def test_empty_frame_is_not_written() -> None:
    """Writing an empty vintage would record 'we observed nothing', which is
    indistinguishable from 'the job did not run'."""
    empty = pd.DataFrame({"wind_speed_100m": []}, index=pd.DatetimeIndex([], tz="UTC"))
    vintage.append_vintage("weather", empty, T0)
    assert vintage.read_vintages("weather").empty
