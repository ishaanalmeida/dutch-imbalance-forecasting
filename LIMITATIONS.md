# Limitations

Maintained from day one, not written at the end. Everything here is a known
weakness in the evidence this project produces.

**Project status: Phase 0 (domain verification) complete and awaiting review. No
data has been fetched, no model trained, no backtest run.** Every performance,
revenue and accuracy claim below is therefore `TODO: not yet measured` (R3).

## Established in Phase 0

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

### The balance-delta publication lag is unknown and will be measured, not cited
The widely-repeated 3 → 5 → 2 minute *delay* timeline could not be substantiated
against TenneT's own pages, which document only **cadence** changes (1/min →
5/min → every 12 s) and describe an added delay as an option with "no concrete
plans" as of 2025-10-28. The two were likely conflated in trade press.

The lag is therefore `null` in config and Phase 1 measures it from the data.
Until then no feature may be built from balance delta. If the measurement proves
noisy or time-varying in a way we cannot pin down, every revenue figure inherits
that uncertainty and it must be reported as a sensitivity, not hidden in a
point estimate.

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
