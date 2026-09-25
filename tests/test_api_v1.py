"""Tests for the v1 forecasting API: it serves the live job's log, never a model.

Uses FastAPI's TestClient against a temporary forecast log.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import forecast_service
from src.api.main import app

NOW = datetime(2026, 9, 25, 12, 5, tzinfo=UTC)


def _entry(issued: str, target: str, median: float) -> dict[str, object]:
    return {
        "forecast_issued_at": f"2026-09-25T{issued}:00+00:00",
        "target_isp": f"2026-09-25T{target}:00+00:00",
        "quantiles": {"0.10": median - 10, "0.50": median, "0.90": median + 10},
        "median": median,
        "regulation_state": {"single_price": 0.8, "dual_price": 0.2},
        "dispatch_recommendation": {"action": "hold", "reason": "test"},
    }


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "forecasts.jsonl"
    entries = [
        _entry("11:00", "00:00", 1.0),  # hindcast (issued after target began)
        _entry("10:00", "11:45", 2.0),  # past target
        _entry("10:00", "12:00", 3.0),  # current ISP, superseded below
        _entry("10:00", "12:15", 4.0),
        _entry("11:30", "12:00", 5.0),  # later issue for 12:00 wins
    ]
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def test_hindcasts_are_never_forecasts(log: Path) -> None:
    medians = [e["median"] for e in forecast_service.read_ex_ante(log)]
    assert medians == [2.0, 3.0, 4.0, 5.0]


def test_live_forecast_is_latest_issue_per_unended_isp(log: Path) -> None:
    body = forecast_service.live_forecast(now=NOW, path=log)
    assert [(f["target_isp"][11:16], f["median"]) for f in body["forecasts"]] == [
        ("12:00", 5.0),
        ("12:15", 4.0),
    ]
    assert body["last_issued_at"] == "2026-09-25T11:30:00+00:00"


def test_forecast_503_when_every_target_is_past(tmp_path: Path) -> None:
    stale = tmp_path / "forecasts.jsonl"
    stale.write_text(json.dumps(_entry("10:00", "12:00", 1.0)).replace("2026", "2020") + "\n")
    with patch.object(forecast_service, "LOG_FILE", stale):
        r = TestClient(app).get("/v1/forecast")
    assert r.status_code == 503
    assert "2020-09-25T10:00" in r.json()["detail"]


def test_empty_log_track_record(tmp_path: Path) -> None:
    body = forecast_service.track_record(path=tmp_path / "missing.jsonl")
    assert body["n_forecasts"] == 0


def test_track_record_scores_settled_ex_ante_entries(log: Path) -> None:
    prices = pd.DataFrame(
        {"price_short": [12.0]}, index=pd.DatetimeIndex([pd.Timestamp("2026-09-25T11:45Z")])
    )
    with patch("src.api.forecast_service.cache.read_frame", return_value=prices):
        body = forecast_service.track_record(path=log)
    assert body["n_forecasts"] == 4
    assert body["n_scored"] == 1
    (scored,) = body["recent"]
    assert scored["error"] == -10.0
    assert scored["lead_minutes"] == 105
    assert body["summary"]["mae"] == 10.0


def test_health_is_ok() -> None:
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
