"""Settlement logic against hand-worked examples from docs/DOMAIN_NOTES.md.

RIGOUR ZONE (CLAUDE.md §12, §10 mandatory tests). Every expected value below is
worked by hand from Table 2 of TenneT's Imbalance Pricing System v6.0, not by
running the implementation and recording what it produced.

Component prices used throughout (EUR/MWh), chosen to exercise sign flips:
    p_up = 120     highest-priced activated upward aFRR bid
    p_down = -15   lowest-priced activated downward aFRR bid (negative)
    p_mid = 30     mean of lowest upward and highest downward *offered* bid
"""

from __future__ import annotations

import pytest

from src.market import cash_to_brp, imbalance_prices, load_rules

P_UP, P_DOWN, P_MID = 120.0, -15.0, 30.0


# --- Table 2: price selection per regulation state --------------------------

@pytest.mark.parametrize(
    ("state", "expected_long", "expected_short", "why"),
    [
        (0, P_MID, P_MID, "state 0: no activation, both sides settle at the mid-price"),
        (1, P_UP, P_UP, "state +1: upward only, single price at p_up"),
        (-1, P_DOWN, P_DOWN, "state -1: downward only, single price at p_down"),
        # State 2 normal dual pricing: p_up >= p_mid and p_down <= p_mid, so
        # neither leg is corrected. Short pays 120, long receives -15 (i.e. pays 15).
        (2, P_DOWN, P_UP, "state 2: dual priced, both legs punitive"),
    ],
)
def test_price_selection_matches_table_2(
    state: int, expected_long: float, expected_short: float, why: str
) -> None:
    long_price, short_price = imbalance_prices(state, P_UP, P_DOWN, P_MID)
    assert long_price == expected_long, why
    assert short_price == expected_short, why


def test_state_2_reverse_pricing_collapses_both_legs_to_mid() -> None:
    """Reverse pricing: p_up < p_mid AND p_down > p_mid.

    Table 2 replaces each offending leg with the mid-price, so the dual price
    collapses to a single price. Hand-worked: p_up=25 < p_mid=30 -> short = 30;
    p_down=35 > p_mid=30 -> long = 30.
    """
    long_price, short_price = imbalance_prices(2, p_up=25.0, p_down=35.0, p_mid=30.0)
    assert (long_price, short_price) == (30.0, 30.0)


def test_state_2_reverse_pricing_corrects_only_the_offending_leg() -> None:
    """Only the long leg is reversed here: p_up=120 >= p_mid=30 stays at 120,
    p_down=35 > p_mid=30 is corrected to 30."""
    long_price, short_price = imbalance_prices(2, p_up=120.0, p_down=35.0, p_mid=30.0)
    assert (long_price, short_price) == (30.0, 120.0)


def test_dual_price_never_favours_the_brp() -> None:
    """Invariant across all states: the short price is never below the long price.

    A BRP can never be paid more for being long than it is charged for being
    short in the same ISP. If this ever fails, the rule table has been inverted.
    """
    grid = [-50.0, -15.0, 0.0, 30.0, 120.0]
    for state in (0, 1, -1, 2):
        for p_up in grid:
            for p_down in grid:
                for p_mid in grid:
                    long_price, short_price = imbalance_prices(state, p_up, p_down, p_mid)
                    assert short_price >= long_price, (state, p_up, p_down, p_mid)


# --- Table 2: direction of payment ------------------------------------------

@pytest.mark.parametrize(
    ("state", "e_surplus", "e_shortage", "expected_cash", "table_2_row"),
    [
        (1, 10.0, 0.0, +1200.0, "state +1, BRP surplus, Pup (+): TSO -> BRP"),
        (1, 0.0, 10.0, -1200.0, "state +1, BRP shortage, Pup (+): BRP -> TSO"),
        (-1, 0.0, 10.0, +150.0, "state -1, BRP shortage, Pdown (-): TSO -> BRP"),
        (-1, 10.0, 0.0, -150.0, "state -1, BRP surplus, Pdown (-): BRP -> TSO"),
        (2, 10.0, 0.0, -150.0, "state 2, BRP surplus, Pdown (-): BRP -> TSO"),
        (2, 0.0, 10.0, -1200.0, "state 2, BRP shortage, Pup (+): BRP -> TSO"),
    ],
)
def test_direction_of_payment(
    state: int,
    e_surplus: float,
    e_shortage: float,
    expected_cash: float,
    table_2_row: str,
) -> None:
    long_price, short_price = imbalance_prices(state, P_UP, P_DOWN, P_MID)
    assert cash_to_brp(long_price, short_price, e_surplus, e_shortage) == expected_cash, table_2_row


def test_state_2_penalises_a_battery_in_both_directions() -> None:
    """The economic point of dual pricing, stated as a test.

    In state 2 with these prices a battery that discharges 10 MWh above schedule
    pays 150 EUR, and one that charges 10 MWh above schedule pays 1200 EUR.
    Passive balancing is loss-making in both directions, so a dispatch policy
    that ignores the state-2 probability will systematically overstate revenue.
    """
    long_price, short_price = imbalance_prices(2, P_UP, P_DOWN, P_MID)
    assert cash_to_brp(long_price, short_price, 10.0, 0.0) < 0
    assert cash_to_brp(long_price, short_price, 0.0, 10.0) < 0


# --- Failure modes: the rule table must refuse rather than guess -------------

def test_unknown_regulation_state_raises() -> None:
    with pytest.raises(KeyError, match="Unknown regulation state"):
        imbalance_prices(99, P_UP, P_DOWN, P_MID)


def test_missing_required_component_price_raises() -> None:
    """State +1 needs p_up. Supplying None must fail loudly, not fall back."""
    with pytest.raises(ValueError, match="requires"):
        imbalance_prices(1, p_up=None, p_down=P_DOWN, p_mid=P_MID)


def test_irrelevant_component_price_may_be_absent() -> None:
    """State -1 does not reference p_up, so p_up=None is fine."""
    assert imbalance_prices(-1, None, P_DOWN, P_MID) == (P_DOWN, P_DOWN)


def test_negative_energy_magnitude_raises() -> None:
    with pytest.raises(ValueError, match="magnitudes"):
        cash_to_brp(30.0, 30.0, -1.0, 0.0)


# --- The config is the source of truth --------------------------------------

def test_rule_table_covers_exactly_the_documented_regulation_states() -> None:
    rules = load_rules()
    documented = {k for k in rules["regulation_states"] if isinstance(k, int)}
    priced = set(rules["pricing"]["rules"])
    assert documented == priced == {0, 1, -1, 2}


def test_isp_length_is_fifteen_minutes() -> None:
    assert load_rules()["isp"]["length_minutes"] == 15


def test_incentive_component_is_abolished() -> None:
    """CLAUDE.md Phase 0 Q4. Abolished 2020-07-31; must not enter any price."""
    incentive = load_rules()["incentive_component"]
    assert incentive["active"] is False
    assert incentive["value_eur_per_mwh"] == 0.0
