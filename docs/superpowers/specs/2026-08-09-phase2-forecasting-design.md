# Phase 2 — Forecasting: design

**Status:** approved 2026-08-09. Training window chosen by the repo owner;
holdout and horizon delegated to me and decided below with reasoning.

## The constraint that shapes everything

Dual pricing runs at ~6% of ISPs from 2021 through mid-2024, then jumps to
25.7% by 2024-11 and climbs to 40.5% by 2026-05. The jump coincides with
PICASSO go-live (2024-10-18) and has a primary-sourced mechanism: [IPS61] §3.4
says PICASSO can flip a Dutch shortage into a surplus *within* an ISP, which is
exactly the condition for regulation state 2. See ADR-022.

**Training is restricted to 2024-10-18 onward.** A model spanning that boundary
would be miscalibrated on P(state 2) — the quantity that decides whether the
risk-aware dispatch policy beats the deterministic one, i.e. the project's
central comparison. Buying calibration with sample size is the right trade here,
and the shortfall against CLAUDE.md §3's 3-year target is recorded in
`LIMITATIONS.md` rather than hidden.

Usable span: 2024-10-18 → 2026-07-31, ≈ 65,000 ISPs.

## Decisions I was asked to make

**Final holdout: 2026-05-01 → 2026-07-31 (3 complete months, ~8,800 ISPs).**
CLAUDE.md §6 requires "one contiguous, most-recent period", which rules out
holding out a winter. Three months costs 14% of a thin sample where six would
cost 27%. **Its weakness, stated now rather than discovered later:** it is
summer-only, so it under-tests winter scarcity — precisely when a battery earns
most. Mitigation: the walk-forward period contains a full winter and results are
segmented by season, so winter performance is measured, just not in the holdout.
Touched exactly once, at the very end.

**Horizon: t … t+7 (the current ISP plus 2 hours).** Phase 3's entire value is
state-of-charge planning under uncertainty; a 2-hour battery cannot plan a
charge/discharge cycle in less than its own duration. CLAUDE.md §4 suggests 1–8
ISPs and asks for justification — this is it. Compute is managed by making
horizon a parameter and reporting `t` and `t+3` as headline while producing the
full set.

## Targets

All three from CLAUDE.md §4, built in this order:

| | Target | Type | Why it exists |
|---|---|---|---|
| **T1** | regulation state ∈ {0, +1, −1, 2} | multiclass, calibrated probabilities | State 2 is dual-priced and loss-making in both directions. `P(state=2)` is the single input that makes risk-aware dispatch beat deterministic. |
| **T2** | `price_long`, `price_short` | 19 quantiles each (0.05…0.95) | Two targets, not one — dual pricing means they diverge in 26–40% of ISPs. |
| **T3** | `price_short − day_ahead_price` | 19 quantiles | More stationary and more directly tradeable. |

T1 is built first, against the brief's ordering of T1/T2/T3, because it is both
the most actionable and the cheapest to evaluate honestly (calibration curves,
Brier, log loss) — and because a well-calibrated T1 is what Phase 3 consumes.

**T3 and the MTU change.** Day-ahead moved from 60- to 15-minute MTU on
2025-10-01, inside the training window. Pre-change day-ahead prices are
broadcast across the four ISPs of each hour (ADR-008) and every T3 result is
segmented at that date.

## Features

Every feature is fetched through the Phase 1 layer and gated by
`assert_available(field, isp, decision_time)`. The builder cannot emit a feature
that fails that check — that is the Phase 1 gate doing its job, not a new
control.

| Group | Features | Availability note |
|---|---|---|
| Balance delta | mean, last, min, max, sign changes, and monotonicity of the intra-ISP series | **t−2 only** at ISP granularity (134 s lag from period end; ADR-021). Monotonicity is the direct analogue of the state rule. |
| Lagged targets | settled price and state at t−96 (same ISP yesterday), t−672 (last week) | Wall-clock D+1 10:00 rule (ADR-014). Needs local-delivery-day arithmetic, not UTC offsets. |
| Real-time estimate | imbalance price estimate at t−2, t−3 | 120 s lag; t−1 publishes *after* the decision. |
| Day-ahead | price at t, shape over the day, spread to t±1 | Available from D−1 ~13:00. |
| Load | forecast, and residual load = load − wind − solar | D−1 10:00. |
| Wind/solar | day-ahead vintage, intraday vintage, **and their difference** | The forecast-error proxy (§4). Available only when `available_vintages` returns both — a per-period fact, false for early-morning ISPs. |
| Calendar | hour, day-of-week, month, NL holiday; cyclic encoding | Always available. |
| Cross-border | physical flows | 1 h lag → t−5 at the earliest. |

No feature without a stated rationale (§4). Permutation importance reported at
the end.

## Baselines — built and evaluated *before* any model (R4)

1. **Persistence** — last observed settled value.
2. **Seasonal naive** — same ISP previous day, and previous week.
3. **Climatological quantiles** — conditioned on hour-of-day × day-of-week,
   fitted on the training fold only. The brief warns this is stronger than most
   papers admit; treat it as the one to beat.
4. **Day-ahead price** as a direct predictor of the imbalance price.
5. **T1**: majority class, and a conditional frequency table on hour × recent state.

A model that does not beat these is reported as not beating them.

## Walk-forward protocol

Rolling-origin, **expanding** training window, with a **purge and embargo gap**
between train and test. Gap = 1 day: long enough to cover the D+1 settlement
publication so a training label cannot leak into a test feature through the
lagged-target path.

- Train start fixed at 2024-10-18; test folds of 1 month; step 1 month.
- Folds run 2025-04 → 2026-04 (the first ~5 months are the minimum training base).
- **The holdout (2026-05 onward) is excluded from every fold** and from all
  model selection.

## Evaluation

`src/evaluation/` is a first-class deliverable (§4), not an afterthought.

- **Probabilistic:** pinball loss per quantile and aggregate; CRPS from the
  quantile set; empirical coverage vs nominal; PIT histogram; interval widths
  conditional on calibration.
- **Point (secondary):** MAE, RMSE — with sMAPE explicitly refused near zero
  prices, which happens constantly here.
- **T1:** confusion matrix, per-class F1, log loss, Brier, reliability diagram.
  Calibration matters more than accuracy for downstream optimisation.
- **Significance:** Diebold-Mariano vs each baseline with HAC standard errors;
  Benjamini-Hochberg across the model grid; and a running count of every
  configuration tried, reported in the write-up.
- **Segmented:** by regulation state, hour, season, volatility regime, and by
  the 2025-10-01 MTU boundary.

**Quantile crossing** is handled by post-hoc sorting, stated explicitly, with a
test asserting monotonicity of every emitted quantile vector.

## Module layout

| File | Responsibility |
|---|---|
| `src/features/builder.py` | Assemble the feature matrix; every field access via `assert_available`. |
| `src/features/catalogue.py` | One declarative entry per feature: source field, lag basis, rationale. Renders `docs/FEATURES.md`. |
| `src/models/base.py` | The common interface: `fit`, `predict_quantiles`, `predict_proba`. |
| `src/models/baselines.py` | The five baselines. |
| `src/models/lear.py` | Regularised linear quantile regression. |
| `src/models/gbm.py` | LightGBM quantile / multiclass. |
| `src/evaluation/metrics.py` | Pinball, CRPS, coverage, PIT, Brier, log loss. |
| `src/evaluation/significance.py` | DM test, HAC errors, multiple-comparison correction. |
| `src/evaluation/walkforward.py` | Fold generation, purge/embargo, run orchestration. |

Models share one interface so swapping one requires no change to evaluation,
optimisation or backtest code (§9).

## What this design deliberately excludes

- **Distributional neural network** (§4 model 3) until LEAR and GBM are complete
  and compared. Compute cost reported if built.
- **Intra-ISP re-decision** — out of scope per ADR-005.
- **Point-level balance-delta availability** (ADR-021) — the ISP-level answer is
  conservative and safe; the finer version is a follow-up once the coarse
  pipeline demonstrably works.

## Risks

| Risk | Handling |
|---|---|
| 22 months is thin for tail calibration | Stated in LIMITATIONS; block-bootstrap CIs on every headline figure |
| Holdout is summer-only | Winter measured in walk-forward, segmented reporting; stated up front |
| Dual-price share still trending upward *within* the training window | Segment by quarter; if the trend dominates, report it rather than fitting through it |
| Multiple-comparison inflation across a large model grid | Configuration counter from the first run; BH correction; disclosed in the write-up |
