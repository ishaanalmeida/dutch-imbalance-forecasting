"""Model lifecycle for the forecasting API.

Trains the GBM at startup on cached data, serves live predictions.
Phase B of the Biosync plan — the forecasting-as-a-service wedge.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.data import cache
from src.evaluation.metrics import QUANTILES
from src.features.builder import build_features
from src.features.targets import PICASSO_START, build_targets
from src.jobs.live_forecast import _dispatch_recommendation, _regulation_state_probs
from src.models.gbm import QuantileGBM

LOG_DIR = Path("forecast_log")
LOG_FILE = LOG_DIR / "forecasts.jsonl"


class ForecastService:
    """Holds a trained model and serves live predictions."""

    def __init__(self) -> None:
        self.model: QuantileGBM | None = None
        self.trained_at: datetime | None = None
        self.n_training_isps: int = 0
        self.training_start: datetime | None = None
        self.training_end: datetime | None = None
        self._features: pd.DataFrame | None = None
        self._data_hash: str = ""

    @property
    def ready(self) -> bool:
        return self.model is not None

    def train(self) -> dict[str, Any]:
        """Train on all available post-PICASSO data. Returns model metadata."""
        end = datetime.now(UTC)
        prices = cache.read_frame("imbalance_prices", PICASSO_START, end)
        if prices.empty:
            raise RuntimeError("No cached price data — run data fetchers first")

        da_cached = cache.read_frame("day_ahead_price", PICASSO_START, end)
        da: pd.Series[Any] | None = None
        if not da_cached.empty and "day_ahead_price" in da_cached.columns:
            da_raw: pd.Series[Any] = da_cached["day_ahead_price"]
            da = da_raw.reindex(pd.DatetimeIndex(prices.index), method="ffill")

        X = build_features(prices, day_ahead=da)
        y: pd.Series[Any] = build_targets(prices)["price_short"]

        model = QuantileGBM()
        model.fit(X, y)

        self.model = model
        self.trained_at = datetime.now(UTC)
        self.n_training_isps = len(X)
        idx = pd.DatetimeIndex(X.index)
        self.training_start = idx[0].to_pydatetime()
        self.training_end = idx[-1].to_pydatetime()
        self._features = X
        self._data_hash = hashlib.sha256(
            f"{self.training_start}:{self.training_end}:{self.n_training_isps}".encode()
        ).hexdigest()[:12]

        return self.model_info()

    def model_info(self) -> dict[str, Any]:
        """Current model metadata and version."""
        if self.model is None:
            return {"status": "not_trained"}
        return {
            "model_id": f"gbm-{self._data_hash}",
            "model_class": "QuantileGBM",
            "trained_at": _iso(self.trained_at),
            "training_window": {
                "start": _iso(self.training_start),
                "end": _iso(self.training_end),
            },
            "n_training_isps": self.n_training_isps,
            "quantiles": list(QUANTILES),
            "data_hash": self._data_hash,
            "data_age_hours": _age_hours(self.training_end),
        }

    def forecast(self) -> dict[str, Any]:
        """Produce quantile forecast for the next ISP."""
        if self.model is None or self._features is None:
            raise RuntimeError("Model not trained")

        issued_at = datetime.now(UTC)
        last_idx = pd.DatetimeIndex(self._features.index)[-1]
        target_isp = last_idx + pd.Timedelta(minutes=15)

        q_pred = self.model.predict_quantiles(self._features.iloc[-1:], QUANTILES)
        taus = list(QUANTILES)
        q_dict = {f"{t:.2f}": round(float(q_pred[0][j]), 2) for j, t in enumerate(taus)}

        return {
            "model_version": f"gbm-{self._data_hash}",
            "forecast_issued_at": issued_at.isoformat(),
            "market": "NL",
            "target_isp": target_isp.isoformat(),
            "quantiles": q_dict,
            "median": round(float(q_pred[0][taus.index(0.5)]), 2),
            "regulation_state": _regulation_state_probs(q_dict),
            "dispatch_recommendation": _dispatch_recommendation(q_dict),
        }

    def track_record(self, limit: int = 100) -> dict[str, Any]:
        """Scored track record from the live forecast log."""
        if not LOG_FILE.exists():
            return {"n_forecasts": 0, "n_scored": 0, "summary": {}, "recent": []}

        raw = LOG_FILE.read_text().strip()
        if not raw:
            return {"n_forecasts": 0, "n_scored": 0, "summary": {}, "recent": []}

        entries: list[dict[str, Any]] = [
            json.loads(line) for line in raw.split("\n") if line.strip()
        ]
        if not entries:
            return {"n_forecasts": 0, "n_scored": 0, "summary": {}, "recent": []}

        earliest_ts = pd.Timestamp(entries[0]["target_isp"])
        latest_ts = pd.Timestamp(entries[-1]["target_isp"])
        prices = cache.read_frame(
            "imbalance_prices",
            earliest_ts.to_pydatetime(),
            (latest_ts + pd.Timedelta(minutes=15)).to_pydatetime(),
        )

        scored: list[dict[str, Any]] = []
        for entry in entries:
            ts = pd.Timestamp(entry["target_isp"])
            if prices.empty or ts not in prices.index:
                continue
            actual = float(prices.loc[ts]["price_short"])
            entry["actual_price_short"] = actual
            entry["error"] = round(entry["median"] - actual, 2)
            entry["pinball_loss"] = _entry_pinball(entry["quantiles"], actual)
            scored.append(entry)

        summary: dict[str, Any] = {"n_scored": len(scored)}
        if scored:
            errors = [e["error"] for e in scored]
            pbs = [e["pinball_loss"] for e in scored]
            summary["mae"] = round(float(np.mean(np.abs(errors))), 2)
            summary["mean_error"] = round(float(np.mean(errors)), 2)
            summary["mean_pinball_loss"] = round(float(np.mean(pbs)), 2)

        return {
            "n_forecasts": len(entries),
            "n_scored": len(scored),
            "summary": summary,
            "recent": entries[-limit:],
        }


def _entry_pinball(quantiles: dict[str, float], actual: float) -> float:
    """Mean pinball loss across all quantiles for one forecast entry."""
    losses = []
    for tau_str, pred in quantiles.items():
        tau = float(tau_str)
        diff = actual - pred
        losses.append(max(tau * diff, (tau - 1) * diff))
    return round(float(np.mean(losses)), 2) if losses else 0.0


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _age_hours(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return round((datetime.now(UTC) - dt).total_seconds() / 3600, 1)
