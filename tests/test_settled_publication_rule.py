"""Settled prices publish at a wall-clock time, not at a fixed lag.

RIGOUR ZONE (CLAUDE.md §12).

[IPS61] §3.2: "After the delivery day (D+1), the process of financial settlement
starts at 10.00 a.m." That is a wall-clock rule covering the whole delivery day
at once -- NOT a constant offset from each ISP.

Modelling it as a fixed 34-hour lag was wrong in shape and up to 21 hours too
late for mid-day ISPs. That is conservative for the target itself, which is
harmless, but it silently crippled the mandatory baselines: a seasonal-naive
model could not use yesterday's settled price at any sampled ISP, though it
genuinely had it. An artificially weak baseline flatters every model measured
against it, which is the exact failure R4 exists to prevent.

Expected instants below are hand-derived. Europe/Amsterdam in August is CEST
(UTC+2), so 10:00 local = 08:00 UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from src.data.data_availability import available_at, is_available

FIELDS = ("imbalance_price_settled", "regulation_state")


def test_settled_price_publishes_at_10_local_the_day_after_delivery() -> None:
    for field in FIELDS:
        for hour in (0, 6, 12, 18):
            isp = datetime(2026, 8, 7, hour, 45, tzinfo=UTC)
            assert available_at(field, isp) == datetime(2026, 8, 8, 8, 0, tzinfo=UTC), (
                f"{field} at {isp} should publish D+1 10:00 CEST = 08:00 UTC"
            )


def test_every_isp_of_one_delivery_day_publishes_at_the_same_instant() -> None:
    """The whole delivery day settles together. A fixed per-ISP lag cannot
    express that, which is why the rule shape mattered.

    The day must be built in LOCAL time: a UTC calendar day is not a delivery
    day. At 00:00 UTC it is already 02:00 in Amsterdam, so a UTC-built day
    straddles two delivery days and legitimately settles at two instants.
    """
    from src.data.timebase import isps_in_local_day

    instants = {
        available_at("imbalance_price_settled", isp) for isp in isps_in_local_day(date(2026, 8, 7))
    }
    assert len(instants) == 1, f"expected one publication instant, got {sorted(instants)}"


def test_the_target_is_still_never_usable_at_its_own_decision_time() -> None:
    """The fix must not accidentally make the target available for its own ISP."""
    for hour in range(0, 24, 3):
        isp = datetime(2026, 8, 7, hour, 0, tzinfo=UTC)
        for field in FIELDS:
            assert not is_available(field, isp, isp), f"{field} leaked at {isp}"


def test_seasonal_naive_baseline_can_use_yesterdays_settled_price() -> None:
    """The regression this fix exists for. A baseline denied data it genuinely
    had is an artificially weak baseline.

    Sampled at 09:00-20:00 UTC, which is 11:00-22:00 local: decisions taken
    after the 10:00 local settlement run, and on the same local calendar day.

    Both ends of that window matter. Before ~08:00 UTC the settlement run has
    not happened yet. After ~22:00 UTC the ISP has rolled into the NEXT local
    day, so subtracting 24 hours in UTC lands on a different delivery day than
    "yesterday" means locally. Those two cases are pinned separately below.
    """
    for hour in (9, 12, 16, 20):
        isp = datetime(2026, 8, 7, hour, 0, tzinfo=UTC)
        yesterday = isp - timedelta(days=1)
        assert is_available("imbalance_price_settled", yesterday, isp), (
            f"seasonal-naive baseline crippled at {isp}: yesterday's settled "
            f"price published {available_at('imbalance_price_settled', yesterday)}"
        )


def test_last_weeks_settled_price_is_available() -> None:
    isp = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    assert is_available("imbalance_price_settled", isp - timedelta(days=7), isp)


def test_late_evening_utc_isp_belongs_to_the_next_local_delivery_day() -> None:
    """23:00 UTC is 01:00 local the following day. Subtracting 24 hours in UTC
    therefore does NOT give "the same period yesterday" in delivery-day terms,
    and the settled price for that period is not out yet. A feature builder
    must do this arithmetic in local delivery days, not in UTC offsets."""
    isp = datetime(2026, 8, 7, 23, 0, tzinfo=UTC)
    assert not is_available("imbalance_price_settled", isp - timedelta(days=1), isp)


def test_early_morning_isp_cannot_use_the_immediately_previous_day() -> None:
    """Honest limit of the fix: an ISP at 06:00 UTC (08:00 local) decides BEFORE
    the 10:00 local settlement run, so yesterday's prices are not yet out.
    The baseline must fall back to D-2, and this documents that it must."""
    isp = datetime(2026, 8, 7, 6, 0, tzinfo=UTC)
    assert not is_available("imbalance_price_settled", isp - timedelta(days=1), isp)
    assert is_available("imbalance_price_settled", isp - timedelta(days=2), isp)


def test_dst_boundary_resolves_to_local_10_00_on_both_sides() -> None:
    """25 Oct 2026 is the 25-hour day. 10:00 local is CET (+01:00) on the 26th."""
    isp = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    assert available_at("imbalance_price_settled", isp) == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
