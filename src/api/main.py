"""Biosync NL Forecasting API.

Phase B: live probabilistic forecasts with model versioning and scored
track record, plus the original backtest/evaluation result endpoints.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.api.forecast_service import ForecastService

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
BACKTEST_PATH = ROOT / "work" / "backtest" / "backtest_results.json"
EVAL_PATH = ROOT / "work" / "evaluation" / "walkforward_results.json"

service = ForecastService()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Training model at startup...")
    try:
        info = service.train()
        logger.info(
            "Model ready: %s (%d ISPs, data to %s)",
            info.get("model_id"),
            info.get("n_training_isps"),
            info.get("training_window", {}).get("end"),
        )
    except Exception:
        logger.exception("Model training failed — forecast endpoints will 503")
    yield


app = FastAPI(
    title="Biosync NL Forecasting API",
    description=(
        "Probabilistic imbalance-price forecasting for the Dutch market. "
        "Quantile forecasts with calibrated uncertainty, regulation-state "
        "probabilities, and dispatch recommendations."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
)


def _load_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)  # type: ignore[no-any-return]


def _require_model() -> None:
    if not service.ready:
        raise HTTPException(503, "Model not trained — check server logs")


# ── v1 endpoints (Phase B) ──────────────────────────────────────────────


@app.get("/v1/forecast")
def get_forecast() -> dict[str, Any]:
    """Live probabilistic forecast for the next ISP."""
    _require_model()
    return service.forecast()


@app.get("/v1/model")
def get_model_info() -> dict[str, Any]:
    """Current model metadata and version."""
    return service.model_info()


@app.post("/v1/retrain")
def retrain() -> dict[str, Any]:
    """Retrain the model on latest cached data."""
    return service.train()


@app.get("/v1/track-record")
def get_track_record(
    limit: int = Query(100, ge=1, le=10000),
) -> dict[str, Any]:
    """Scored forecast track record from the live log."""
    return service.track_record(limit=limit)


# ── health ───────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_ready": service.ready,
        "model_id": service.model_info().get("model_id") if service.ready else None,
    }


# ── legacy endpoints (backtest results) ─────────────────────────────────


@app.get("/api/backtest")
def backtest_results() -> dict[str, Any]:
    return _load_json(BACKTEST_PATH)


@app.get("/api/evaluation")
def evaluation_results() -> dict[str, Any]:
    return _load_json(EVAL_PATH)


@app.get("/api/backtest/frontier")
def efficient_frontier() -> list[Any]:
    bt = _load_json(BACKTEST_PATH)
    return bt["efficient_frontier"]  # type: ignore[no-any-return]


@app.get("/api/backtest/saturation")
def saturation_curve() -> list[Any]:
    bt = _load_json(BACKTEST_PATH)
    return bt["saturation_curve"]  # type: ignore[no-any-return]


@app.get("/api/whatif")
def whatif(
    power_mw: float = Query(10.0, ge=0.1, le=500),
    duration_h: float = Query(4.0, ge=0.5, le=12),
    efficiency: float = Query(0.90, ge=0.5, le=0.99),
    degradation: float = Query(5.0, ge=0, le=50),
    risk_aversion: float = Query(0.5, ge=0, le=1),
) -> dict[str, Any]:
    """Scaled revenue estimate from backtest results."""
    import numpy as np

    bt = _load_json(BACKTEST_PATH)
    sat = bt["saturation_curve"]
    sat_mw = np.array([s["capacity_mw"] for s in sat])
    sat_rpm = np.array([s["revenue_per_mw"] for s in sat])
    rpm = float(np.interp(power_mw, sat_mw, sat_rpm))

    base_eff = bt["battery"]["efficiency_charge"] * bt["battery"]["efficiency_discharge"]
    eff_scale = efficiency / base_eff
    base_dur = bt["battery"]["energy_mwh"] / bt["battery"]["power_mw"]
    dur_scale = min(duration_h / base_dur, 1.5)

    n_test_years = bt["n_test_isps"] * 0.25 / 8760
    annual = rpm * power_mw / n_test_years * eff_scale * dur_scale

    ci = bt["bootstrap_ci_95"]["deterministic"]
    ci_lo = annual * ci["lo"] / ci["mean"]
    ci_hi = annual * ci["hi"] / ci["mean"]

    return {
        "power_mw": power_mw,
        "energy_mwh": power_mw * duration_h,
        "est_annual_revenue_eur": round(annual, 0),
        "ci_95_lo": round(ci_lo, 0),
        "ci_95_hi": round(ci_hi, 0),
        "impact_model": bt["market_impact_model"],
        "note": "Scaled estimate from backtest; actual dispatch requires re-optimization.",
    }
