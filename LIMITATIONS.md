# Limitations

Maintained from day one, not written at the end. Everything here is a known
weakness in the evidence this project produces.

**Project status (2026-08-08): Phase 1 data layer built and connected to live
data. ENTSO-E and TenneT credentials are working; ~8 months of NL imbalance
prices have been read. No model has been trained and no backtest has been run**,
so every performance, revenue and accuracy claim remains `TODO: not yet
measured` (R3).

## Established in Phases 0-1

### PICASSO shifted the price distribution, even though the rules held
Resolved in review: TenneT's *Imbalance Pricing System* **v6.1 (21 Oct 2024)**
confirms the CBMP is "not explicitly taken into account in the price
determination" and that IGCC/PICASSO have "no direct impact on the regulation
state". Price formation is therefore consistent across the sample.

The *distribution* is not. v6.1 §3.4 states PICASSO relocates residual power
imbalance "to the area with the most economical bids", which walks the domestic
merit order less far and truncates price extremes; secondary sources report
volatility roughly halved. Results spanning 2024-10-18 must be segmented, and a
model fitted mostly on pre-PICASSO data will be miscalibrated on the tails that
matter most for battery revenue.

### The balance-delta lag is measured, but over a narrow window

**Measured 2026-08-07: 134 s** — the p95 of 630 samples across 10 ISPs, with a
total spread of 1.0 second (min 133.0, median 133.5, max 134.0, σ 0.29). The
field is enabled and `available_at` now serves it. Provenance is recorded
inline in `config/market_rules.yaml` under `lag_measurement`.

**Coverage is the live weakness.** 2.18 hours of a single weekday afternoon
(12:15–14:25 UTC). Not observed: overnight, weekends, or scarcity and
high-volatility periods — which are exactly the conditions where a battery
earns most, and where a TSO is most likely to intervene. TenneT's own API
documentation describes the delay as *configurable*, i.e. a knob they set, so
stability over two hours is not evidence of stability over a year.
**Re-measure across a full 24 h before any headline revenue figure depends on
this**, and again after any announced TenneT platform change.

**Granularity is a second, quieter cost.** Balance delta publishes every 12
seconds, but availability is answered at ISP granularity — the whole ISP is
reported as arriving 134 s after the ISP *ends*. That is conservative and
therefore safe under R1, but it discards up to ~12.8 minutes of the previous
ISP's intra-period shape, and intra-ISP shape is precisely what determines the
regulation state. So the conservatism bites hardest on the T1 classification
target. Point-level availability (`point_end + lag`) is a Phase 2 task
(ADR-021).

**On the trade press.** The widely-repeated "2 minutes" turns out close to
right as a *current* value. The 3 → 5 → 2 minute *timeline* remains unsupported
by any primary source, and this measurement says nothing about historical
values — so a backtest over 2024–25 still has no measured lag for its own
period, and must either treat that as a sensitivity or restrict itself.

### The regulation state changed meaning on 2026-02-03
From 3 February 2026 TenneT determines the regulation state from the 12-second
balance delta rather than the 1-minute series — 75 samples per ISP instead of
15. The rule wording is unchanged, but exact monotonicity is less likely over
more samples, so **state 2 (the only dual-priced state) should become more
frequent with no change in the physical system.** T1 class priors break at this
date and only ~6 months of post-change data exist. This is a target-variable
regime change that is invisible in the price series.

### Data revision vintages cannot be fully reconstructed
ENTSO-E's API serves the current vintage, not the historically-published one.
For revised series (load forecasts, actual generation, cross-border flows) a
history pulled today is not what was visible at decision time. R1 requires the
first-published vintage. Mitigation is partial: capture vintages live going
forward and bound the bias on the backtest period. **The bias is not yet
quantified.**

### The day-ahead/imbalance spread is not comparable across 2025-10-01
The day-ahead MTU changed from 60 to 15 minutes on delivery day 1 October 2025.
Before then one day-ahead price covered four ISPs, giving target T3 a
step-function artefact. Results spanning that date must be segmented.

### Regulation state is a path property, not a level
State 2 (the dual-priced state) depends on whether the intra-ISP balance-delta
series both rises and falls — not on any end-of-period quantity. State 2 is
therefore mechanically correlated with intra-period volatility, i.e. with price
extremity. State classification and price magnitude are not independent errors.

### NL battery capacity figures are unreliable
Public sources conflict and routinely mix GW with GWh, and *operational* with
*in realisation* with *queued*. The market-impact argument currently rests on a
qualitative claim (single-digit GW, growing fast, queue an order of magnitude
larger) rather than one authoritative figure.

### Price limits are secondary-sourced
Balancing-energy bid limits (±15 000 €/MWh transitional to July 2026, ±99 999
€/MWh technical thereafter) come from search summaries, not the ACER decision
text. Whether a proposed ±10 000 €/MWh transitional limit was adopted is unknown.

### Scope excluded by choice
The decision timestamp is set at ISP start, so the model decides once per ISP.
Real passive balancers re-decide continuously *within* the ISP (every 12 s since
late 2025). That intra-ISP policy is more realistic and is where the money is;
it is deliberately out of scope and would require a different Phase 3 dispatch
formulation, not merely a different cutoff.

## To be established in later phases

- Market impact is **modelled, not measured**. `TODO: not yet measured`
- No modelling of any other positions in the operator's portfolio.
- Backtest assumes settlement at the imbalance price; real operation involves
  bidding and activation uncertainty.
- Number of model configurations explored, and its effect on reported
  performance. `TODO: not yet measured` — counter starts at Phase 2.
- Regime dependence: which years worked, which did not. `TODO: not yet measured`
