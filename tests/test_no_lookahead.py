"""The Phase 1 gate (CLAUDE.md §3): prove no feature can be constructed from
information published after its decision timestamp.

RIGOUR ZONE. These tests are adversarial by design — each one attempts a leak
that a plausible implementation would permit, and asserts refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.data.data_availability import (
    LookAheadError,
    UnresolvedLagError,
    assert_available,
    available_at,
    is_available,
)
from src.data.timebase import ISP_MINUTES
from src.market import load_rules

ISP = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
DECISION = ISP  # CLAUDE.md ADR-005: decision is taken at ISP start


def test_the_target_can_never_be_used_as_a_feature() -> None:
    """The settled imbalance price is the TARGET. If this ever passes, the
    entire project is measuring nothing."""
    for field in ("imbalance_price_settled", "regulation_state"):
        with pytest.raises(LookAheadError):
            assert_available(field, ISP, DECISION)


def test_no_field_is_available_before_the_period_it_describes_unless_declared() -> None:
    """Only day-before-published fields may precede delivery. Anything else
    claiming pre-delivery availability is a config error.

    The real invariant is availability >= period END (start + ISP_MINUTES),
    not just >= start: lag_after_period is measured from the period's end, so
    a bare `>= ISP` bound would also pass an implementation that (wrongly)
    measured lag from the period's START -- exactly the off-by-one this
    file's other test targets directly, but this one would silently miss it
    too if left at `>= ISP`."""
    period_end = ISP + timedelta(minutes=ISP_MINUTES)
    for field, spec in load_rules()["publication"].items():
        if spec["rule"] == "unresolved":
            continue
        got = available_at(field, ISP)
        if spec["rule"] != "published_day_before_at":
            assert got >= period_end, f"{field} claims availability before its period ends"


def test_same_isp_realtime_data_is_not_available_at_isp_start() -> None:
    """A within-ISP estimate for ISP t cannot be known at the start of t."""
    assert not is_available("imbalance_price_realtime_estimate", ISP, DECISION)


def test_previous_isp_realtime_estimate_is_not_yet_available() -> None:
    """ISP t-1 ends exactly at the decision instant, so its 2-minute-lagged
    estimate publishes 2 minutes too late. The obvious feature is unusable."""
    previous = ISP - timedelta(minutes=15)
    assert not is_available("imbalance_price_realtime_estimate", previous, DECISION)


def test_two_isps_back_realtime_estimate_is_available() -> None:
    """ISP t-2 ends 15 minutes before the decision; its estimate publishes
    13 minutes before. This is the true information frontier."""
    two_back = ISP - timedelta(minutes=30)
    assert is_available("imbalance_price_realtime_estimate", two_back, DECISION)


def test_an_unresolved_lag_still_blocks_use_in_both_directions() -> None:
    """Unresolved lag must block use, not merely warn — for any ISP, past or
    present. ADR-006.

    `balance_delta` held this role until its lag was measured (ADR-019);
    `activated_balancing_volumes` is still unresolved and carries it now.
    """
    for offset in (-timedelta(days=30), timedelta(0), timedelta(days=30)):
        with pytest.raises(UnresolvedLagError):
            is_available("activated_balancing_volumes", ISP + offset, DECISION)


def test_balance_delta_respects_its_measured_lag() -> None:
    """Measured from the END of the period (ADR-019, ADR-024).

    Consequence worth stating: the balance delta for ISP t-1 publishes the
    measured lag AFTER t-1 ends — i.e. 134 s into ISP t — so it is NOT available at the
    decision instant for t. The newest usable ISP-level balance delta is t-2.
    Same shape as the real-time price estimate, and for the same reason.
    """
    spec = load_rules()["publication"]["balance_delta"]
    assert spec["lag_confidence"] == "measured", "lag must be measured, never assumed"
    lag_seconds = int(spec["lag_seconds"])

    # Pin the value against its own evidence rather than hard-coding a number
    # that silently rots. R1 is asymmetric: understating the lag leaks, while
    # overstating only costs signal -- so the encoded value must be at least
    # the largest lag ever observed.
    measurement = spec["lag_measurement"]
    assert lag_seconds >= measurement["max_seconds"], (
        f"encoded lag {lag_seconds}s is below the observed max "
        f"{measurement['max_seconds']}s -- that leaks on the upper tail"
    )

    # Its own ISP: obviously not available at that ISP's start.
    assert not is_available("balance_delta", ISP, DECISION)

    # The immediately previous ISP: ends exactly at DECISION, publishes lag_seconds later.
    previous = ISP - timedelta(minutes=15)
    assert available_at("balance_delta", previous) == DECISION + timedelta(seconds=lag_seconds)
    assert not is_available("balance_delta", previous, DECISION)

    # Two ISPs back: ends 15 min before DECISION, so visible with ~13 min to spare.
    two_back = ISP - timedelta(minutes=30)
    assert is_available("balance_delta", two_back, DECISION)


def test_balance_delta_isp_level_availability_is_deliberately_conservative() -> None:
    """balance_delta is a 12-SECOND signal, but `lag_after_period` answers at
    ISP granularity: it reports the whole ISP as arriving `lag_seconds` after
    the ISP ends. In reality each 12 s point arrives that long after that
    POINT ends, so
    most of ISP t-1 is visible well before this rule admits.

    That is conservative — it can only ever withhold information, never grant
    it early — so it is safe under R1 and is the right default. It does cost
    real signal, and the feature builder will want point-level availability
    (`point_end + lag`) rather than this ISP-level answer. Pinned so the
    conservatism is a recorded decision rather than an unnoticed limitation.
    """
    spec = load_rules()["publication"]["balance_delta"]
    assert spec["cadence_seconds"] == 12, "sub-ISP cadence is what makes this conservative"

    previous = ISP - timedelta(minutes=15)
    isp_level = available_at("balance_delta", previous)
    # A point ending one cadence tick into t-1 would really be visible here:
    earliest_point_end = previous + timedelta(seconds=spec["cadence_seconds"])
    point_level = earliest_point_end + timedelta(seconds=spec["lag_seconds"])
    assert point_level < isp_level, "ISP-level answer should be the later, safer one"


def test_availability_is_monotonic_in_target_period() -> None:
    """A later ISP can never become available earlier than an earlier one."""
    fields = [f for f, s in load_rules()["publication"].items() if s["rule"] != "unresolved"]
    for field in fields:
        earlier = available_at(field, ISP)
        later = available_at(field, ISP + timedelta(hours=6))
        assert later >= earlier, f"{field} availability went backwards in time"


def test_a_deliberate_off_by_one_leak_is_caught() -> None:
    """Classic bug: measuring lag from period START instead of period END.
    For a 1-hour-lag field that mistake grants 15 free minutes."""
    published = available_at("actual_generation", ISP)
    naive_wrong = ISP + timedelta(hours=1)
    assert published > naive_wrong
    assert not is_available("actual_generation", ISP, naive_wrong)


def test_unresolved_fields_never_yield_a_timestamp_whatever_lag_they_carry() -> None:
    """available_at must dispatch on `rule` BEFORE reading `lag_seconds`. If it
    ever reads the number first, a field whose lag is admittedly unknown would
    silently produce a real-looking availability time."""
    for field, spec in load_rules()["publication"].items():
        if spec["rule"] == "unresolved":
            with pytest.raises(UnresolvedLagError):
                available_at(field, ISP)


def test_rule_unresolved_wins_even_when_lag_seconds_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test above doesn't actually discriminate rule-first dispatch from
    lag-first dispatch: both real `unresolved` fields (balance_delta,
    activated_balancing_volumes) carry `lag_seconds: null` today (ADR-009),
    so a lag-first implementation would raise on the null check for the same
    wrong reason and pass that test anyway. This poisons a spec with
    `rule: unresolved` paired with a *numeric* lag_seconds -- the one case
    that actually proves `rule` is checked before `lag_seconds` is ever
    read, which is the claim ADR-009 relies on."""
    monkeypatch.setattr(
        "src.data.data_availability.load_rules",
        lambda: {
            "publication": {
                "poisoned_field": {
                    "rule": "unresolved",
                    "lag_seconds": 60,
                    "lag_confidence": "unresolved",
                },
            }
        },
    )
    with pytest.raises(UnresolvedLagError):
        available_at("poisoned_field", ISP)
