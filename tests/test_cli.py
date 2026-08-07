"""CLI smoke tests.

The CLI is how a human inspects this project, so a command that crashes is a
real defect even though nothing depends on it programmatically. These run
offline: no command that reaches the network is exercised here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.cli import main
from src.data import cache, vintage


@pytest.fixture(autouse=True)
def _tmp_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vintage, "VINTAGE_ROOT", tmp_path / "vintages")
    monkeypatch.setattr(cache, "DATA_ROOT", tmp_path / "data")


def test_status_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "MARKET RULES" in out
    assert "BLOCKED ON" in out
    # The unresolved field must be visibly flagged, not quietly listed.
    assert "balance_delta" in out
    assert "REFUSES" in out


def test_settle_reproduces_the_hand_worked_table(capsys: pytest.CaptureFixture[str]) -> None:
    """Same numbers as tests/test_settlement.py, surfaced for a human."""
    assert main(["settle", "--p-up", "120", "--p-down", "-15", "--p-mid", "30"]) == 0
    out = capsys.readouterr().out
    # State 2 is dual priced: long -15, short 120.
    assert "-15.00" in out and "120.00" in out
    assert "loss-making in BOTH directions" in out


def test_availability_marks_the_target_as_unusable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["availability", "--isp", "2026-06-17T14:30:00+00:00"]) == 0
    out = capsys.readouterr().out
    assert "REFUSED (lag unresolved)" in out, "balance_delta must show as refused"
    # The settled price is the target and can never be usable at decision time.
    settled = next(line for line in out.splitlines() if "imbalance_price_settled" in line)
    assert settled.strip().endswith("no")


def test_availability_reports_the_vintage_pair(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["availability", "--isp", "2026-06-17T14:30:00+00:00"]) == 0
    out = capsys.readouterr().out
    assert "vintage day_ahead" in out
    assert "vintage intraday" in out
    assert "forecast-error proxy computable: yes" in out


def test_availability_reports_the_proxy_as_unavailable_early_in_the_day(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """04:30 UTC is before the 07:00 local intraday update."""
    assert main(["availability", "--isp", "2026-06-17T04:30:00+00:00"]) == 0
    out = capsys.readouterr().out
    assert "forecast-error proxy computable: NO" in out


def test_track_record_on_an_empty_log_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty track record is a real problem worth a non-zero exit."""
    assert main(["track-record"]) == 1
    assert "empty" in capsys.readouterr().out


def test_reparse_with_nothing_stored_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reparse", "--dry-run"]) == 1
    assert "Nothing to reparse" in capsys.readouterr().out


def test_reparse_skips_live_vintages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Live forecasts are vintages. Feeding them into the settled parquet cache
    would collapse successive observations of the same target hour into one row
    and destroy exactly the revision history the vintage log exists to keep."""
    import json
    from datetime import UTC, datetime

    payload = json.dumps(
        {
            "hourly": {
                "time": ["2026-08-08T00:00", "2026-08-08T01:00"],
                "wind_speed_100m": [10.0, 11.0],
                "shortwave_radiation": [0.0, 5.0],
                "temperature_2m": [15.0, 15.5],
            }
        }
    ).encode()
    at = datetime(2026, 8, 8, tzinfo=UTC)
    cache.store_raw("openmeteo", "live_52.1_5.2_20260808T000000", payload, at)
    cache.store_raw("openmeteo", "forecast_52.1_5.2_2026-08-08_2026-08-08", payload, at)

    assert main(["reparse", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 response(s) reparsed" in out
    assert "1 live-forecast response(s) skipped" in out


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["not-a-command"])
    assert exc.value.code != 0
