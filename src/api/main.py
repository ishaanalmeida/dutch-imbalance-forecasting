"""FastAPI backend — programmatic access to backtest and evaluation results.

Endpoints mirror the four demo screens: forecast (placeholder), track record,
backtest results, and what-if dispatch simulation.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parent.parent.parent
BACKTEST_PATH = ROOT / "work" / "backtest" / "backtest_results.json"
EVAL_PATH = ROOT / "work" / "evaluation" / "walkforward_results.json"

app = FastAPI(
    title="NL Imbalance Lab API",
    description="Dutch imbalance market forecasting & battery dispatch results.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
)


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/backtest")
def backtest_results() -> dict:
    return _load_json(BACKTEST_PATH)


@app.get("/api/evaluation")
def evaluation_results() -> dict:
    return _load_json(EVAL_PATH)


@app.get("/api/backtest/frontier")
def efficient_frontier() -> list:
    bt = _load_json(BACKTEST_PATH)
    return bt["efficient_frontier"]


@app.get("/api/backtest/saturation")
def saturation_curve() -> list:
    bt = _load_json(BACKTEST_PATH)
    return bt["saturation_curve"]


@app.get("/api/whatif")
def whatif(
    power_mw: float = Query(10.0, ge=0.1, le=500),
    duration_h: float = Query(4.0, ge=0.5, le=12),
    efficiency: float = Query(0.90, ge=0.5, le=0.99),
    degradation: float = Query(5.0, ge=0, le=50),
    risk_aversion: float = Query(0.5, ge=0, le=1),
) -> dict:
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
