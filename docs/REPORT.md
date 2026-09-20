# Dutch Imbalance Market Forecasting & Battery Dispatch — Full Report

## 1. Problem statement

A battery-storage operator in the Netherlands earns or loses money at the
imbalance settlement price — the price TenneT charges balance-responsible
parties whose portfolio deviates from schedule during an imbalance settlement
period (ISP, 15 minutes). The price is volatile (regularly exceeding
±500 EUR/MWh), partially predictable from publicly available signals, and
subject to complex settlement rules including dual pricing in certain
regulation states.

This project asks: **how much of the perfect-foresight battery revenue can a
probabilistic forecast of the imbalance price capture, and how does that
fraction change with risk aversion, deployed capacity, and market conditions?**

The answer matters because it determines whether public-data imbalance
forecasting is a viable commercial strategy for grid-scale storage, or whether
the signal is too noisy, too crowded, or too regime-dependent to justify the
infrastructure.

## 2. Data

### Sources

| Source | Series | Period |
|---|---|---|
| ENTSO-E Transparency Platform | Imbalance prices (price_short, price_long), day-ahead prices | 2024-10-18 to 2026-08-01 |
| Open-Meteo | Historical forecast archive (wind, solar, temperature) | Same period, forecast vintages |

All data post-PICASSO (18 Oct 2024), when TenneT joined the PICASSO platform
for automatic frequency restoration reserve (aFRR) activation. Price formation
rules held constant (confirmed by TenneT IPS v6.1), but the price distribution
shifted — volatility roughly halved.

### Publication lag enforcement

Every feature is gated by `data_availability.py`. The function
`available_at(field, target_isp)` returns the timestamp at which each datum
was first retrievable. The feature builder refuses to emit any feature that
violates the availability constraint. This is tested by 13 mutation tests
that deliberately break the lag logic and verify rejection.

| Field | Publication lag | Confidence |
|---|---|---|
| Imbalance price (price_short) | ISP end + 60 s | high (API measurement) |
| Day-ahead price | D-1 12:42 CET (SDAC results) | high (ENTSO-E docs) |
| Balance delta | ISP end + 134 s | medium (narrow measurement window) |

### Data quality

- 60,288 ISPs total (PICASSO start to holdout end)
- No gaps detected in imbalance price series
- DST transitions tested explicitly (92 and 100 ISPs for 23/25-hour days)
- Dual pricing occurs in ~26–40% of ISPs post-PICASSO

## 3. Features

7 features, each with a documented availability constraint:

| Feature | Source | Lag | Rationale |
|---|---|---|---|
| `lag_price_short_freshest` | ENTSO-E | ISP end + 60 s | Autoregressive signal |
| `lag_price_short_672` | ENTSO-E | 1 week | Weekly seasonal pattern |
| `hour_sin`, `hour_cos` | Calendar | None | Diurnal load/generation pattern |
| `dow_sin`, `dow_cos` | Calendar | None | Weekday/weekend demand difference |
| `day_ahead_price` | ENTSO-E | D-1 12:42 CET | Market expectation, spread anchor |

### Permutation importance (GBM, last walk-forward fold)

| Feature | Δ pinball (EUR/MWh) |
|---|---|
| `day_ahead_price` | +6.22 |
| `hour_sin` | +0.31 |
| `hour_cos` | +0.24 |
| `lag_price_short_freshest` | +0.12 |
| `lag_price_short_672` | +0.07 |
| `dow_sin` | -0.04 |
| `dow_cos` | -0.01 |

Day-ahead price dominates. The lagged imbalance price provides incremental
signal but is secondary. Day-of-week features are negligible — consistent with
the imbalance market being driven by forecast errors in renewable generation
rather than demand patterns.

## 4. Models

### 4.1 Baselines (mandatory per R4)

1. **Persistence**: last observed price broadcast to all quantiles.
2. **Seasonal naive (1w)**: same ISP, previous week.
3. **Climatological quantiles**: hour-of-day × day-of-week conditional
   quantiles. This is the strongest naive baseline and many papers
   under-report it.
4. **Day-ahead price**: the spot market clearing price as a predictor.

### 4.2 LEAR

Regularised linear quantile regression (Lasso), one model per quantile.
The standard benchmark in the electricity price forecasting literature.
Linear, interpretable, fast.

### 4.3 Quantile GBM

LightGBM with `objective="quantile"`, one model per quantile (19 quantiles:
0.05, 0.10, ..., 0.95). Quantile crossings resolved by post-hoc sorting.

### Common interface

All models implement `fit(X, y)` and `predict_quantiles(X, quantiles)`.
Swapping models requires no changes to evaluation, optimisation, or backtest.

## 5. Evaluation

### 5.1 Protocol

Expanding-origin walk-forward with monthly folds. 18 folds from 2024-11 to
2026-04. No random splits. No k-fold on time series. One final holdout
(2026-05 to 2026-08) evaluated exactly once at the end.

### 5.2 Pinball loss (mean across 18 folds)

| Model | Mean | Std |
|---|---|---|
| Persistence | 42.50 | 12.23 |
| Seasonal naive (1w) | 44.66 | 12.45 |
| **Climatology** | **25.47** | **7.74** |
| Day-ahead | 29.19 | 7.96 |
| **LEAR** | **23.20** | **6.90** |
| **GBM** | **23.17** | **7.09** |

### 5.3 Statistical significance

Diebold-Mariano tests vs climatology (reference), Holm-Bonferroni corrected:

| Model | DM stat | adj. p-value | Significant |
|---|---|---|---|
| Persistence | +22.04 | 4.9e-107 | Yes (worse) |
| Seasonal naive (1w) | +23.10 | 2.5e-117 | Yes (worse) |
| Day-ahead | +16.48 | 9.4e-61 | Yes (worse) |
| LEAR | -16.55 | 5.2e-61 | Yes (better) |
| GBM | -15.31 | 6.4e-53 | Yes (better) |

**LEAR vs GBM head-to-head**: DM = 0.286, p = 0.774. Not significantly
different. The 0.03 EUR/MWh gap is economically negligible.

### 5.4 Calibration

**GBM (walk-forward)**:
- Mean absolute calibration error: 0.008
- Max calibration error: 0.022 (at q=0.70)
- PIT CV: 0.079 (near-uniform; perfect = 0)
- CRPS: 46.42 EUR/MWh

**GBM (holdout)**:
- Mean absolute calibration error: 0.059
- PIT CV: 0.201
- CRPS: 45.02 EUR/MWh

Calibration degrades on holdout — the PIT histogram shows a right-tail
concentration (more observations above the predicted distribution than
expected). This is consistent with the holdout period being more volatile
than the training distribution.

**LEAR**: comparable calibration on walk-forward (cal error 0.007, PIT CV
0.074), but degrades more on holdout (cal error 0.095, PIT CV 0.500).

**Climatology**: well-calibrated by construction on walk-forward (cal error
0.018), but significantly miscalibrated on holdout (cal error 0.114) —
the conditional quantiles are period-specific.

### 5.5 Point accuracy

| Model | MAE (EUR/MWh) | RMSE (EUR/MWh) |
|---|---|---|
| Climatology | 61.30 | 183.17 |
| Day-ahead | 58.47 | 179.67 |
| LEAR | 56.70 | 180.13 |
| GBM | 56.78 | 180.24 |

sMAPE deliberately omitted: meaningless near zero prices, which occur
constantly in this market.

### 5.6 Segmented results

**By hour (pinball loss, GBM)**: worst at hours 9–12 UTC (36–33 EUR/MWh),
best overnight hours 1–4 (13–14 EUR/MWh). Consistent with renewable
generation ramps creating the hardest-to-predict imbalances during morning
hours.

**By season**: winter (21.1) < summer (22.8) < autumn (23.9) < spring (25.5).
Spring is hardest, likely due to unpredictable solar/wind transitions.

**By year**: 2024 (35.1) > 2025 (22.2) > 2026 (20.2). Improving over time as
the training set grows and the post-PICASSO regime stabilises.

**By dual-pricing state**: single-priced ISPs (25.5) > dual-priced (17.5).
The model is better at predicting prices during dual-priced periods, which
carry the highest revenue potential for batteries.

## 6. Battery dispatch

### 6.1 Battery parameters

| Parameter | Value |
|---|---|
| Power | 10 MW |
| Energy | 40 MWh (4h duration) |
| Round-trip efficiency | 90.25% (95% × 95%) |
| Degradation cost | EUR 5/MWh throughput |
| Terminal SoC penalty | EUR 50/MWh deviation from 50% |

### 6.2 Dispatch policies

**Deterministic**: rolling-horizon LP (window=32 ISPs / 8h, step=16 ISPs / 4h)
against the median forecast. At each step, optimise over the window, execute
the first `step` ISPs, carry the SoC forward.

**CVaR**: same rolling horizon, but optimise against 20 scenarios generated by
stratified quantile sampling. Each scenario picks a consistent base quantile
level (evenly spaced), with small per-ISP jitter (σ=0.03). The objective
maximises (1-ra) × E[revenue] + ra × CVaR_5%.

**Perfect foresight**: optimise against realised prices. The upper bound — not
achievable, but essential context.

### 6.3 Scenario generation

The original Schaake shuffle (independent quantile levels per ISP) destroyed
the forecast's directional signal, producing EUR -394K (a loss). Stratified
quantile sampling preserves the forecast's temporal shape within each scenario
while varying the price level across scenarios. CVaR went from -394K to +870K.
See ADR-031 for the full diagnosis.

## 7. Backtest results

### 7.1 Walk-forward (18 folds, 52,416 ISPs)

| Policy | Net Revenue | Ratio to PF | 95% CI |
|---|---|---|---|
| Perfect foresight | EUR 5,840,081 | 100% | [5,395K, 6,299K] |
| Deterministic | EUR 929,535 | 15.9% | [682K, 1,201K] |
| CVaR (ra=0.5) | EUR 870,398 | 14.9% | [607K, 1,156K] |
| Do nothing | EUR 0 | 0% | — |

The 15.9% PF capture ratio is consistent with public-data imbalance strategies
in the literature (10–20%). CIs for deterministic and CVaR overlap —
statistically indistinguishable on this sample.

### 7.2 Final holdout (6,528 ISPs, May–Jul 2026)

| Policy | Net Revenue | Ratio to PF | 95% CI |
|---|---|---|---|
| Perfect foresight | EUR 754,119 | 100% | [622K, 916K] |
| Deterministic | EUR 210,673 | 27.9% | [94K, 351K] |
| CVaR (ra=0.5) | EUR 185,236 | 24.6% | [70K, 324K] |

Holdout outperforms walk-forward on PF ratio. Annualised: deterministic
EUR 1.13M vs walk-forward EUR 621K (+82%). This likely reflects period
characteristics rather than model improvement — see Limitations.

### 7.3 Per-year revenue (walk-forward, deterministic)

| Year | Revenue | Annualised |
|---|---|---|
| 2024 (2 months) | EUR 73,531 | EUR 439,983 |
| 2025 (12 months) | EUR 981,310 | EUR 981,310 |
| 2026 (4 months) | EUR 143,023 | EUR 435,028 |

2025 dominates — a volatile year with high imbalance prices. The strategy's
value is regime-dependent.

### 7.4 Efficient frontier

Sweep of risk_aversion from 0.0 to 1.0 on the last 3 months of walk-forward:

| Risk aversion | Net revenue (EUR) | CVaR 5% (EUR/ISP) |
|---|---|---|
| 0.0 | 76,284 | -280.0 |
| 0.2 | 85,682 | -279.5 |
| 0.5 | 83,815 | -274.3 |
| 0.9 | 67,062 | -272.5 |
| 1.0 | 76,209 | -272.5 |

The frontier is relatively flat — expected revenue and tail risk move together.
This makes economic sense: in this market, the worst ISPs for a battery
(extreme negative prices) are also the ones where the expected value is most
negative, so reducing tail exposure also reduces expected revenue.

### 7.5 Revenue-per-MW saturation

| Capacity | Revenue/MW | Total |
|---|---|---|
| 1 MW | EUR 87,596 | EUR 87,596 |
| 10 MW | EUR 76,013 | EUR 760,131 |
| 50 MW | EUR 55,074 | EUR 2,753,686 |
| 100 MW | EUR 39,383 | EUR 3,938,339 |
| 200 MW | EUR 17,194 | EUR 3,438,785 |

At 200 MW, total revenue begins declining — the operator's own impact
outweighs the signal. This is where a rational operator would stop scaling.
The model is uncalibrated (see Limitations) but the shape is structurally
correct.

## 8. Conclusions

1. Public-data probabilistic forecasting of the Dutch imbalance price is
   feasible: GBM and LEAR both significantly beat the strongest naive baseline.
2. The two models are statistically indistinguishable — the linear model is
   surprisingly competitive.
3. A 10 MW battery captures 15–28% of perfect-foresight revenue depending on
   the period. Annualised: EUR 400K–1.1M.
4. The CVaR policy performs comparably to deterministic — the efficient frontier
   is flat in this market.
5. Revenue degrades with deployed capacity. At 100 MW, revenue per MW is less
   than half of the 1 MW value.
6. The strategy is regime-dependent: 2025 was a good year, 2024 and 2026
   less so.

**What this project demonstrates**: forecasting rigour (walk-forward, no
look-ahead, significance tests, calibration), optimisation under uncertainty
(three policies, efficient frontier), and honest evaluation (limitations
prominently placed, holdout improvement not over-claimed, regime dependence
reported).

**What it does not demonstrate**: a production-ready trading system. The gap
between backtested revenue and real revenue is the content of
[LIMITATIONS.md](../LIMITATIONS.md).
