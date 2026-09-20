"""Walk-forward evaluation: baselines, LEAR, and quantile-GBM on identical
folds and data (CLAUDE.md R4). Reports mean pinball loss, Diebold-Mariano
significance tests, calibration (empirical coverage, PIT histogram shape),
and segmented results by hour and year.

T2 target: `price_short`, the settlement price a BRP shortage pays.
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
    crps_from_quantiles,
    diebold_mariano,
    empirical_coverage,
    holm_bonferroni,
    mean_pinball,
    pinball_loss_per_obs,
    pit_values,
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
from src.models.gbm import QuantileGBM
from src.models.lear import LEARModel

MODEL_FACTORIES: dict[str, Callable[[], QuantileModel]] = {
    "persistence": PersistenceBaseline,
    "seasonal_naive_1d": lambda: SeasonalNaiveBaseline(period_isps=96),
    "seasonal_naive_1w": lambda: SeasonalNaiveBaseline(period_isps=672),
    "climatology": ClimatologyBaseline,
    "day_ahead": DayAheadBaseline,
    "lear": LEARModel,
    "gbm": QuantileGBM,
}

REFERENCE_MODEL = "climatology"
RESULTS_DIR = Path("work/evaluation")

_F = TypeVar("_F", pd.DataFrame, "pd.Series[float]")


def _resample_day_ahead(
    day_ahead_raw: pd.Series[float], isp_index: pd.DatetimeIndex
) -> pd.Series[float]:
    return day_ahead_raw.reindex(isp_index, method="ffill")


def _slice(frame: _F, start: datetime, end: datetime) -> _F:
    return frame[(frame.index >= start) & (frame.index < end)]


def _calibration_report(
    y_all: FloatArray, pred_all: FloatArray, label: str
) -> dict[str, object]:
    """Compute calibration metrics for one model's pooled predictions."""
    coverage = empirical_coverage(y_all, pred_all, QUANTILES)
    taus = np.asarray(QUANTILES)
    cal_error = np.abs(coverage - taus)
    pit = pit_values(y_all, pred_all, QUANTILES)
    pit_hist, _ = np.histogram(pit, bins=10, range=(0.0, 1.0))
    pit_cv = float(pit_hist.std() / pit_hist.mean()) if pit_hist.mean() > 0 else float("inf")

    print(f"\n--- Calibration: {label} ---")
    print(f"  Mean absolute calibration error: {cal_error.mean():.4f}")
    worst_q = taus[cal_error.argmax()]
    print(f"  Max calibration error:           {cal_error.max():.4f} (at q={worst_q:.2f})")
    print(f"  PIT CV (0 = perfect uniform):    {pit_cv:.4f}")
    crps = crps_from_quantiles(y_all, pred_all, QUANTILES)
    print(f"  CRPS (approx):                   {crps:.4f}")
    print("  Coverage table (nominal → empirical):")
    for i in range(0, len(taus), 4):
        parts = [f"  {taus[j]:.2f}→{coverage[j]:.3f}" for j in range(i, min(i + 4, len(taus)))]
        print("   " + "  ".join(parts))

    return {
        "mean_abs_cal_error": float(cal_error.mean()),
        "max_cal_error": float(cal_error.max()),
        "pit_cv": pit_cv,
        "crps": float(crps_from_quantiles(y_all, pred_all, QUANTILES)),
        "coverage": {
            f"{t:.2f}": float(c) for t, c in zip(taus, coverage, strict=True)
        },
        "pit_histogram": pit_hist.tolist(),
    }


def _segmented_report(
    y_series: pd.Series[float],
    pred_dict: dict[str, FloatArray],
    idx: pd.DatetimeIndex,
) -> dict[str, dict[str, dict[str, float]]]:
    """Segmented mean pinball by hour-of-day and by year."""
    results: dict[str, dict[str, dict[str, float]]] = {}

    segments = {"by_hour": idx.hour, "by_year": idx.year}
    for seg_name, seg_keys in segments.items():
        unique_keys = sorted(set(seg_keys))
        seg_result: dict[str, dict[str, float]] = {}
        for model_name, pred in pred_dict.items():
            model_seg: dict[str, float] = {}
            for k in unique_keys:
                mask = seg_keys == k
                if mask.sum() < 10:
                    continue
                y_seg = y_series.to_numpy()[mask]
                p_seg = pred[mask]
                model_seg[str(k)] = float(
                    mean_pinball(y_seg, p_seg, QUANTILES)
                )
            seg_result[model_name] = model_seg
        results[seg_name] = seg_result

    # Print summary
    print("\n=== Segmented pinball loss by hour (LEAR, GBM, climatology) ===")
    hour_models = ["lear", "gbm", "climatology"]
    available = [m for m in hour_models if m in pred_dict]
    header = f"{'hour':>5s}" + "".join(f" {m:>12s}" for m in available)
    print(header)
    by_hour = results.get("by_hour", {})
    for h in range(24):
        vals = []
        for m in available:
            seg = by_hour.get(m, {})
            v = seg.get(str(h))
            vals.append(f" {v:12.2f}" if v is not None else " " * 13)
        print(f"{h:5d}" + "".join(vals))

    print("\n=== Segmented pinball loss by year ===")
    by_year = results.get("by_year", {})
    for m in pred_dict:
        seg = by_year.get(m, {})
        if seg:
            parts = [f"{k}: {v:.2f}" for k, v in seg.items()]
            print(f"  {m:18s} {', '.join(parts)}")

    return results


def main() -> int:
    prices = cache.read_frame("imbalance_prices", PICASSO_START, HOLDOUT_START)
    day_ahead_cached = cache.read_frame(
        "day_ahead_price", PICASSO_START, HOLDOUT_START
    )
    day_ahead_raw = day_ahead_cached["day_ahead_price"]
    day_ahead = _resample_day_ahead(
        day_ahead_raw, pd.DatetimeIndex(prices.index)
    )

    X = build_features(prices, day_ahead=day_ahead)
    y = build_targets(prices)["price_short"]

    folds = generate_folds(
        datetime(2024, 11, 1, tzinfo=UTC),
        datetime(2026, 12, 1, tzinfo=UTC),
    )
    print(f"{len(folds)} folds: {folds[0].label} .. {folds[-1].label}\n")

    fold_losses: dict[str, list[float]] = {n: [] for n in MODEL_FACTORIES}
    per_obs_losses: dict[str, list[FloatArray]] = {n: [] for n in MODEL_FACTORIES}
    # For calibration and segmented reporting
    all_y: list[FloatArray] = []
    all_pred: dict[str, list[FloatArray]] = {n: [] for n in MODEL_FACTORIES}
    all_idx: list[pd.DatetimeIndex] = []

    for fold in folds:
        X_train = _slice(X, fold.train_start, fold.train_end)
        y_train = _slice(y, fold.train_start, fold.train_end)
        X_test = _slice(X, fold.test_start, fold.test_end)
        y_test = _slice(y, fold.test_start, fold.test_end)

        fold_y_stored = False
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
            all_pred[name].append(pred)
            if not fold_y_stored:
                all_y.append(y_arr)
                all_idx.append(pd.DatetimeIndex(X_test.index))
                fold_y_stored = True
        print(f"  {fold.label} done")

    # --- Aggregate results ---
    print("\n=== Mean pinball loss across folds (EUR/MWh, lower is better) ===")
    for name, values in fold_losses.items():
        if values:
            mean, std = np.mean(values), np.std(values)
            print(f"{name:18s} n={len(values):2d}  mean={mean:.4f}  std={std:.4f}")
        else:
            print(f"{name:18s} n=0   (never produced a prediction)")

    # --- DM tests ---
    ref_obs = per_obs_losses.get(REFERENCE_MODEL, [])
    if not ref_obs:
        print(f"\n'{REFERENCE_MODEL}' produced no predictions; skipping DM.")
        return 0

    ref_concat = np.concatenate(ref_obs)
    dm_results: list[tuple[str, float, float]] = []
    raw_pvalues: list[tuple[str, float]] = []

    print(f"\n=== Diebold-Mariano tests vs {REFERENCE_MODEL} ===")
    hdr = f"{'model':18s} {'DM stat':>10s} {'p-value':>10s} {'n_obs':>8s}"
    print(hdr)

    for name, obs_list in per_obs_losses.items():
        if name == REFERENCE_MODEL or not obs_list:
            continue
        model_concat = np.concatenate(obs_list)
        if len(model_concat) != len(ref_concat):
            n_m, n_r = len(model_concat), len(ref_concat)
            print(f"{name:18s}  SKIP ({n_m} vs {n_r})")
            continue
        max_lag = int(np.floor(len(model_concat) ** (1.0 / 3.0)))
        dm_stat, p_val = diebold_mariano(
            model_concat, ref_concat, max_lag=max_lag
        )
        dm_results.append((name, dm_stat, p_val))
        raw_pvalues.append((name, p_val))
        print(f"{name:18s} {dm_stat:10.4f} {p_val:10.6f} {len(model_concat):8d}")

    corrected: list[tuple[str, float, bool]] = []
    if raw_pvalues:
        corrected = holm_bonferroni(raw_pvalues)
        n_comp = len(raw_pvalues)
        print(f"\n=== Holm-Bonferroni ({n_comp} comparisons) ===")
        print(f"{'model':18s} {'adj p':>12s} {'sig 5%':>8s}")
        for label, adj_p, sig in corrected:
            print(f"{label:18s} {adj_p:12.6f} {'YES' if sig else 'no':>8s}")

    # --- Calibration ---
    y_concat = np.concatenate(all_y)
    idx_concat = pd.DatetimeIndex(np.concatenate([i.values for i in all_idx]))
    calibration: dict[str, dict[str, object]] = {}
    pred_concat: dict[str, FloatArray] = {}
    for name, preds in all_pred.items():
        if not preds:
            continue
        pc = np.concatenate(preds, axis=0)
        if len(pc) == len(y_concat):
            pred_concat[name] = pc
            calibration[name] = _calibration_report(y_concat, pc, name)

    # --- Segmented ---
    segmented = _segmented_report(
        pd.Series(y_concat, index=idx_concat),
        pred_concat,
        idx_concat,
    )

    # --- Save ---
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "folds": len(folds),
        "fold_range": f"{folds[0].label} .. {folds[-1].label}",
        "reference_model": REFERENCE_MODEL,
        "n_comparisons": len(raw_pvalues),
        "correction": "Holm-Bonferroni",
        "model_configs_tried": len(MODEL_FACTORIES),
        "fold_losses": {
            name: {
                "n": len(v),
                "mean": float(np.mean(v)),
                "std": float(np.std(v)),
            }
            for name, v in fold_losses.items()
            if v
        },
        "dm_tests": [
            {"model": n, "dm_stat": d, "p_value": p}
            for n, d, p in dm_results
        ],
        "holm_bonferroni": [
            {"model": lb, "adj_p_value": ap, "significant_005": sg}
            for lb, ap, sg in corrected
        ],
        "calibration": calibration,
        "segmented": segmented,
    }
    results_path = RESULTS_DIR / "walkforward_results.json"
    results_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nResults saved to {results_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
