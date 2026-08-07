"""The balance-delta lag harness.

The endpoint, rate limits and poll cadence are now known from TenneT's
published API docs; only the subscription key is missing. These tests pin the
things that would silently corrupt a measurement: refusing to run without a
key, never exceeding TenneT's published rate cap, and refusing to invent a
timestamp field.
"""

from __future__ import annotations

import pytest

from scripts import measure_balance_delta_lag as harness


def test_refuses_to_run_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unmeasured lag must never look measured."""
    monkeypatch.delenv("TENNET_API_KEY", raising=False)
    with pytest.raises(harness.MissingKeyError, match="TENNET_API_KEY"):
        harness.fetch_latest_balance_delta()


def test_poll_cadence_respects_tennets_published_rate_limit() -> None:
    """TenneT documents 10 req/min on /latest and warns that exceeding limits
    can block the key. Their recommended cadence is 5 polls per minute, one
    second after each 12-second refresh event."""
    assert len(harness.POLL_OFFSETS) == 5
    assert len(harness.POLL_OFFSETS) <= harness.MAX_REQUESTS_PER_MINUTE
    assert harness.MAX_REQUESTS_PER_MINUTE == 10
    # One second after each 12-second refresh: 1, 13, 25, 37, 49.
    assert harness.POLL_OFFSETS == (1, 13, 25, 37, 49)
    gaps = [b - a for a, b in zip(harness.POLL_OFFSETS, harness.POLL_OFFSETS[1:], strict=False)]
    assert all(g == 12 for g in gaps)


def test_uses_the_documented_endpoint() -> None:
    assert harness.BASE_URL == "https://api.tennet.eu"
    assert harness.LATEST_PATH == "/publications/v1/balance-delta-high-res/latest"


def test_extract_records_finds_a_timestamp_in_a_list_payload() -> None:
    payload = [{"timestamp": "2026-08-07T10:00:00Z", "value": 1.0}]
    got = harness.extract_records(payload)
    assert got[0]["timestamp"] == "2026-08-07T10:00:00Z"


def test_extract_records_unwraps_a_single_list_envelope() -> None:
    payload = {"data": [{"measureTime": "2026-08-07T10:00:00Z", "v": 2.0}]}
    assert harness.extract_records(payload)[0]["timestamp"] == "2026-08-07T10:00:00Z"


def test_extract_records_raises_rather_than_guessing() -> None:
    """A record with no timestamp field must fail loudly. Returning nothing
    would read as 'no new data' and yield a measurement of zero samples."""
    with pytest.raises(ValueError, match="No timestamp-like field"):
        harness.extract_records([{"value": 1.0}])


def test_extract_records_raises_on_an_ambiguous_envelope() -> None:
    with pytest.raises(ValueError, match="Cannot locate the record list"):
        harness.extract_records({"a": [1], "b": [2]})


def test_timestamps_are_parsed_as_utc() -> None:
    """TenneT states /latest timestamps are UTC. A naive parse would be read as
    host local time and corrupt every lag by the host's offset."""
    parsed = harness._parse_instant("2026-08-07T10:00:00")
    assert parsed.tzinfo is not None
    assert harness._parse_instant("2026-08-07T10:00:00Z") == parsed
