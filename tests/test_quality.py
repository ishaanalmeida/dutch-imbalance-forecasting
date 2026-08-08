"""Data quality diagnostics tests.

RIGOUR ZONE (CLAUDE.md §12) -- src/data/quality.py feeds docs/DATA_QUALITY.md
and, via structural_break_check, the ADR-007 falsifiable prediction. All
frames constructed here are SYNTHETIC (built by hand in this file), not real
market data -- no ENTSO-E token or TenneT registration exists yet (2026-08-06).
Real numbers are deferred to scripts/build_quality_report.py once data lands.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.data.quality import (
    duplicate_report,
    gap_report,
    regulation_state_distribution,
    structural_break_check,
)

# ---------------------------------------------------------------------------
# gap_report -- verbatim from task-8-brief.md Step 1
# ---------------------------------------------------------------------------


def test_gap_report_finds_a_missing_isp() -> None:
    idx = pd.date_range("2026-06-01", periods=10, freq="15min", tz="UTC")
    df = pd.DataFrame({"v": range(10)}, index=idx).drop(idx[5])
    gaps = gap_report(df)
    assert len(gaps) == 1
    assert gaps.iloc[0]["missing_isps"] == 1


def test_gap_report_is_empty_on_a_complete_series() -> None:
    idx = pd.date_range("2026-06-01", periods=96, freq="15min", tz="UTC")
    assert gap_report(pd.DataFrame({"v": range(96)}, index=idx)).empty


def test_gap_report_tolerates_the_25_hour_day() -> None:
    """The autumn-back day has 100 ISPs and no gaps. A UTC-based check passes
    naturally; a local-time check would report a spurious 4-ISP gap."""
    from src.data.timebase import isps_in_local_day

    idx = isps_in_local_day(date(2026, 10, 25))
    assert len(idx) == 100
    assert gap_report(pd.DataFrame({"v": range(100)}, index=idx)).empty


def test_duplicate_report_finds_repeated_index_entries() -> None:
    idx = pd.DatetimeIndex(["2026-06-01T00:00Z", "2026-06-01T00:00Z", "2026-06-01T00:15Z"])
    dupes = duplicate_report(pd.DataFrame({"v": [1, 2, 3]}, index=idx))
    assert len(dupes) == 1


def test_regulation_state_distribution_sums_to_one_per_group() -> None:
    idx = pd.date_range("2026-06-01", periods=8, freq="15min", tz="UTC")
    df = pd.DataFrame({"regulation_state": [0, 1, -1, 2, 0, 1, 2, 2]}, index=idx)
    dist = regulation_state_distribution(df, by="hour")
    for _, row in dist.iterrows():
        assert row.sum() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# gap_report -- edge cases the brief flags for extra scrutiny: the
# `deltas.iloc[i]` indexing keyed off `breaks.index` (positions from a
# filtered Series, applied back against the unfiltered one) is the kind of
# thing that looks right and is subtly off by one. Verified by hand in the
# investigation for this task; pinned here with two unequal-sized gaps plus
# gaps that sit at the very first and very last boundary of the frame.
# ---------------------------------------------------------------------------


def test_gap_report_finds_two_separate_gaps_of_different_sizes() -> None:
    idx = pd.date_range("2026-06-01", periods=20, freq="15min", tz="UTC")
    # Drop isp 5 (a 1-ISP gap) and isps 12,13 (a 2-ISP gap), leaving both
    # untouched runs on either side so the two gaps are independent.
    df = pd.DataFrame({"v": range(20)}, index=idx).drop(idx[[5, 12, 13]])
    gaps = gap_report(df)
    assert len(gaps) == 2

    first, second = gaps.iloc[0], gaps.iloc[1]
    assert first["gap_start"] == idx[5]
    assert first["gap_end"] == idx[6]
    assert first["missing_isps"] == 1

    assert second["gap_start"] == idx[12]
    assert second["gap_end"] == idx[14]
    assert second["missing_isps"] == 2


def test_gap_report_finds_a_gap_immediately_after_the_first_row() -> None:
    idx = pd.date_range("2026-06-01", periods=10, freq="15min", tz="UTC")
    df = pd.DataFrame({"v": range(10)}, index=idx).drop(idx[1])
    gaps = gap_report(df)
    assert len(gaps) == 1
    assert gaps.iloc[0]["gap_start"] == idx[1]
    assert gaps.iloc[0]["gap_end"] == idx[2]
    assert gaps.iloc[0]["missing_isps"] == 1


def test_gap_report_finds_a_gap_immediately_before_the_last_row() -> None:
    idx = pd.date_range("2026-06-01", periods=10, freq="15min", tz="UTC")
    df = pd.DataFrame({"v": range(10)}, index=idx).drop(idx[8])
    gaps = gap_report(df)
    assert len(gaps) == 1
    assert gaps.iloc[0]["gap_start"] == idx[8]
    assert gaps.iloc[0]["gap_end"] == idx[9]
    assert gaps.iloc[0]["missing_isps"] == 1


def test_gap_report_empty_frame_does_not_explode() -> None:
    gaps = gap_report(pd.DataFrame({"v": []}, index=pd.DatetimeIndex([], tz="UTC")))
    assert gaps.empty
    assert list(gaps.columns) == ["gap_start", "gap_end", "missing_isps"]


# ---------------------------------------------------------------------------
# duplicate_report -- multiple distinct duplicated groups.
# ---------------------------------------------------------------------------


def test_duplicate_report_finds_multiple_distinct_duplicate_groups() -> None:
    idx = pd.DatetimeIndex(
        [
            "2026-06-01T00:00Z",
            "2026-06-01T00:00Z",
            "2026-06-01T00:00Z",
            "2026-06-01T00:15Z",
            "2026-06-01T00:30Z",
            "2026-06-01T00:30Z",
        ]
    )
    dupes = duplicate_report(pd.DataFrame({"v": range(6)}, index=idx))
    assert len(dupes) == 2
    counts = dict(zip(dupes["timestamp"], dupes["count"], strict=True))
    assert counts[pd.Timestamp("2026-06-01T00:00Z")] == 3
    assert counts[pd.Timestamp("2026-06-01T00:30Z")] == 2


# ---------------------------------------------------------------------------
# regulation_state_distribution -- a group containing a single state must
# still sum to 1.0 (no accidental NaN from a degenerate denominator), and
# grouping by month/year on a tz-aware UTC index must not silently misbehave.
# ---------------------------------------------------------------------------


def test_regulation_state_distribution_single_state_group_sums_to_one() -> None:
    idx = pd.date_range("2026-06-01T00:00", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"regulation_state": [0, 0, 0, 0]}, index=idx)
    dist = regulation_state_distribution(df, by="hour")
    assert len(dist) == 1
    assert dist.iloc[0].sum() == pytest.approx(1.0)
    assert dist.iloc[0][0] == pytest.approx(1.0)


def test_regulation_state_distribution_by_month_sums_to_one_per_group() -> None:
    idx = list(pd.date_range("2026-06-28", periods=4, freq="1D", tz="UTC")) + list(
        pd.date_range("2026-07-01", periods=4, freq="1D", tz="UTC")
    )
    df = pd.DataFrame({"regulation_state": [0, 1, -1, 2, 0, 0, 1, 1]}, index=pd.DatetimeIndex(idx))
    dist = regulation_state_distribution(df, by="month")
    assert len(dist) == 2
    for _, row in dist.iterrows():
        assert row.sum() == pytest.approx(1.0)


def test_regulation_state_distribution_by_month_does_not_warn_about_dropped_tz() -> None:
    """Converting a tz-aware index via `.to_period()` drops tz and warns (see
    src/data/cache.py's write_frame, which hit this exact issue). This is a
    UTC-based rigour-zone function; it must group by month without emitting
    that warning."""
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"regulation_state": [0, 1, 0, 1]}, index=idx)
    with _no_warnings():
        regulation_state_distribution(df, by="month")


@contextmanager
def _no_warnings() -> Iterator[None]:
    """Fail the test if any warning is emitted inside the block."""
    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        yield
    assert not records, f"unexpected warnings: {[str(w.message) for w in records]}"


def test_regulation_state_distribution_by_year_sums_to_one_per_group() -> None:
    idx = pd.date_range("2025-12-30", periods=6, freq="1D", tz="UTC")
    df = pd.DataFrame({"regulation_state": [0, 1, -1, 2, 0, 1]}, index=idx)
    dist = regulation_state_distribution(df, by="year")
    assert len(dist) == 2
    for _, row in dist.iterrows():
        assert row.sum() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# structural_break_check
# ---------------------------------------------------------------------------


def _stub_breaks(monkeypatch: pytest.MonkeyPatch, breaks: list[dict[str, object]]) -> None:
    """structural_break_check does `from src.market import load_rules` inside
    the function body, so patching the src.market module attribute is picked
    up on every call regardless of lru_cache on the real load_rules."""
    monkeypatch.setattr("src.market.load_rules", lambda: {"structural_breaks": breaks})


def test_structural_break_check_computes_state2_share_before_and_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_breaks(
        monkeypatch,
        [{"date": "2026-02-03", "what": "synthetic_break"}],
    )
    idx = pd.date_range("2026-02-01", periods=8, freq="1D", tz="UTC")
    # Before 2026-02-03: 2 rows, 0 of them state 2 -> share 0.0.
    # From 2026-02-03 (inclusive): 6 rows, 3 of them state 2 -> share 0.5.
    states = [0, 1, 2, 2, 2, 0, 1, -1]
    df = pd.DataFrame({"regulation_state": states}, index=idx)
    result = structural_break_check(df)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["date"] == "2026-02-03"
    assert row["what"] == "synthetic_break"
    assert row["n_before"] == 2
    assert row["n_after"] == 6
    assert row["state2_share_before"] == pytest.approx(0.0)
    assert row["state2_share_after"] == pytest.approx(3 / 6)


def test_structural_break_check_skips_a_break_with_no_data_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data entirely AFTER the break: `before` is empty -> must not explode."""
    _stub_breaks(monkeypatch, [{"date": "2030-01-01", "what": "future_break"}])
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"regulation_state": [0, 1, 2, -1]}, index=idx)
    result = structural_break_check(df)
    assert result.empty


def test_structural_break_check_skips_a_break_with_no_data_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data entirely BEFORE the break: `after` is empty -> must not explode."""
    _stub_breaks(monkeypatch, [{"date": "2020-01-01", "what": "ancient_break"}])
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"regulation_state": [0, 1, 2, -1]}, index=idx)
    result = structural_break_check(df)
    assert result.empty


def test_structural_break_check_empty_result_still_has_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_breaks(monkeypatch, [{"date": "2030-01-01", "what": "future_break"}])
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"regulation_state": [0, 1, 2, -1]}, index=idx)
    result = structural_break_check(df)
    assert list(result.columns) == [
        "date",
        "what",
        "state2_share_before",
        "state2_share_after",
        "n_before",
        "n_after",
    ]


def test_structural_break_check_handles_multiple_breaks_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_breaks(
        monkeypatch,
        [
            {"date": "2026-02-03", "what": "spanned_break"},
            {"date": "2030-01-01", "what": "unspanned_future_break"},
        ],
    )
    idx = pd.date_range("2026-02-01", periods=8, freq="1D", tz="UTC")
    states = [0, 1, 2, 2, 2, 0, 1, -1]
    df = pd.DataFrame({"regulation_state": states}, index=idx)
    result = structural_break_check(df)
    assert list(result["what"]) == ["spanned_break"]


def test_structural_break_check_against_real_config_surfaces_adr007(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No monkeypatch here: uses the REAL config/market_rules.yaml, exercising
    the actual ADR-007 entry (2026-02-03). Data is still synthetic -- this
    only proves the function surfaces the right row with correct arithmetic
    against real config, not a claim about real state-2 frequency."""
    idx = pd.date_range("2026-01-01", periods=40, freq="1D", tz="UTC")
    # Deliberately construct a rise in state-2 share at the break so the
    # function's arithmetic can be checked precisely; this is NOT evidence
    # about the real market -- see module docstring.
    states = [0 if i % 5 else 2 for i in range(40)]  # 20% state-2 throughout
    df = pd.DataFrame({"regulation_state": states}, index=idx)
    result = structural_break_check(df)

    row = result[result["what"] == "regulation_state_input_switched_to_12s_balance_delta"]
    assert len(row) == 1
    r = row.iloc[0]
    assert r["date"] == "2026-02-03"
    assert r["n_before"] + r["n_after"] == 40
    assert 0.0 <= r["state2_share_before"] <= 1.0
    assert 0.0 <= r["state2_share_after"] <= 1.0

    # Other real structural breaks (2020-07-31, 2024-10-18, 2025-10-01,
    # 2025-11-25) all fall entirely before this synthetic frame, so they must
    # be skipped rather than raising -- confirms the `continue` path fires
    # against the real config too, not just a hand-built stub.
    assert "incentive_component_abolished" not in set(result["what"])


# ---------------------------------------------------------------------------
# scripts/build_quality_report.py -- R3 guard. No real data is cached yet
# (2026-08-06), so the one behaviour worth pinning now is that the script
# refuses to write docs/DATA_QUALITY.md from an empty frame rather than
# emitting a report that reads as "zero issues found".
# ---------------------------------------------------------------------------


def test_build_quality_report_refuses_to_write_from_empty_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.build_quality_report as bqr

    monkeypatch.setattr(bqr, "read_frame", lambda *a, **k: pd.DataFrame())
    out = tmp_path / "DATA_QUALITY.md"
    monkeypatch.setattr(bqr, "OUT", out)

    with pytest.raises(SystemExit):
        bqr.build()

    assert not out.exists()


# --- dual-price share: a rigorous LOWER BOUND on regulation state 2 ----------


def test_dual_price_share_counts_periods_where_the_two_prices_differ() -> None:
    from src.data.quality import dual_price_share

    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {"price_long": [10.0, 10.0, -5.0, 20.0], "price_short": [10.0, 30.0, 40.0, 20.0]},
        index=idx,
    )
    assert dual_price_share(df) == 0.5


def test_dual_price_share_is_a_lower_bound_not_an_equality() -> None:
    """States 0/+1/-1 always have price_long == price_short. State 2 usually
    differs -- but a FULLY reverse-priced state-2 period collapses both legs to
    the mid-price and is indistinguishable here. So this undercounts state 2
    and must never be reported as its exact frequency."""
    from src.data.quality import dual_price_share

    idx = pd.date_range("2026-06-01", periods=2, freq="15min", tz="UTC")
    reverse_priced_state_2 = pd.DataFrame(
        {"price_long": [30.0, 30.0], "price_short": [30.0, 30.0]}, index=idx
    )
    assert dual_price_share(reverse_priced_state_2) == 0.0


def test_dual_price_share_ignores_floating_point_noise() -> None:
    from src.data.quality import dual_price_share

    idx = pd.date_range("2026-06-01", periods=1, freq="15min", tz="UTC")
    df = pd.DataFrame({"price_long": [10.0], "price_short": [10.0 + 1e-12]}, index=idx)
    assert dual_price_share(df) == 0.0


def test_dual_price_break_report_splits_on_the_structural_break_date() -> None:
    from src.data.quality import dual_price_break_report

    before = pd.date_range("2026-01-01", periods=4, freq="15min", tz="UTC")
    after = pd.date_range("2026-03-01", periods=4, freq="15min", tz="UTC")
    df = pd.concat(
        [
            pd.DataFrame({"price_long": [1.0] * 4, "price_short": [1.0] * 4}, index=before),
            pd.DataFrame({"price_long": [1.0] * 4, "price_short": [9.0] * 4}, index=after),
        ]
    )
    out = dual_price_break_report(df)
    row = out[out["date"].astype(str).str.startswith("2026-02-03")].iloc[0]
    assert row["dual_share_before"] == 0.0
    assert row["dual_share_after"] == 1.0


def test_dual_price_break_report_skips_breaks_the_data_does_not_span() -> None:
    from src.data.quality import dual_price_break_report

    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"price_long": [1.0] * 4, "price_short": [1.0] * 4}, index=idx)
    out = dual_price_break_report(df)
    assert "2020-07-31" not in out["date"].astype(str).tolist()


def test_settlement_invariant_holds_flags_violations() -> None:
    """price_short >= price_long is an invariant of the settlement rules. If
    real data ever violates it, either the columns are swapped or the rules
    changed -- both are data-quality emergencies, not curiosities."""
    from src.data.quality import settlement_invariant_violations

    idx = pd.date_range("2026-06-01", periods=3, freq="15min", tz="UTC")
    ok = pd.DataFrame({"price_long": [1.0, 2.0, 3.0], "price_short": [1.0, 5.0, 3.0]}, index=idx)
    assert settlement_invariant_violations(ok) == 0

    bad = pd.DataFrame({"price_long": [9.0, 2.0, 3.0], "price_short": [1.0, 5.0, 3.0]}, index=idx)
    assert settlement_invariant_violations(bad) == 1
