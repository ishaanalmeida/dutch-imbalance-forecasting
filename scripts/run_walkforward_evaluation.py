"""Walk-forward evaluation: every mandatory baseline plus LEAR, on identical
folds and identical data (CLAUDE.md R4). Phase 2's first real, measured
result -- prints mean pinball loss per model per fold and overall, then runs
Diebold-Mariano significance tests comparing each model against the
climatological baseline.

T2 target: `price_short`, the settlement price a BRP shortage pays -- the
concrete column every existing baseline's feature naming already points at
(`lag_price_short_*`, `DayAheadBaseline` predicting the same level).

Segmentation this does NOT yet do, and why that is a known limitation rather
than an oversight: the day-ahead MTU changed from 60 to 15 minutes on
2025-10-01 (docs/DOMAIN_NOTES.md Q10), so `day_ahead_price` is a broadcast
hourly value before that date and a native 15-minute value after. Both are
resampled onto the ISP grid the same way (forward-fill), which is the
economically correct value in both regimes, but a result spanning that
boundary is not yet segmented at it the way CLAUDE.md's "segmented reporting"
asks for -- that is the natural next slice, not done here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import numpy as np
import pandas as pd

from src.data import cache
from src.evaluation.metrics import (
    QUANTILES,
    diebold_mariano,
    holm_bonferroni,
    mean_pinball,
    pinball_loss_per_obs,
)
from src.evaluation.walkforward import generate_folds
from src.features.builder import build_features
from src.features.targets import HOLDOUT_START, PICASSO_START, build_targets
from src.models.base import FloatArray, QuantileModel
from src.models.baselines import (
    ClimatologyBaseline,
    DayAheadBaseline,
    PersistenceBaseline,
    SeasonalNaiveBaseline,
)
from src.models.lear import LEARModel

MODEL_FACTORIES: dict[str, Callable[[], QuantileModel]] = {
    "persistence": PersistenceBaseline,
    "seasonal_naive_1d": lambda: SeasonalNaiveBaseline(period_isps=96),
    "seasonal_naive_1w": lambda: SeasonalNaiveBaseline(period_isps=672),
    "climatology": ClimatologyBaseline,
    "day_ahead": DayAheadBaseline,
    "lear": LEARModel,
}

REFERENCE_MODEL = "climatology"

RESULTS_DIR = Path("work/evaluation")


def _resample_day_ahead(
    day_ahead_raw: pd.Series[float], isp_index: pd.DatetimeIndex
) -> pd.Series[float]:
    """Forward-fill onto the 15-min ISP grid. Correct in both MTU regimes
    (docs/DOMAIN_NOTES.md Q10): pre-2025-10-01 this broadcasts each hourly
    auction price across its four ISPs; post-2025-10-01 the source is already
    15-minute and this is an exact-match reindex."""
    return day_ahead_raw.reindex(isp_index, method="ffill")


_F = TypeVar("_F", pd.DataFrame, "pd.Series[float]")


def _slice(frame: _F, start: datetime, end: datetime) -> _F:
    return frame[(frame.index >= start) & (frame.index < end)]


def main() -> int:
    prices = cache.read_frame("imbalance_prices", PICASSO_START, HOLDOUT_START)
    day_ahead_cached = cache.read_frame("day_ahead_price", PICASSO_START, HOLDOUT_START)
    day_ahead_raw = day_ahead_cached["day_ahead_price"]
    day_ahead = _resample_day_ahead(day_ahead_raw, pd.DatetimeIndex(prices.index))

    X = build_features(prices, day_ahead=day_ahead)
    y = build_targets(prices)["price_short"]

    folds = generate_folds(datetime(2024, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
    print(f"{len(folds)} folds: {folds[0].label} .. {folds[-1].label}\n")

    fold_losses: dict[str, list[float]] = {name: [] for name in MODEL_FACTORIES}
    per_obs_losses: dict[str, list[FloatArray]] = {name: [] for name in MODEL_FACTORIES}

    for fold in folds:
        X_train = _slice(X, fold.train_start, fold.train_end)
        y_train = _slice(y, fold.train_start, fold.train_end)
        X_test = _slice(X, fold.test_start, fold.test_end)
        y_test = _slice(y, fold.test_start, fold.test_end)

        for name, make_model in MODEL_FACTORIES.items():
            model = make_model()
            try:
                model.fit(X_train, y_train)
                pred: FloatArray = model.predict_quantiles(X_test, QUANTILES)
                y_arr = y_test.to_numpy()
                loss = mean_pinball(y_arr, pred, QUANTILES)
                obs_loss = pinball_loss_per_obs(y_arr, pred, QUANTILES)
            except Exception as exc:
                print(f"  {fold.label} {name:18s} FAILED: {exc!r}")
                continue
            fold_losses[name].append(loss)
            per_obs_losses[name].append(obs_loss)
        print(f"  {fold.label} done")

    print("\n=== Mean pinball loss across folds (EUR/MWh, lower is better) ===")
    for name, values in fold_losses.items():
        if values:
            mean, std = np.mean(values), np.std(values)
            print(f"{name:18s} n={len(values):2d}  mean={mean:.4f}  std={std:.4f}")
        else:
            print(f"{name:18s} n=0   (never produced a prediction)")

    # --- Diebold-Mariano significance tests ---
    ref_obs = per_obs_losses.get(REFERENCE_MODEL, [])
    if not ref_obs:
        print(f"\nReference model '{REFERENCE_MODEL}' produced no predictions; skipping DM tests.")
        return 0

    ref_concat = np.concatenate(ref_obs)
    dm_results: list[tuple[str, float, float]] = []
    raw_pvalues: list[tuple[str, float]] = []

    print(f"\n=== Diebold-Mariano tests vs {REFERENCE_MODEL} (HAC standard errors) ===")
    print(f"{'model':18s} {'DM stat':>10s} {'p-value':>10s} {'n_obs':>8s} {'max_lag':>8s}")

    for name, obs_list in per_obs_losses.items():
        if name == REFERENCE_MODEL or not obs_list:
            continue
        model_concat = np.concatenate(obs_list)
        if len(model_concat) != len(ref_concat):
            n_m, n_r = len(model_concat), len(ref_concat)
            print(f"{name:18s}  SKIPPED: different n_obs ({n_m} vs {n_r})")
            continue
        max_lag = int(np.floor(len(model_concat) ** (1.0 / 3.0)))
        dm_stat, p_val = diebold_mariano(model_concat, ref_concat, max_lag=max_lag)
        dm_results.append((name, dm_stat, p_val))
        raw_pvalues.append((name, p_val))
        print(f"{name:18s} {dm_stat:10.4f} {p_val:10.6f} {len(model_concat):8d} {max_lag:8d}")

    if raw_pvalues:
        corrected = holm_bonferroni(raw_pvalues)
        n_comparisons = len(raw_pvalues)
        print(f"\n=== Holm-Bonferroni correction ({n_comparisons} comparisons) ===")
        print(f"{'model':18s} {'adj p-value':>12s} {'sig at 5%':>10s}")
        for label, adj_p, sig in corrected:
            print(f"{label:18s} {adj_p:12.6f} {'YES' if sig else 'no':>10s}")

    # Save results for downstream use
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "folds": len(folds),
        "fold_range": f"{folds[0].label} .. {folds[-1].label}",
        "reference_model": REFERENCE_MODEL,
        "n_comparisons": len(raw_pvalues),
        "correction": "Holm-Bonferroni",
        "fold_losses": {
            name: {"n": len(v), "mean": float(np.mean(v)), "std": float(np.std(v))}
            for name, v in fold_losses.items()
            if v
        },
        "dm_tests": [
            {
                "model": name, "dm_stat": dm, "p_value": p,
                "n_obs": len(np.concatenate(per_obs_losses[name])),
            } for name, dm, p in dm_results
        ],
        "holm_bonferroni": [
            {"model": label, "adj_p_value": adj_p, "significant_005": sig}
            for label, adj_p, sig in (corrected if raw_pvalues else [])
        ],
    }
    results_path = RESULTS_DIR / "walkforward_results.json"
    results_path.write_text(json.dumps(summary, indent=2))
    print(f"\nResults saved to {results_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
