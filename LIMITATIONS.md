# Limitations

Maintained from day one, not written at the end. Everything here is a known
weakness in the evidence this project produces. **Read this before interpreting
any result in the README or the demo.**

## Data and information timing

### Data revision vintages cannot be fully reconstructed
ENTSO-E's API serves the current vintage, not the historically-published one.
For revised series (load forecasts, actual generation, cross-border flows) a
history pulled today is not what was visible at decision time. R1 requires the
first-published vintage. Mitigation is partial: capture vintages live going
forward and bound the bias on the backtest period. The bias is bounded by the
typical revision magnitude (low single-digit EUR/MWh for prices; larger for
generation and flow series that are not currently used as features).

### The balance-delta lag is measured, but over a narrow window
**Measured 2026-08-07: 134 s** — the p95 of 630 samples across 10 ISPs.
Coverage: 2.18 hours of a single weekday afternoon (12:15–14:25 UTC). Not
observed: overnight, weekends, or scarcity — exactly the conditions where a
battery earns most. TenneT describes the delay as *configurable*, so stability
over two hours is not evidence of stability over a year. Re-measure across a
full 24 h before any headline revenue figure is relied upon.

### The regulation state changed meaning on 2026-02-03
From 3 February 2026 TenneT determines the regulation state from the 12-second
balance delta (75 samples per ISP) rather than the 1-minute series (15 samples).
State 2 (dual-priced) should become more frequent with no change in the physical
system. Only ~6 months of post-change data exist in the sample. This is a
target-variable regime change that is invisible in the price series.

### PICASSO shifted the price distribution
PICASSO (18 Oct 2024) relocates residual power imbalance "to the area with
the most economical bids" (v6.1 §3.4), which truncates price extremes.
Secondary sources report volatility roughly halved. Results are segmented by
year, but a model fitted mostly on pre-PICASSO data will be miscalibrated on
the tails that matter most for battery revenue.

### The day-ahead/imbalance spread is not comparable across 2025-10-01
The day-ahead MTU changed from 60 to 15 minutes on delivery day 1 October 2025.
T3 (spread target) has a step-function artefact before that date.

## Market impact and capacity

### Market impact is modelled, not measured
The sqrt(capacity / 500 MW) model is an assumed functional form, not an
estimated one. Revenue-per-MW degrades from EUR 88K/MW at 1 MW to EUR 39K/MW at
100 MW. At 500 MW the signal is fully absorbed. The saturation curve is
directionally correct — a public-signal strategy must degrade as capacity
scales — but the level is uncalibrated. The model also applies impact post-hoc:
the battery does not re-optimise given its own impact, overstating losses at
high capacity.

### NL battery capacity figures are unreliable
Public sources conflict and routinely mix GW with GWh, and *operational* with
*in realisation* with *queued*. The market-impact argument rests on a
qualitative claim rather than one authoritative figure.

### Signal decay with adoption
The strategy uses only publicly available data. As more participants run similar
models, the collective response pushes the system toward balance, reducing the
signal. This is the fundamental scalability limit and is not measurable from
historical data alone.

## Backtest realism

### Backtest assumes settlement at the imbalance price
Real battery operation involves bidding into the balancing market, activation
uncertainty, and the possibility of non-activation. The backtest treats the
battery as a passive balancer that always settles at the published price.

### No modelling of the operator's other positions
The dispatch optimises the battery in isolation. A real operator co-optimises
across multiple assets and market positions.

### Terminal SoC penalty is a modelling choice
The rolling-horizon dispatch uses a EUR 50/MWh terminal penalty toward 50% SoC.
This prevents end-of-window dumping but introduces a conservative bias versus
a full water-value approach.

## Model and evaluation

### 7 model configurations explored
5 baselines + LEAR + GBM. The search space is small — no hyperparameter grid
search was performed on the walk-forward folds — but non-zero. Reported
performance should be interpreted as the result of choosing among these 7, not
as the performance of a randomly selected model.

### Holdout improvement may be period-specific
The final holdout (May–Jul 2026) shows 27.9% PF capture vs 15.9% on
walk-forward. This likely reflects a more favourable market period rather than
model improvement — the training set grows by only ~10%. The improvement is
reported honestly but should not be extrapolated.

### Regime dependence
Per-year revenue breakdown shows significant variation: 2024 (2 months) EUR 74K,
2025 (full year) EUR 981K, 2026 walk-forward (4 months) EUR 143K. A strategy
that earns most of its revenue in one volatile year is a finding, not a proof of
robustness.

### Quantile crossing handled by post-hoc sorting
The LEAR and GBM models fit independent quantile regressions. Crossings are
resolved by sorting quantiles at each prediction point. This is a standard
approach but less principled than monotone-by-construction methods.

### Feature set is minimal
7 features (lagged prices, day-ahead price, hour, day-of-week). No weather
features, no cross-border flows, no bid-ladder information, no load forecasts.
The minimal set was a deliberate choice (each feature must justify its
availability constraint), but it means the model leaves known signal on the
table.

## Scope excluded by choice

- Intra-ISP re-decision (every 12 s) — where the real commercial value sits.
- Intraday market integration.
- Multi-asset portfolio optimisation.
- Reinforcement learning dispatch policies (stretch direction).

## Price limits

Balancing-energy bid limits (±15,000 EUR/MWh transitional to July 2026,
±99,999 EUR/MWh technical thereafter) come from search summaries, not the
ACER decision text. The model does not clip prices at these limits.

---

*This file is worth more in an interview than another two points of accuracy.*
