"""Walk-forward evaluation: baselines, LEAR, and quantile-GBM on identical
folds and data (CLAUDE.md R4). Reports mean pinball loss, Diebold-Mariano
significance tests, calibration (empirical coverage, PIT histogram shape),
point accuracy (MAE, RMSE), feature importance, and segmented results by
hour, year, dual-pricing state, and season.

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
    mae,
    mean_pinball,
    pinball_loss_per_obs,
    pit_values,
    rmse,
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

    median_idx = list(QUANTILES).index(0.5)
    median_pred = pred_all[:, median_idx]
    point_mae = mae(y_all, median_pred)
    point_rmse = rmse(y_all, median_pred)

    print(f"\n--- Calibration: {label} ---")
    print(f"  Mean absolute calibration error: {cal_error.mean():.4f}")
    worst_q = taus[cal_error.argmax()]
    print(f"  Max calibration error:           {cal_error.max():.4f} (at q={worst_q:.2f})")
    print(f"  PIT CV (0 = perfect uniform):    {pit_cv:.4f}")
    crps = crps_from_quantiles(y_all, pred_all, QUANTILES)
    print(f"  CRPS (approx):                   {crps:.4f}")
    print(f"  MAE (median forecast):           {point_mae:.2f}")
    print(f"  RMSE (median forecast):          {point_rmse:.2f}")
    print("  (sMAPE omitted: meaningless near zero prices, which occur frequently)")
    print("  Coverage table (nominal -> empirical):")
    for i in range(0, len(taus), 4):
        parts = [f"  {taus[j]:.2f}->{coverage[j]:.3f}" for j in range(i, min(i + 4, len(taus)))]
        print("   " + "  ".join(parts))

    return {
        "mean_abs_cal_error": float(cal_error.mean()),
        "max_cal_error": float(cal_error.max()),
        "pit_cv": pit_cv,
        "crps": float(crps_from_quantiles(y_all, pred_all, QUANTILES)),
        "mae": point_mae,
        "rmse": point_rmse,
        "coverage": {
            f"{t:.2f}": float(c) for t, c in zip(taus, coverage, strict=True)
        },
        "pit_histogram": pit_hist.tolist(),
    }


def _season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def _segmented_report(
    y_series: pd.Series[float],
    pred_dict: dict[str, FloatArray],
    idx: pd.DatetimeIndex,
    is_dual: pd.Series[bool] | None = None,
) -> dict[str, dict[str, dict[str, float]]]:
    """Segmented mean pinball by hour, year, dual-pricing state, and season."""
    results: dict[str, dict[str, dict[str, float]]] = {}

    season_keys = pd.Index([_season(m) for m in idx.month])
    segments: dict[str, pd.Index[str]] = {
        "by_hour": pd.Index([str(h) for h in idx.hour]),
        "by_year": pd.Index([str(y) for y in idx.year]),
        "by_season": season_keys,
    }
    if is_dual is not None:
        segments["by_dual_priced"] = pd.Index(
            [str(v) for v in is_dual.reindex(idx).to_numpy()]
        )

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

    # Print summaries
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

    print("\n=== Segmented pinball loss by season (LEAR, GBM, climatology) ===")
    by_season = results.get("by_season", {})
    for s in ["winter", "spring", "summer", "autumn"]:
        vals = []
        for m in available:
            seg = by_season.get(m, {})
            v = seg.get(s)
            vals.append(f" {v:8.2f}" if v is not None else " " * 9)
        print(f"  {s:8s}" + "".join(vals))

    if "by_dual_priced" in results:
        print("\n=== Segmented pinball loss by dual-pricing state (LEAR, GBM, climatology) ===")
        by_dp = results["by_dual_priced"]
        for state_label in ["False", "True"]:
            label = "single-priced" if state_label == "False" else "dual-priced"
            vals = []
            for m in available:
                seg = by_dp.get(m, {})
                v = seg.get(state_label)
                vals.append(f" {v:8.2f}" if v is not None else " " * 9)
            print(f"  {label:15s}" + "".join(vals))

    return results


def _filter_nan_predictions(
    y: FloatArray, pred: FloatArray
) -> tuple[FloatArray, FloatArray, int]:
    """Drop rows where the prediction is NaN (e.g. seasonal naive with
    unavailable lags). Returns (y_clean, pred_clean, n_dropped)."""
    valid = ~np.isnan(pred).any(axis=1)
    return y[valid], pred[valid], int((~valid).sum())


def _permutation_importance(
    model: QuantileModel,
    X_test: pd.DataFrame,
    y_test: FloatArray,
    n_repeats: int = 10,
    seed: int = 42,
) -> dict[str, float]:
    """Permutation importance on pinball loss. Shuffle each feature column
    independently, measure the increase in loss. Positive = important."""
    from src.models.lear import FEATURE_COLUMNS

    rng = np.random.default_rng(seed)
    base_pred = model.predict_quantiles(X_test, QUANTILES)
    base_loss = mean_pinball(y_test, base_pred, QUANTILES)

    importances: dict[str, float] = {}
    for col in FEATURE_COLUMNS:
        deltas = []
        for _ in range(n_repeats):
            X_perm = X_test.copy()
            X_perm[col] = rng.permutation(X_perm[col].to_numpy())
            try:
                perm_pred = model.predict_quantiles(X_perm, QUANTILES)
                perm_loss = mean_pinball(y_test, perm_pred, QUANTILES)
                deltas.append(perm_loss - base_loss)
            except Exception:
                deltas.append(0.0)
        importances[col] = float(np.mean(deltas))
    return importances


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
    targets = build_targets(prices)
    y = targets["price_short"]
    is_dual = targets["is_dual_priced"]

    folds = generate_folds(
        datetime(2024, 11, 1, tzinfo=UTC),
        datetime(2026, 12, 1, tzinfo=UTC),
    )
    print(f"{len(folds)} folds: {folds[0].label} .. {folds[-1].label}\n")

    fold_losses: dict[str, list[float]] = {n: [] for n in MODEL_FACTORIES}
    per_obs_losses: dict[str, list[FloatArray]] = {n: [] for n in MODEL_FACTORIES}
    nan_counts: dict[str, int] = {n: 0 for n in MODEL_FACTORIES}
    all_y: list[FloatArray] = []
    all_pred: dict[str, list[FloatArray]] = {n: [] for n in MODEL_FACTORIES}
    all_idx: list[pd.DatetimeIndex] = []
    last_models: dict[str, QuantileModel] = {}

    for fold in folds:
        X_train = _slice(X, fold.train_start, fold.train_end)
        y_train = _slice(y, fold.train_start, fold.train_end)
        X_test = _slice(X, fold.test_start, fold.test_end)
        y_test = _slice(y, fold.test_start, fold.test_end)

        y_arr = y_test.to_numpy()
        all_y.append(y_arr)
        all_idx.append(pd.DatetimeIndex(X_test.index))

        for name, make_model in MODEL_FACTORIES.items():
            model = make_model()
            try:
                model.fit(X_train, y_train)
                pred: FloatArray = model.predict_quantiles(X_test, QUANTILES)

                y_clean, pred_clean, n_nan = _filter_nan_predictions(y_arr, pred)
                nan_counts[name] += n_nan
                if len(y_clean) == 0:
                    print(f"  {fold.label} {name:18s} SKIPPED: all predictions NaN")
                    continue

                loss = mean_pinball(y_clean, pred_clean, QUANTILES)
                obs_loss = pinball_loss_per_obs(y_clean, pred_clean, QUANTILES)
            except Exception as exc:
                print(f"  {fold.label} {name:18s} FAILED: {exc!r}")
                continue
            fold_losses[name].append(loss)
            per_obs_losses[name].append(obs_loss)
            all_pred[name].append(pred)
            last_models[name] = model
        print(f"  {fold.label} done")

    # --- Aggregate results ---
    print("\n=== Mean pinball loss across folds (EUR/MWh, lower is better) ===")
    for name, values in fold_losses.items():
        if values:
            mean, std = np.mean(values), np.std(values)
            nan_note = f"  ({nan_counts[name]} NaN-filtered)" if nan_counts[name] > 0 else ""
            print(f"{name:18s} n={len(values):2d}  mean={mean:.4f}  std={std:.4f}{nan_note}")
        else:
            total_nan = nan_counts.get(name, 0)
            if total_nan > 0:
                print(
                    f"{name:18s} n=0   (all {total_nan} predictions NaN"
                    " -- excluded from comparisons)"
                )
            else:
                print(f"{name:18s} n=0   (never produced a prediction)")

    # --- Quantile crossing ---
    print("\n=== Quantile crossing ===")
    print("  Method: post-hoc sorting (enforce_monotone). Applied in LEAR, GBM, and climatology.")
    print(
        "  Persistence and day-ahead are point forecasts broadcast"
        " to all quantiles (no crossing possible)."
    )

    # --- DM tests vs reference ---
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
        print(f"\n=== Holm-Bonferroni ({n_comp} comparisons, vs {REFERENCE_MODEL}) ===")
        print(f"{'model':18s} {'adj p':>12s} {'sig 5%':>8s}")
        for label, adj_p, sig in corrected:
            print(f"{label:18s} {adj_p:12.6f} {'YES' if sig else 'no':>8s}")

    # --- DM test: LEAR vs GBM head-to-head ---
    lear_obs = per_obs_losses.get("lear", [])
    gbm_obs = per_obs_losses.get("gbm", [])
    dm_lear_gbm: dict[str, object] = {}
    if lear_obs and gbm_obs:
        lear_concat = np.concatenate(lear_obs)
        gbm_concat = np.concatenate(gbm_obs)
        if len(lear_concat) == len(gbm_concat):
            max_lag = int(np.floor(len(lear_concat) ** (1.0 / 3.0)))
            dm_stat, p_val = diebold_mariano(lear_concat, gbm_concat, max_lag=max_lag)
            print("\n=== Diebold-Mariano: LEAR vs GBM (head-to-head) ===")
            print(f"  DM stat: {dm_stat:.4f}  p-value: {p_val:.6f}  n={len(lear_concat)}")
            if p_val < 0.05:
                winner = "GBM" if dm_stat > 0 else "LEAR"
                print(f"  Significant at 5%: {winner} is better")
            else:
                print("  Not significant at 5%: no evidence LEAR and GBM differ")
            dm_lear_gbm = {"dm_stat": dm_stat, "p_value": p_val, "n_obs": len(lear_concat)}

    # --- Calibration ---
    y_concat = np.concatenate(all_y)
    idx_concat = pd.DatetimeIndex(np.concatenate([i.values for i in all_idx]))
    if all_idx and all_idx[0].tz is not None:
        idx_concat = idx_concat.tz_localize(all_idx[0].tz)
    calibration: dict[str, dict[str, object]] = {}
    pred_full: dict[str, FloatArray] = {}
    for name, preds in all_pred.items():
        if not preds:
            continue
        pc = np.concatenate(preds, axis=0)
        if len(pc) != len(y_concat):
            continue
        has_nan = np.isnan(pc).any(axis=1)
        n_nan_rows = int(has_nan.sum())
        if n_nan_rows == 0:
            pred_full[name] = pc
            calibration[name] = _calibration_report(y_concat, pc, name)
        elif n_nan_rows < len(pc):
            valid = ~has_nan
            suffix = f" ({n_nan_rows} NaN-filtered, {valid.sum()}/{len(pc)} coverage)"
            calibration[name] = _calibration_report(y_concat[valid], pc[valid], f"{name}{suffix}")

    # --- Segmented (full-coverage models only) ---
    segmented = _segmented_report(
        pd.Series(y_concat, index=idx_concat),
        pred_full,
        idx_concat,
        is_dual=is_dual,
    )

    # --- Feature importance (permutation, on last fold) ---
    importance_results: dict[str, dict[str, float]] = {}
    for model_name in ["lear", "gbm"]:
        if model_name in last_models:
            last_fold = folds[-1]
            X_test_last = _slice(X, last_fold.test_start, last_fold.test_end)
            y_test_last = _slice(y, last_fold.test_start, last_fold.test_end).to_numpy()
            complete = X_test_last[list(
                ("lag_price_short_freshest", "lag_price_short_672",
                 "hour_sin", "hour_cos", "dow_sin", "dow_cos", "day_ahead_price")
            )].notna().all(axis=1)
            X_test_clean = X_test_last[complete]
            y_test_clean = y_test_last[complete.to_numpy()]
            if len(X_test_clean) > 0:
                n_imp = len(X_test_clean)
                print(f"\n=== Permutation importance: {model_name} (last fold, n={n_imp}) ===")
                imp = _permutation_importance(
                    last_models[model_name], X_test_clean, y_test_clean
                )
                importance_results[model_name] = imp
                for feat, delta in sorted(imp.items(), key=lambda x: -x[1]):
                    print(f"  {feat:30s} {delta:+.4f} EUR/MWh")

    # --- Save ---
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "folds": len(folds),
        "fold_range": f"{folds[0].label} .. {folds[-1].label}",
        "reference_model": REFERENCE_MODEL,
        "n_comparisons": len(raw_pvalues),
        "correction": "Holm-Bonferroni",
        "model_configs_tried": len(MODEL_FACTORIES),
        "quantile_crossing": "post-hoc sorting (enforce_monotone)",
        "fold_losses": {
            name: {
                "n": len(v),
                "mean": float(np.mean(v)),
                "std": float(np.std(v)),
                "nan_filtered": nan_counts[name],
            }
            for name, v in fold_losses.items()
            if v
        },
        "dm_tests_vs_reference": [
            {"model": n, "dm_stat": d, "p_value": p}
            for n, d, p in dm_results
        ],
        "dm_lear_vs_gbm": dm_lear_gbm,
        "holm_bonferroni": [
            {"model": lb, "adj_p_value": ap, "significant_005": sg}
            for lb, ap, sg in corrected
        ],
        "calibration": calibration,
        "segmented": segmented,
        "permutation_importance": importance_results,
    }
    results_path = RESULTS_DIR / "walkforward_results.json"
    results_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nResults saved to {results_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
