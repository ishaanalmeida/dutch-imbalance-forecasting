"""Read side of the forecasting API: serves the live forecast log.

Phase B of the Biosync plan — the forecasting-as-a-service wedge.

The API never trains or predicts. The scheduled job (`src.jobs.live_forecast`,
run by live-forecast.yml) is the single source of forecasts, so what the API
serves is exactly what the track record scores — the two cannot drift apart.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.data import cache
from src.data.timebase import ISP_MINUTES
from src.jobs.live_forecast import LOG_FILE

logger = logging.getLogger(__name__)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def read_ex_ante(path: Path | None = None) -> list[dict[str, Any]]:
    """All logged forecasts issued before their target ISP began, in log order.

    Entries issued after their target began are hindcasts from the pre-ADR-034
    job; they stay in the append-only log but never count as forecasts.
    """
    # ponytail: frontend/app.py:load_forecast_log applies the same filter; it can't
    # import src (Streamlit only puts frontend/ on sys.path). Merge if that changes.
    path = path or LOG_FILE
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed forecast log line: %s", line[:80])
            continue
        if _ts(entry["forecast_issued_at"]) <= _ts(entry["target_isp"]):
            entries.append(entry)
    return entries


def live_forecast(now: datetime | None = None, path: Path | None = None) -> dict[str, Any]:
    """Latest-issued forecast for every ISP that has not yet ended."""
    now = now or datetime.now(UTC)
    isp = timedelta(minutes=ISP_MINUTES)
    entries = read_ex_ante(path)
    latest: dict[str, dict[str, Any]] = {}
    for e in entries:
        if _ts(e["target_isp"]) + isp > now:
            latest[e["target_isp"]] = e  # append-ordered, so later issue wins
    forecasts = sorted(latest.values(), key=lambda e: _ts(e["target_isp"]))
    last_issued = max((e["forecast_issued_at"] for e in entries), key=_ts, default=None)
    return {
        "market": "NL",
        "model_class": "QuantileGBM",  # retrained from scratch on every job run
        "last_issued_at": last_issued,
        "forecasts": forecasts,
    }


def track_record(limit: int = 100, path: Path | None = None) -> dict[str, Any]:
    """Every ex-ante forecast, scored against settled prices where available.

    Scoring reads the local price cache, so it is only as current as that cache.
    """
    entries = read_ex_ante(path)
    if not entries:
        return {"n_forecasts": 0, "n_scored": 0, "summary": {}, "recent": []}

    targets = [pd.Timestamp(e["target_isp"]) for e in entries]
    prices = cache.read_frame(
        "imbalance_prices",
        min(targets).to_pydatetime(),
        (max(targets) + pd.Timedelta(minutes=ISP_MINUTES)).to_pydatetime(),
    )

    scored: list[dict[str, Any]] = []
    for entry, ts in zip(entries, targets, strict=True):
        if prices.empty or ts not in prices.index:
            continue
        actual = float(prices.loc[ts]["price_short"])
        lead = ts - pd.Timestamp(entry["forecast_issued_at"])
        scored.append(
            {
                **entry,
                "lead_minutes": round(lead.total_seconds() / 60),
                "actual_price_short": actual,
                "error": round(entry["median"] - actual, 2),
                "pinball_loss": _entry_pinball(entry["quantiles"], actual),
            }
        )

    summary: dict[str, Any] = {"n_scored": len(scored)}
    if scored:
        errors = [e["error"] for e in scored]
        summary["mae"] = round(float(np.mean(np.abs(errors))), 2)
        summary["mean_error"] = round(float(np.mean(errors)), 2)
        summary["mean_pinball_loss"] = round(float(np.mean([e["pinball_loss"] for e in scored])), 2)

    return {
        "n_forecasts": len(entries),
        "n_scored": len(scored),
        "summary": summary,
        "recent": scored[-limit:],
    }


def _entry_pinball(quantiles: dict[str, float], actual: float) -> float:
    """Mean pinball loss across all quantiles for one forecast entry."""
    losses = []
    for tau_str, pred in quantiles.items():
        tau = float(tau_str)
        diff = actual - pred
        losses.append(max(tau * diff, (tau - 1) * diff))
    return round(float(np.mean(losses)), 2) if losses else 0.0
