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
from zoneinfo import ZoneInfo

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
    assert available_at("actual_generation", ISP) == datetime(2026, 6, 17, 15, 45, tzinfo=UTC)


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


def test_look_ahead_error_message_normalises_all_timestamps_to_utc() -> None:
    """`published` is UTC by construction; target_period_start and
    decision_time must be normalised to UTC too, or the message mixes offsets
    and becomes misleading when a caller passes a non-UTC-tz instant."""
    amsterdam = ZoneInfo("Europe/Amsterdam")
    isp_local = ISP.astimezone(amsterdam)  # same instant, +02:00 CEST
    with pytest.raises(LookAheadError) as exc:
        assert_available("imbalance_price_settled", isp_local, isp_local)
    message = str(exc.value)
    assert ISP.isoformat() in message, message
    assert "+02:00" not in message, message


# ---------------------------------------------------------------------------
# Fix round (coordinator review): guards that were previously exercised only
# incidentally, or not at all. Each poisons a minimal, hand-built publication
# spec via monkeypatch rather than mutating config/market_rules.yaml.
# ---------------------------------------------------------------------------


def _stub_publication(
    monkeypatch: pytest.MonkeyPatch, publication: dict[str, dict[str, object]]
) -> None:
    """Replace load_rules() as seen by data_availability with a minimal
    hand-built publication block, so a malformed spec can be tested without
    touching the real config."""
    monkeypatch.setattr(
        "src.data.data_availability.load_rules", lambda: {"publication": publication}
    )


def test_negative_lag_seconds_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A negative lag_after_period value would mean the datum is available
    before the period it describes even ENDS. This repo shipped exactly this
    class of bug once already as the lag_seconds: -1 sentinel (see this
    file's module docstring) -- available_at must refuse it outright, not
    silently grant a day of look-ahead."""
    _stub_publication(
        monkeypatch,
        {"actual_generation": {"rule": "lag_after_period", "lag_seconds": -86400}},
    )
    with pytest.raises(UnresolvedLagError, match="negative"):
        available_at("actual_generation", ISP)


def test_boolean_lag_seconds_is_rejected_as_not_an_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """isinstance(True, int) is True in Python, and PyYAML 1.1 parses
    yes/on/true as booleans -- lag_seconds: yes must not silently resolve to
    a 1-second lag."""
    _stub_publication(
        monkeypatch,
        {"actual_generation": {"rule": "lag_after_period", "lag_seconds": True}},
    )
    with pytest.raises(UnresolvedLagError, match="int"):
        available_at("actual_generation", ISP)


def test_misaligned_target_period_start_is_rejected() -> None:
    with pytest.raises(ValueError, match="ISP boundary"):
        available_at("actual_generation", ISP + timedelta(minutes=1))


def test_naive_target_period_start_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        available_at("actual_generation", datetime(2026, 6, 17, 14, 30))


def test_unhandled_publication_rule_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_publication(monkeypatch, {"mystery_field": {"rule": "measured_by_tarot_reading"}})
    with pytest.raises(UnknownFieldError):
        available_at("mystery_field", ISP)


def test_lag_after_period_with_null_lag_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct from `rule: unresolved` (ADR-009): a field that claims
    lag_after_period but omits the lag entirely is a plain config error and
    must refuse cleanly rather than crash trying to add None to a
    timedelta."""
    _stub_publication(
        monkeypatch, {"phantom_field": {"rule": "lag_after_period", "lag_seconds": None}}
    )
    with pytest.raises(UnresolvedLagError, match="null"):
        available_at("phantom_field", ISP)


def test_day_ahead_price_uses_local_not_utc_calendar_date() -> None:
    """22:30 UTC in June is already the 18th in Amsterdam (CEST, UTC+2). An
    implementation keying off the UTC calendar date instead of the local one
    would grant a full extra day of apparent availability for roughly 8 ISPs
    per day, all year round."""
    isp = datetime(2026, 6, 17, 22, 30, tzinfo=UTC)
    assert available_at("day_ahead_price", isp) == datetime(2026, 6, 17, 11, 0, tzinfo=UTC)


def test_day_ahead_price_publication_offset_across_spring_forward() -> None:
    """Delivery (2026-03-29 12:00Z) is after the spring-forward transition,
    so local delivery is CEST (UTC+2); publication the day before is still
    pre-transition CET (UTC+1), so the UTC offset of the result differs from
    the delivery ISP's own offset."""
    isp = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    assert available_at("day_ahead_price", isp) == datetime(2026, 3, 28, 12, 0, tzinfo=UTC)


def test_day_ahead_price_publication_offset_across_fall_back() -> None:
    """Delivery (2026-10-25 12:00Z) is after the fall-back transition, so
    local delivery is CET (UTC+1); publication the day before is still
    pre-transition CEST (UTC+2)."""
    isp = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    assert available_at("day_ahead_price", isp) == datetime(2026, 10, 24, 11, 0, tzinfo=UTC)
