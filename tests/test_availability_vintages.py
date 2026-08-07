"""Vintage-aware availability.

RIGOUR ZONE (CLAUDE.md §12).

Six of the ten publication fields are `revised: true`. Until now `available_at`
returned one answer per field, which is conservative but blocks the feature
CLAUDE.md §4 explicitly asks for: the difference between two vintages of the
same forecast, which is a legitimate leading indicator of imbalance and does
not leak.

Reg. 543/2013 Art. 14(2)(d) mandates two wind/solar vintages: one by 17:00 on
D-1, and at least one intraday update at 07:00 on D. Both are declared in
config/market_rules.yaml.

All expected instants below are hand-derived from the config, not recorded from
the implementation. Europe/Amsterdam in June is CEST (UTC+2).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.data.data_availability import (
    UnknownVintageError,
    available_at,
    available_vintages,
    is_available,
)

FIELD = "wind_solar_forecast_day_ahead"

# Target ISP 16:30 local on 17 June 2026. Decision is taken at ISP start.
ISP_AFTERNOON = datetime(2026, 6, 17, 14, 30, tzinfo=UTC)
# Target ISP 06:30 local the same day -- before the 07:00 intraday update.
ISP_EARLY = datetime(2026, 6, 17, 4, 30, tzinfo=UTC)


def test_day_ahead_vintage_resolves_to_d_minus_1_17_00_local() -> None:
    """17:00 CEST on 16 June = 15:00 UTC."""
    assert available_at(FIELD, ISP_AFTERNOON, vintage="day_ahead") == datetime(
        2026, 6, 16, 15, 0, tzinfo=UTC
    )


def test_intraday_vintage_resolves_to_07_00_local_on_the_delivery_day() -> None:
    """07:00 CEST on 17 June = 05:00 UTC."""
    assert available_at(FIELD, ISP_AFTERNOON, vintage="intraday") == datetime(
        2026, 6, 17, 5, 0, tzinfo=UTC
    )


def test_no_vintage_argument_returns_the_earliest_vintage() -> None:
    """The conservative default. Code that does not ask for a vintage by name
    must never silently receive a later revision."""
    default = available_at(FIELD, ISP_AFTERNOON)
    first = available_at(FIELD, ISP_AFTERNOON, vintage="day_ahead")
    latest = available_at(FIELD, ISP_AFTERNOON, vintage="intraday")
    assert default == first
    assert default < latest


def test_both_vintages_are_available_for_an_afternoon_target() -> None:
    """So the forecast-error proxy is computable here."""
    assert available_vintages(FIELD, ISP_AFTERNOON, ISP_AFTERNOON) == ["day_ahead", "intraday"]


def test_only_the_day_ahead_vintage_is_available_for_an_early_target() -> None:
    """Decision at 04:30 UTC; the intraday update publishes at 05:00 UTC, half
    an hour later. A feature builder that assumes both vintages always exist
    would be reading the future for every early-morning ISP."""
    assert available_vintages(FIELD, ISP_EARLY, ISP_EARLY) == ["day_ahead"]
    assert not is_available(FIELD, ISP_EARLY, ISP_EARLY, vintage="intraday")
    assert is_available(FIELD, ISP_EARLY, ISP_EARLY, vintage="day_ahead")


def test_forecast_error_proxy_is_only_computable_when_both_vintages_exist() -> None:
    """State the §4 feature's precondition as a test: the proxy needs two
    vintages, and that is a per-period fact, not a global one."""

    def proxy_available(isp: datetime) -> bool:
        return len(available_vintages(FIELD, isp, isp)) >= 2

    assert proxy_available(ISP_AFTERNOON)
    assert not proxy_available(ISP_EARLY)


def test_unknown_vintage_name_raises() -> None:
    with pytest.raises(UnknownVintageError, match="tomorrow_maybe"):
        available_at(FIELD, ISP_AFTERNOON, vintage="tomorrow_maybe")


def test_requesting_a_vintage_on_a_field_that_declares_none_raises() -> None:
    """day_ahead_price has no vintage schedule. Asking for one must fail rather
    than silently returning the base rule, which would misrepresent the answer."""
    with pytest.raises(UnknownVintageError, match="declares no named vintages"):
        available_at("day_ahead_price", ISP_AFTERNOON, vintage="intraday")


def test_vintages_are_declared_earliest_first() -> None:
    """available_at()'s conservative default depends on this ordering, so it is
    an invariant of the config, not a convention."""
    from src.market import load_rules

    for field, spec in load_rules()["publication"].items():
        vintages = spec.get("vintages")
        if not vintages:
            continue
        times = [available_at(field, ISP_AFTERNOON, vintage=v["name"]) for v in vintages]
        assert times == sorted(times), f"{field} vintages are not earliest-first"


def test_unresolved_field_still_refuses_even_with_a_vintage_argument() -> None:
    """The vintage path must not become a way around the unresolved-lag guard."""
    from src.data.data_availability import UnresolvedLagError

    with pytest.raises((UnresolvedLagError, UnknownVintageError)):
        available_at("balance_delta", ISP_AFTERNOON, vintage="whatever")


def test_available_vintages_is_empty_before_anything_is_published() -> None:
    early_decision = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    assert available_vintages(FIELD, ISP_AFTERNOON, early_decision) == []
