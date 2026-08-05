"""Config-shape tests for the `publication:` block in config/market_rules.yaml.

RIGOUR ZONE (CLAUDE.md §12). Phase 0 encoded "available before the delivery
day" as the sentinel `lag_seconds: -1`. In a config that governs look-ahead
enforcement, a negative-number sentinel reads like a real lag and would
silently grant a full day of look-ahead to any code that treats it
arithmetically. These tests pin the replacement: every field declares an
explicit, named `rule`, and no negative lag sentinel remains.

Task 3 extends this file with the actual `available_at()` enforcement tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.data.data_availability import (
    LookAheadError,
    UnknownFieldError,
    UnresolvedLagError,
    assert_available,
    available_at,
    is_available,
)
from src.market import load_rules

VALID_RULES = {"lag_after_period", "published_day_before_at", "unresolved"}


def test_every_publication_field_declares_a_known_rule() -> None:
    for field, spec in load_rules()["publication"].items():
        assert "rule" in spec, f"{field} has no publication rule"
        assert spec["rule"] in VALID_RULES, f"{field} has unknown rule {spec['rule']!r}"


def test_no_negative_lag_sentinels_remain() -> None:
    """A negative lag would mean 'available before the period it describes',
    which is only ever true via an explicit day-before rule."""
    for field, spec in load_rules()["publication"].items():
        lag = spec.get("lag_seconds")
        if lag is not None:
            assert lag >= 0, f"{field} still uses a negative lag sentinel"


def test_day_before_fields_declare_a_publication_time() -> None:
    for field, spec in load_rules()["publication"].items():
        if spec["rule"] == "published_day_before_at":
            assert "local_time" in spec, f"{field} needs a local_time"
            assert "timezone" in spec, f"{field} needs a timezone"


def test_unresolved_fields_carry_no_usable_lag() -> None:
    """A field whose lag is unresolved must not also carry a number that code
    could read and mistake for a measurement."""
    for field, spec in load_rules()["publication"].items():
        if spec["rule"] == "unresolved":
            assert spec.get("lag_seconds") is None, (
                f"{field} declares rule: unresolved but still carries "
                f"lag_seconds={spec.get('lag_seconds')!r}"
            )


# ---------------------------------------------------------------------------
# Task 3: available_at() / is_available() / assert_available() enforcement.
# ---------------------------------------------------------------------------

ISP = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)


def test_lag_after_period_is_measured_from_period_end() -> None:
    """actual_generation: 1h after the period ENDS, not after it starts.
    ISP 14:30-14:45 UTC therefore becomes available at 15:45 UTC."""
    assert available_at("actual_generation", ISP) == datetime(
        2026, 6, 17, 15, 45, tzinfo=UTC
    )


def test_day_ahead_price_is_available_the_previous_afternoon() -> None:
    got = available_at("day_ahead_price", ISP)
    assert got < ISP, "day-ahead price must precede delivery"
    assert got == datetime(2026, 6, 16, 11, 0, tzinfo=UTC)  # 13:00 CEST


def test_settled_imbalance_price_is_not_available_same_day() -> None:
    assert available_at("imbalance_price_settled", ISP) > ISP


def test_unresolved_lag_refuses_rather_than_defaulting() -> None:
    """ADR-006. balance_delta must REFUSE, never fall back to a guess."""
    with pytest.raises(UnresolvedLagError, match="balance_delta"):
        available_at("balance_delta", ISP)


def test_unknown_field_raises() -> None:
    with pytest.raises(UnknownFieldError):
        available_at("wind_speed_at_my_house", ISP)


def test_is_available_is_strict_not_inclusive() -> None:
    """R1 says STRICTLY before. A datum published exactly at the decision
    instant is not usable."""
    at = available_at("actual_generation", ISP)
    assert is_available("actual_generation", ISP, at + timedelta(seconds=1))
    assert not is_available("actual_generation", ISP, at)


def test_assert_available_raises_with_a_diagnostic_message() -> None:
    with pytest.raises(LookAheadError) as exc:
        assert_available("imbalance_price_settled", ISP, ISP)
    assert "imbalance_price_settled" in str(exc.value)
    assert "available at" in str(exc.value)


def test_naive_decision_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_available("actual_generation", ISP, datetime(2026, 6, 17, 16, 0))
