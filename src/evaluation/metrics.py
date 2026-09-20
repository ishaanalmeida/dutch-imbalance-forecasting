"""Probabilistic and classification metrics.

RIGOUR ZONE (CLAUDE.md §12). Ponytail simplification does not apply here and
`ponytail:` comments are prohibited: CLAUDE.md §4 calls evaluation "where the
project earns its credibility" and asks for it to be treated as a first-class
product rather than an afterthought.

Deliberately no sMAPE. Percentage errors are meaningless near zero prices,
which happens constantly in this market, and reporting one would invite a
comparison that cannot mean what it appears to.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

# CLAUDE.md §4: "at minimum 0.05 ... 0.95 in steps of 0.05".
QUANTILES: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(1, 20))

_LOG_EPS = 1e-15


def _check_no_nan(*arrays: FloatArray) -> None:
    """Refuse rather than guess: a NaN target/prediction silently mis-scores
    as "covered" (empirical_coverage) or "above the top quantile" (pit_values)
    instead of raising, which would quietly distort a calibration report with
    no warning (docs/DECISIONS.md ADR-025). Every metric in this rigour-zone
    module goes through this, so a caller with e.g. dual-pricing NaN targets
    must filter them before scoring, not rely on this module to guess."""
    for arr in arrays:
        if np.isnan(arr).any():
            raise ValueError("NaN in metric input: filter it before scoring, do not let this guess")


def _check(y_true: FloatArray, q_pred: FloatArray, quantiles: tuple[float, ...]) -> None:
    if q_pred.ndim != 2 or q_pred.shape != (len(y_true), len(quantiles)):
        raise ValueError(
            f"shape mismatch: expected q_pred {(len(y_true), len(quantiles))}, got {q_pred.shape}"
        )
    _check_no_nan(y_true, q_pred)


def _check_1d(y_true: FloatArray, p_pred: FloatArray) -> None:
    if y_true.shape != p_pred.shape:
        raise ValueError(f"shape mismatch: {y_true.shape} vs {p_pred.shape}")
    _check_no_nan(y_true, p_pred)


def pinball_loss(
    y_true: FloatArray, q_pred: FloatArray, quantiles: tuple[float, ...]
) -> FloatArray:
    """Mean pinball loss per quantile. Lower is better."""
    _check(y_true, q_pred, quantiles)
    taus = np.asarray(quantiles)
    error = y_true[:, None] - q_pred
    loss = np.maximum(taus * error, (taus - 1.0) * error)
    return np.asarray(loss.mean(axis=0))


def mean_pinball(y_true: FloatArray, q_pred: FloatArray, quantiles: tuple[float, ...]) -> float:
    return float(pinball_loss(y_true, q_pred, quantiles).mean())


def crps_from_quantiles(
    y_true: FloatArray, q_pred: FloatArray, quantiles: tuple[float, ...]
) -> float:
    """CRPS approximated from the quantile set.

    For a finite quantile grid, mean pinball loss times 2 is the standard
    approximation to CRPS; it converges as the grid densifies. Reported as an
    approximation, never as exact.
    """
    return 2.0 * mean_pinball(y_true, q_pred, quantiles)


def empirical_coverage(
    y_true: FloatArray, q_pred: FloatArray, quantiles: tuple[float, ...]
) -> FloatArray:
    """Fraction of observations at or below each predicted quantile.

    A calibrated forecast puts this on the diagonal against nominal. The gap
    between the two is the reliability curve CLAUDE.md §4 asks to be plotted.
    """
    _check(y_true, q_pred, quantiles)
    return np.asarray((y_true[:, None] <= q_pred).mean(axis=0))


def pit_values(y_true: FloatArray, q_pred: FloatArray, quantiles: tuple[float, ...]) -> FloatArray:
    """Probability integral transform of each observation.

    Flat is the goal. A U-shape means the intervals are too narrow
    (over-confident); a hump means too wide. Report the shape honestly.

    A finite quantile grid only gives us the CDF at 19 points, not the full
    CDF. Snapping each observation to the nearest predicted quantile below it
    (a step function) throws away where inside that bracket the observation
    actually fell -- on a calibrated forecast that collapses 20,000
    continuous PIT values onto ~19 discrete points, which then alias against
    arbitrary histogram bin edges and produce spurious peaks. Linearly
    interpolating between the two bracketing quantile levels recovers the
    within-bracket position instead, which is what makes a calibrated
    forecast's PIT come out genuinely close to uniform rather than merely
    close on average. Below the lowest quantile the PIT is clamped to 0.0;
    above the highest, to 1.0. Where a bracket is degenerate (two adjacent
    predicted quantiles tie), the midpoint of the two quantile levels is used
    since interpolation is undefined.
    """
    _check(y_true, q_pred, quantiles)
    taus = np.asarray(quantiles)
    n_q = len(taus)

    pit = np.where(y_true <= q_pred[:, 0], 0.0, 1.0)
    interior = (y_true > q_pred[:, 0]) & (y_true < q_pred[:, -1])
    if interior.any():
        y_int = y_true[interior]
        q_int = q_pred[interior]
        rows = np.arange(len(y_int))
        # k = index of the highest predicted quantile at or below y.
        k = np.clip((q_int <= y_int[:, None]).sum(axis=1) - 1, 0, n_q - 2)
        lo_val, hi_val = q_int[rows, k], q_int[rows, k + 1]
        lo_tau, hi_tau = taus[k], taus[k + 1]
        span = hi_val - lo_val
        nonzero = span > 0
        frac = np.where(nonzero, (y_int - lo_val) / np.where(nonzero, span, 1.0), 0.5)
        pit[interior] = lo_tau + frac * (hi_tau - lo_tau)
    return np.asarray(pit)


def enforce_monotone(q_pred: FloatArray) -> FloatArray:
    """Sort each row so quantiles never cross.

    CLAUDE.md §4 requires crossing be addressed explicitly and the method
    stated: this project sorts post hoc rather than constraining the model.
    """
    return np.asarray(np.sort(q_pred, axis=1))


def pinball_loss_per_obs(
    y_true: FloatArray, q_pred: FloatArray, quantiles: tuple[float, ...]
) -> FloatArray:
    """Mean pinball loss per observation (averaged across quantiles).

    Returns shape (n,). Used for the Diebold-Mariano test, which needs a
    per-observation loss series, not a per-quantile or scalar aggregate.
    """
    _check(y_true, q_pred, quantiles)
    taus = np.asarray(quantiles)
    error = y_true[:, None] - q_pred
    loss = np.maximum(taus * error, (taus - 1.0) * error)
    return np.asarray(loss.mean(axis=1))


def diebold_mariano(
    loss_a: FloatArray,
    loss_b: FloatArray,
    max_lag: int | None = None,
) -> tuple[float, float]:
    """Two-sided Diebold-Mariano test with Newey-West HAC standard errors.

    Tests H0: E[L_a - L_b] = 0 (equal predictive accuracy).
    Returns (dm_statistic, p_value_two_sided).

    max_lag: truncation lag for the Bartlett kernel. Default floor(T^(1/3)),
    the standard choice in the DM literature.
    """
    _check_no_nan(loss_a, loss_b)
    if loss_a.shape != loss_b.shape or loss_a.ndim != 1:
        raise ValueError(
            f"loss arrays must be 1-D and same length, got {loss_a.shape} vs {loss_b.shape}"
        )

    d = loss_a - loss_b
    T = len(d)
    if T < 2:
        raise ValueError(f"need at least 2 observations for DM test, got {T}")
    d_bar = d.mean()

    if max_lag is None:
        max_lag = int(np.floor(T ** (1.0 / 3.0)))

    gamma = np.empty(max_lag + 1)
    d_centered = d - d_bar
    for k in range(max_lag + 1):
        gamma[k] = np.dot(d_centered[: T - k], d_centered[k:]) / T

    # Newey-West (Bartlett kernel): var = gamma_0 + 2 * sum_{k=1}^{h} (1 - k/(h+1)) * gamma_k
    weights = 1.0 - np.arange(1, max_lag + 1) / (max_lag + 1)
    var_d_bar = (gamma[0] + 2.0 * np.dot(weights, gamma[1:])) / T

    if var_d_bar <= 0:
        return 0.0, 1.0

    dm = d_bar / np.sqrt(var_d_bar)
    from scipy.stats import norm

    p_value = 2.0 * norm.sf(np.abs(dm))
    return float(dm), float(p_value)


def holm_bonferroni(
    p_values: list[tuple[str, float]],
) -> list[tuple[str, float, bool]]:
    """Holm-Bonferroni correction for multiple comparisons.

    Input: list of (label, raw_p_value).
    Output: list of (label, adjusted_p_value, significant_at_005), sorted by
    original p-value ascending.

    Holm-Bonferroni controls FWER and is uniformly more powerful than
    Bonferroni — CLAUDE.md §4 asks for the correction used to be stated.
    """
    m = len(p_values)
    sorted_pv = sorted(p_values, key=lambda x: x[1])
    results: list[tuple[str, float, bool]] = []
    max_adj = 0.0
    for i, (label, p) in enumerate(sorted_pv):
        adj = min(p * (m - i), 1.0)
        adj = max(adj, max_adj)
        max_adj = adj
        results.append((label, adj, adj < 0.05))
    return results


def brier_score(y_true: FloatArray, p_pred: FloatArray) -> float:
    """Mean squared error of a probability forecast. Lower is better."""
    _check_1d(y_true, p_pred)
    return float(np.mean((p_pred - y_true) ** 2))


def log_loss_binary(y_true: FloatArray, p_pred: FloatArray) -> float:
    """Binary log loss, clipped.

    Clipping matters: an unclipped zero probability returns inf and destroys a
    whole run's mean, turning one confident mistake into an unusable report.
    """
    _check_1d(y_true, p_pred)
    p = np.clip(p_pred, _LOG_EPS, 1.0 - _LOG_EPS)
    return float(-np.mean(y_true * np.log(p) + (1.0 - y_true) * np.log(1.0 - p)))
