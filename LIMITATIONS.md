# Limitations

Maintained from day one, not written at the end. Everything here is a known
weakness in the evidence this project produces.

**Project status: Phase 0 (domain verification) complete and awaiting review. No
data has been fetched, no model trained, no backtest run.** Every performance,
revenue and accuracy claim below is therefore `TODO: not yet measured` (R3).

## Established in Phase 0

### The methodology document predates a major market change
TenneT's *Imbalance Pricing System* v6.0 is dated **30 March 2022**. The
Netherlands joined the European aFRR platform **PICASSO on 18 October 2024**. No
newer methodology version was located. Secondary sources indicate TenneT kept
setting the imbalance price from the domestic marginal activated bid rather than
PICASSO's cross-border marginal price — **unverified**. If that is wrong, price
formation differs before and after 2024-10-18 and the usable training history is
~22 months, short of the 3-year minimum in the brief. See `docs/DOMAIN_NOTES.md` Q5.

### The intra-ISP information window changed repeatedly, and the dates are unverified
TenneT changed the balance-delta publication delay at least three times between
late 2024 and late 2025 (≈3 → 5 → 2 minutes), *explicitly to change how
profitable passive balancing is*. The timeline is secondary-sourced only;
`tennet.eu` blocked automated retrieval. A backtest that applies one constant lag
across history is measuring a strategy that never existed.

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
