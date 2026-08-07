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


def test_auth_header_matches_the_spec() -> None:
    """Confirmed from the spec page's Authorize dialog: scheme `apikey`,
    name `apikey`, in `header`. TenneT renamed it from Azure API Management's
    default `Ocp-Apim-Subscription-Key`; using the Azure default would fail
    with a 401 that looks like a bad key rather than a bad header name."""
    assert harness.KEY_HEADER == "apikey"


def _envelope(points: list[dict[str, object]]) -> dict[str, object]:
    """TenneT's Aether envelope, matching the shape observed live 2026-08-07.

    Structural copy of a real response with values reduced to what the parser
    reads. Not a verbatim recording -- the point is to pin the nesting
    (Response -> TimeSeries -> Period -> points), which is what the parser
    depends on.
    """
    return {
        "Response": {
            "informationType": "BALANCE_DELTA_HIGH_RES",
            "period.timeInterval": {
                "start": "2026-08-07T11:35:48Z",
                "end": "2026-08-07T12:05:48Z",
            },
            "TimeSeries": [
                {
                    "mRID": 1,
                    "quantity_Measurement_Unit_name": "MAW",
                    "Period": [{"points": points}],
                }
            ],
        }
    }


def _point(start: str, end: str) -> dict[str, object]:
    return {
        "timeInterval_start": start,
        "timeInterval_end": end,
        "sequence": "4080",
        "power_afrr_in": "0.0",
        "power_afrr_out": "19.0",
        "power_picasso_in": "414.8",
        "power_picasso_out": "0.0",
        "max_upw_regulation_price": None,
        "min_downw_regulation_price": "85.26",
        "mid_price": "85.00",
    }


def test_extract_records_walks_the_aether_envelope() -> None:
    payload = _envelope(
        [
            _point("2026-08-07T11:35:48Z", "2026-08-07T11:36:00Z"),
            _point("2026-08-07T11:36:00Z", "2026-08-07T11:36:12Z"),
        ]
    )
    got = harness.extract_records(payload)
    assert len(got) == 2


def test_timestamp_is_the_interval_END_not_its_start() -> None:
    """A point covers [start, end), so the observation is only complete at
    `end`. Using `start` would understate every measured lag by one 12-second
    tick -- an error in the permissive direction."""
    payload = _envelope([_point("2026-08-07T11:35:48Z", "2026-08-07T11:36:00Z")])
    assert harness.extract_records(payload)[0]["timestamp"] == "2026-08-07T11:36:00Z"


def test_component_prices_survive_into_the_record() -> None:
    """max_upw / min_downw / mid map to p_up / p_down / p_mid in the settlement
    rules, so the feed can price an ISP in near-real time. Nulls are meaningful:
    no upward regulation was active in this point."""
    payload = _envelope([_point("2026-08-07T11:35:48Z", "2026-08-07T11:36:00Z")])
    record = harness.extract_records(payload)[0]["record"]
    assert record["max_upw_regulation_price"] is None
    assert record["min_downw_regulation_price"] == "85.26"
    assert record["mid_price"] == "85.00"


def test_raises_on_an_unrecognised_envelope() -> None:
    with pytest.raises(ValueError, match="expected a top-level 'Response'"):
        harness.extract_records({"data": []})


def test_raises_when_a_point_has_no_end_timestamp() -> None:
    """Must fail loudly rather than skip: a silently dropped point reads as
    'no new data' and biases the measurement."""
    with pytest.raises(ValueError, match="no timeInterval_end"):
        harness.extract_records(_envelope([{"sequence": "1"}]))


def test_raises_when_the_envelope_parses_but_is_empty() -> None:
    """Zero points is not the same as zero new points."""
    with pytest.raises(ValueError, match="no points"):
        harness.extract_records(_envelope([]))
