# Dutch Imbalance Market Forecasting & Battery Dispatch Lab

Probabilistic forecasting of the Dutch (TenneT) imbalance settlement price at
15-minute resolution, with battery-storage dispatch optimised against the full
predictive distribution. Walk-forward backtested under real settlement rules,
real publication latency, and an explicit market-impact model.

## Headline result

A quantile-GBM forecast, dispatching a 10 MW / 40 MWh battery via
rolling-horizon optimisation, **captures 15.9% of the perfect-foresight revenue
on 18 walk-forward folds (2024-11 to 2026-04)** and **27.9% on the final
holdout (May–Jul 2026, evaluated once)**. Annualised walk-forward net revenue:
EUR 621K deterministic, EUR 582K CVaR. All numbers carry block-bootstrap 95%
confidence intervals and are produced by committed code in this repo. The
forecast beats the strongest naive baseline (climatological quantiles
conditioned on hour-of-day) with Diebold-Mariano p << 0.001.

**Read [LIMITATIONS.md](LIMITATIONS.md) before interpreting any number.**

## Key figures

| | Walk-Forward (18 mo) | Final Holdout (3 mo) |
|---|---|---|
| Perfect foresight | EUR 5,840K | EUR 754K |
| Deterministic | EUR 930K (15.9% PF) | EUR 211K (27.9% PF) |
| CVaR (ra=0.5) | EUR 870K (14.9% PF) | EUR 185K (24.6% PF) |
| 95% CI (det.) | [682K, 1,201K] | [94K, 351K] |
| GBM pinball loss | 23.17 EUR/MWh | 22.51 EUR/MWh |
| GBM calibration error | 0.008 | 0.059 |

The holdout outperforms walk-forward on the PF ratio — likely period-specific
(May–Jul 2026 market characteristics) rather than model improvement. Reported
honestly; see ADR-032.

### Efficient frontier

Revenue ranges from EUR 86K (ra=0.2) to EUR 67K (ra=0.9) on the last 3 months
of walk-forward data. The frontier is relatively flat: expected revenue and tail
risk move together in this market, reflecting the skewness of imbalance prices.
CVaR5 improves from -280 to -273 EUR/ISP across the sweep.

### Revenue saturation

Under a sqrt(capacity / 500 MW) market-impact model, revenue per MW degrades
from EUR 88K/MW at 1 MW to EUR 39K/MW at 100 MW. At 500 MW deployed, the
public-data signal is fully absorbed. This is a modelling choice, not a
measurement — see [LIMITATIONS.md](LIMITATIONS.md).

## Method

**Data.** ENTSO-E Transparency Platform (imbalance prices, day-ahead prices)
and Open-Meteo (forecast archive, not reanalysis). 15-minute ISPs from
2024-10-18 (PICASSO go-live) to 2026-08-01. Publication lags enforced in code
via `data_availability.py` — every feature must pass an availability check
before use.

**Features.** Lagged settlement price (freshest available, and 1-week lag),
day-ahead price, cyclically-encoded hour and day-of-week. Each feature has a
documented source and publication lag in [`docs/FEATURES.md`](docs/FEATURES.md).

**Models.** Five baselines (persistence, seasonal naive 1d/1w, climatological
quantiles, day-ahead price), LEAR (regularised linear quantile regression),
and quantile-GBM (LightGBM with quantile loss, one model per quantile).
Quantile crossing handled by post-hoc sorting. All models share a common
interface: `fit(X, y)`, `predict_quantiles(X, quantiles)`.

**Evaluation protocol.** Expanding-origin walk-forward with monthly folds, no
random splits. Pinball loss, CRPS, calibration (reliability curves, PIT
histogram), MAE/RMSE, Diebold-Mariano significance tests with
Holm-Bonferroni correction over 5 comparisons. Results segmented by hour,
season, year, and dual-pricing state. 7 model configurations tried across the
project — reported for multiple-testing context.

**Dispatch.** Rolling-horizon LP (cvxpy, CLARABEL solver). Three policies:
deterministic (median forecast), CVaR (stratified quantile scenarios,
risk-aversion sweep), perfect foresight (upper bound). Battery: 10 MW /
40 MWh, 95% round-trip efficiency, EUR 5/MWh degradation cost, terminal
SoC penalty.

**Settlement.** Actual Dutch rules from `config/market_rules.yaml`, including
dual pricing in regulation state 2.

## Limitations (prominently placed, not at the bottom)

1. **Market impact is modelled, not measured.** The sqrt impact model is a
   structural assumption. The saturation curve is directionally correct but the
   shape is uncalibrated.
2. **Holdout improvement may be period-specific.** The 27.9% PF ratio on
   holdout vs 15.9% on walk-forward likely reflects market conditions in
   May–Jul 2026, not model improvement.
3. **Data revision vintages cannot be fully reconstructed.** ENTSO-E serves the
   current vintage. The backtest uses what was available at pull time, not the
   historically-published value.
4. **7 model configurations explored.** The search space is small but
   non-zero — reported performance should be read with this context.
5. **Backtest assumes settlement at the imbalance price.** Real operation
   involves bidding and activation uncertainty.

Full list: [LIMITATIONS.md](LIMITATIONS.md).

## Reproduction

```bash
uv sync                              # install dependencies
make test                            # 292 tests
make check                           # lint + typecheck + test
make backtest                        # reproduce backtest numbers
make serve                           # Streamlit dashboard at localhost:8501
make serve-api                       # FastAPI at localhost:8000
```

Requires [`uv`](https://docs.astral.sh/uv/) (it fetches the pinned Python).
Copy `.env.example` to `.env` and add an ENTSO-E API token for data fetching.

## Demo

Four-screen Streamlit dashboard:

1. **Live Forecast** — current ISP prediction with fan chart (once the
   scheduled job is running and accumulating a track record).
2. **Track Record** — pinball loss table, calibration curves, PIT histogram,
   DM significance tests, results segmented by hour and season.
3. **Backtest Explorer** — efficient frontier, revenue-per-MW saturation curve,
   per-year breakdown with bootstrap CIs, final holdout comparison.
4. **What-If Simulator** — adjust battery parameters and risk aversion, see
   scaled revenue estimate with uncertainty bands.

## Repo structure

```
├── config/market_rules.yaml     # settlement logic (single source of truth)
├── src/
│   ├── data/                    # fetchers, cache, data_availability.py
│   ├── features/                # builder, targets
│   ├── models/                  # baselines, lear, gbm (common interface)
│   ├── evaluation/              # metrics, walk-forward harness
│   ├── optimisation/            # battery dispatch (deterministic, CVaR, PF)
│   ├── backtest/                # event-driven engine, bootstrap CIs
│   ├── api/                     # FastAPI backend
│   └── jobs/                    # scheduled forecast + weather vintage logging
├── frontend/app.py              # Streamlit dashboard
├── scripts/                     # walk-forward eval, backtest, holdout eval
├── tests/                       # 292 tests
└── docs/                        # DOMAIN_NOTES, FEATURES, DECISIONS, REPORT
```

## Status

| Phase | State |
|---|---|
| 0 — Domain | done |
| 1 — Data | done |
| 2 — Forecasting | done |
| 3 — Dispatch | done |
| 4 — Backtest | done |
| 5 — Demo | done |
| 6 — Write-up | done |

---

*Research tool. Backtested results under stated assumptions. Not investment
advice and not a guarantee of returns.*
