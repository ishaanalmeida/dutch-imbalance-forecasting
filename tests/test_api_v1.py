"""Smoke tests for the v1 forecasting API endpoints.

Uses FastAPI's TestClient — no server startup needed, no real model training.
The forecast_service is tested via its public interface through the API.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


def _make_client() -> TestClient:
    """Import inside function so the lifespan doesn't try to train on import."""
    from src.api.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_health_reports_model_status() -> None:
    with patch("src.api.main.service") as mock_svc:
        mock_svc.ready = False
        mock_svc.model_info.return_value = {}
        client = _make_client()
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert "status" in body
        assert "model_ready" in body


def test_model_info_not_trained() -> None:
    with patch("src.api.main.service") as mock_svc:
        mock_svc.model_info.return_value = {"status": "not_trained"}
        mock_svc.ready = False
        client = _make_client()
        r = client.get("/v1/model")
        assert r.status_code == 200
        assert r.json()["status"] == "not_trained"


def test_forecast_503_when_not_ready() -> None:
    with patch("src.api.main.service") as mock_svc:
        mock_svc.ready = False
        client = _make_client()
        r = client.get("/v1/forecast")
        assert r.status_code == 503


def test_forecast_returns_quantiles_when_ready() -> None:
    mock_response = {
        "model_version": "gbm-test",
        "forecast_issued_at": "2026-09-22T10:00:00+00:00",
        "market": "NL",
        "target_isp": "2026-09-22T10:15:00+00:00",
        "quantiles": {"0.50": 42.0},
        "median": 42.0,
        "regulation_state": {"single_price": 0.8, "dual_price": 0.2},
        "dispatch_recommendation": {"action": "hold", "reason": "test"},
    }
    with patch("src.api.main.service") as mock_svc:
        mock_svc.ready = True
        mock_svc.forecast.return_value = mock_response
        client = _make_client()
        r = client.get("/v1/forecast")
        assert r.status_code == 200
        body = r.json()
        assert body["market"] == "NL"
        assert "quantiles" in body
        assert "model_version" in body


def test_track_record_empty() -> None:
    with patch("src.api.main.service") as mock_svc:
        mock_svc.track_record.return_value = {
            "n_forecasts": 0,
            "n_scored": 0,
            "summary": {},
            "recent": [],
        }
        client = _make_client()
        r = client.get("/v1/track-record")
        assert r.status_code == 200
        assert r.json()["n_forecasts"] == 0
