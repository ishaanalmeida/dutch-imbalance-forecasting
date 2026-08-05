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
