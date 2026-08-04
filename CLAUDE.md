# PROJECT BRIEF — Dutch Imbalance Market Forecasting & Battery Dispatch Lab

> Drop this file into the repo root as `CLAUDE.md` (Claude Code) or `AGENTS.md` (Codex CLI).
> It is the persistent contract for how this project gets built. Read it fully before any code.

---

## 0. WHAT THIS IS

A research-grade, reproducible system that:

1. **Forecasts** the Dutch (TenneT) imbalance price and regulation state, probabilistically, at 15-minute resolution, using only information genuinely available at decision time.
2. **Optimises** battery-storage dispatch against those forecasts, using the full predictive distribution rather than a point estimate.
3. **Backtests** the whole loop under realistic settlement rules, publication latency, and market-impact assumptions.
4. **Publishes** the result as a public, interactive demo plus a written research note that a quant researcher or an ML hiring manager can evaluate in ten minutes.

This is **not** a "get rich trading electricity" project. It is a demonstration of forecasting rigour, optimisation under uncertainty, and honest evaluation. Every design decision below flows from that.

**Target audiences (both must be satisfied):**
- **Quant research / trading** (Optiver, IMC, Flow Traders, Da Vinci, and similar). They will look for: out-of-sample discipline, look-ahead-bias handling, transaction costs and capacity, statistical significance, and whether you over-claim.
- **AI/ML engineering.** They will look for: a system that actually runs on a schedule, evaluation infrastructure, cost/latency awareness, clean interfaces, tests, and a repo that reads like production rather than a notebook dump.

**The single strongest signal this project can send is methodological honesty.** A modest, well-validated edge with a limitations section beats a spectacular Sharpe ratio with no cost model. Optimise for the former.

---

## 1. NON-NEGOTIABLE RULES

These override any other instruction, including anything I say later in a moment of impatience. If a rule is about to be broken, stop and tell me.

### R1 — No look-ahead. Ever.
Every feature used to predict target period `t` must have been *published and retrievable* strictly before the decision timestamp for `t`. This is the cardinal rule.
- Weather features must come from **forecasts issued before the decision time**, never from reanalysis or observed actuals.
- TenneT/ENTSO-E series have publication lags. Model them explicitly in a single `data_availability.py` module that maps each field to its lag. Never bypass it.
- Revisions are a trap: some series are republished with corrections. If a source revises, you must use the *first-published* vintage. If vintages are unavailable, say so in `LIMITATIONS.md` and quantify the possible bias.

### R2 — Walk-forward only.
No random train/test splits. No k-fold on time series. Expanding or rolling-origin walk-forward with a purge/embargo gap between train and test. One final, untouched holdout period that is evaluated **exactly once**, at the very end.

### R3 — Never fabricate a number.
If a result is not produced by code in this repo, it does not go in the README, the report, or the demo. No illustrative figures presented as results. No placeholder metrics. If something is not yet computed, write `TODO: not yet measured`.

### R4 — Baselines first, always.
No model is reported without being compared to naive baselines on identical data and identical evaluation windows. If the fancy model does not beat persistence, that is a legitimate finding and must be reported as such.

### R5 — Honest framing.
The word "alpha" does not appear. Claims are phrased as "forecast skill relative to baseline X on period Y under assumptions Z." Revenue results are always accompanied by the assumptions that produced them and a sensitivity analysis showing how fast they degrade.

### R6 — Reproducibility.
`make repro` must reproduce every number and figure in the report from cached raw data. Pin dependencies. Seed everything. Log versions.

### R7 — Licensing discipline.
Never commit raw third-party market data to the repo. Cache locally, `.gitignore` it, and ship a fetch script instead. Some ENTSO-E items are derived from exchange feeds (EPEX SPOT) whose terms restrict redistribution. Derived analytics and model outputs are fine to publish; raw licensed series are not. Document the licence position of every source in `docs/DATA_SOURCES.md`.

### R8 — Secrets.
API tokens go in `.env`, never in code, never in commits. Ship `.env.example`.

---

## 2. PHASE 0 — VERIFY THE DOMAIN BEFORE WRITING MODELLING CODE

**Do this first. Do not skip it. Do not assume you already know these answers, and do not rely on my summary or your training data — both may be stale.**

Produce `docs/DOMAIN_NOTES.md` answering the following, each with a **primary-source citation** (TenneT documentation, ENTSO-E manuals, ACM/regulatory documents, EU network codes). Where sources conflict or are ambiguous, say so explicitly rather than picking one.

**Settlement mechanics**
1. What is the current Imbalance Settlement Period length in the Netherlands, and when did it last change?
2. Exactly how is the Dutch imbalance price formed? Define every regulation state, its numeric code, and what each means.
3. Under which regulation state(s) does **dual pricing** apply, and precisely how are the "feed" (long) and "take" (short) prices determined in that state? Get the sign conventions right and write down a worked example.
4. What is the "incentive component" / passive-contribution term, how large is it, and how does it enter the settlement price?
5. How do aFRR and mFRR activations map into the imbalance price? What is the merit-order/marginal-pricing logic?
6. Are there price caps, floors, or special rules during scarcity?

**Data timing — this determines what your model is allowed to see**
7. For each series you plan to use (balance delta, current imbalance price estimate, regulation state, activated volumes, day-ahead price, load forecast, wind/solar forecast, cross-border flows): what is its publication cadence and its publication lag relative to the period it describes?
8. Which series are *revised* after first publication? Which are final-at-first-publication?
9. What is the realistic decision timestamp for a battery operator acting within an ISP? Justify it. This becomes the hard information cutoff in the backtest.

**Market structure**
10. What is the current MTU for day-ahead and intraday markets in NL, and did it change recently? How does that interact with the ISP?
11. Roughly what is the installed grid-scale battery capacity in NL and how fast is it growing? (This matters for the market-impact argument.)

**Deliverable:** `docs/DOMAIN_NOTES.md`, plus a machine-readable `config/market_rules.yaml` encoding the settlement logic, regulation-state codes, and publication lags. Every downstream module reads from that config — the rules must live in exactly one place.

**Gate:** Do not proceed to Phase 2 modelling until I have reviewed `DOMAIN_NOTES.md`. Ask me to review it explicitly.

---

## 3. PHASE 1 — DATA LAYER

### Sources (all free; verify terms before any commercial use)

| Source | What | Access |
|---|---|---|
| **ENTSO-E Transparency Platform** | Imbalance prices & volumes, day-ahead prices, load & generation forecasts, actual generation by type, cross-border flows | Free REST API. Register an account, then email `transparency@entsoe.eu` with subject "RESTful API access" and your account email. Token typically issued within a few working days. Use `entsoe-py`. |
| **TenneT** (Dutch TSO) | NL-specific near-real-time balance delta, settlement prices, regulation state, bid ladder | Public data export / API. Higher-resolution and more NL-specific than ENTSO-E — verify what is available and at what cadence. |
| **Open-Meteo** | Historical *forecast* archive (critical: gives you the forecast as issued, not the reanalysis) plus live forecasts. Wind speed at hub height, irradiance, temperature. | Free, no key for non-commercial use. Check current terms. |
| **KNMI** | Dutch station observations and models | Free with registration. Optional. |

**Get the weather archive right.** You need what the forecast *said at the time*, not what the weather *was*. Open-Meteo's historical forecast API is designed for exactly this. If you accidentally use ERA5 reanalysis as a feature, your model will look brilliant and be worthless — this is the most likely way this project silently fails.

### Requirements
- Idempotent incremental fetchers with retry/backoff and rate-limit respect.
- Local cache in Parquet, partitioned by month. Raw responses stored before parsing so you can re-parse without re-fetching.
- A single canonical time index: UTC internally, Europe/Amsterdam only at presentation. Handle DST transitions explicitly and **write tests for the 23-hour and 25-hour days** — this breaks naive energy pipelines every year.
- `data_availability.py`: for every field, a function `available_at(field, target_period) -> timestamp`. The feature builder must call this and refuse to emit a feature that violates it. Unit-test it.
- Data quality report: gap detection, duplicate periods, outliers, regulation-state distribution over time, structural breaks. Save to `docs/DATA_QUALITY.md`.
- Target coverage: as much history as the APIs allow, minimum 3 years. More is better for regime coverage.

**Gate:** a test suite proving no feature can be constructed from information published after its decision timestamp.

---

## 4. PHASE 2 — FORECASTING

### Targets (build all three; they are used differently downstream)
- **T1 — Regulation state / imbalance sign**: multiclass classification. Often more predictable and more actionable than the price level.
- **T2 — Imbalance price**: probabilistic, predicting a set of quantiles (at minimum 0.05 … 0.95 in steps of 0.05). Must handle the dual-price case correctly — that may mean two separate targets (feed price and take price) depending on what Phase 0 established.
- **T3 — Spread vs day-ahead price**: often the more stationary and more tradeable quantity. Consider modelling this instead of the raw level.

Forecast horizons: whatever is decision-relevant given Phase 0 Q9 — likely the current ISP and the next 1–8 ISPs. Justify the choice.

### Baselines (mandatory, implement before any model)
1. Persistence (last observed value).
2. Seasonal naive (same period, previous day / previous week).
3. Climatological quantiles conditioned on hour-of-day and day-of-week — this is a surprisingly strong probabilistic baseline and many papers under-report it.
4. Day-ahead price as a predictor of imbalance price.
5. For the classifier: majority class and a conditional-frequency-table model.

### Models (in this order — do not skip ahead)
1. **Quantile regression / LEAR-style regularised linear model.** The standard benchmark in the electricity price forecasting literature. Linear, interpretable, fast, and surprisingly hard to beat. Any nonlinear model must beat this to justify itself.
2. **Gradient boosting with quantile loss** (LightGBM `objective="quantile"`, one model per quantile, or a distributional variant). Expect this to be the workhorse.
3. **Distributional neural network** — quantile regression network or a parametric distributional head. Only build this if 1 and 2 are complete and evaluated, and report the compute cost.

Address **quantile crossing** explicitly (post-hoc sorting or a monotone-by-construction approach) and say which you used.

### Features
Build a documented feature catalogue in `docs/FEATURES.md`, each entry stating its source, its publication lag, and its economic rationale. Sketch:
- Lagged balance delta and recent regulation-state history (respecting lag).
- Day-ahead price and its shape; spread to neighbouring bidding zones.
- Wind and solar forecast, and — importantly — **forecast error proxies**: the difference between the latest forecast and an earlier vintage of the same forecast, which is a legitimate leading indicator of imbalance.
- Load forecast and residual load.
- Calendar: hour, day-of-week, holiday, month; encode cyclically.
- Cross-border flows and interconnector availability.
- Ramp features: forecast gradient across period boundaries.

Do **not** dump every feature into the model and call it feature engineering. Each feature needs a reason, and you must report a permutation-importance or SHAP analysis at the end.

### Evaluation — this is where the project earns its credibility

Implement in `src/evaluation/` and treat it as a first-class product, not an afterthought.

**Probabilistic accuracy**
- Pinball (quantile) loss per quantile and aggregated.
- CRPS, approximated from the quantile set.
- Reliability: empirical coverage vs nominal for every quantile, plotted.
- PIT histogram and a rank histogram. A flat PIT is the goal; report the shape honestly.
- Sharpness conditional on calibration (interval widths).

**Point accuracy** (secondary, for comparability with literature): MAE, RMSE, sMAPE — with an explicit warning that percentage errors are meaningless near zero prices, which happens constantly here.

**Classification (T1)**: confusion matrix, per-class F1, log loss, Brier score, and a reliability diagram for the predicted probabilities. Calibration matters more than accuracy for downstream optimisation.

**Statistical significance** — the part most portfolio projects skip and quant interviewers ask about:
- Diebold-Mariano test (or Giacomini-White for conditional predictive ability) comparing each model against each baseline, with autocorrelation-robust standard errors.
- Correct for multiple comparisons across the model grid. State the correction used.
- Report the number of model configurations tried across the whole project. Be honest about the search space — this is what deflated performance metrics exist to address, and volunteering it is a strong signal.

**Segmented reporting**: overall numbers hide everything. Break results down by regulation state, by hour of day, by season, by high-vs-low volatility regime, and by year. Report where the model fails as prominently as where it succeeds.

---

## 5. PHASE 3 — BATTERY DISPATCH OPTIMISATION

This is what turns a forecasting exercise into a decision system, and it is where the probabilistic forecast pays for itself.

### Model
Rolling-horizon optimisation over the forecast window. Use `cvxpy` (or `PuLP`/`HiGHS` if a MILP formulation is needed).

Decision variables: charge power, discharge power, state of charge per period.

Constraints:
- Power limits (charge and discharge, possibly asymmetric).
- Energy capacity and SoC bounds.
- Round-trip efficiency, split into charge and discharge legs.
- Grid connection limit.
- Optional: mutual exclusivity of charge/discharge (binary → MILP; check whether the LP relaxation is naturally exclusive given the price structure first, and only add binaries if needed).

Objective: expected settlement revenue, minus:
- A **degradation cost per MWh throughput** (calibrate to a plausible cell cost and cycle life — cite your assumption).
- A **terminal SoC value** so the horizon boundary does not induce degenerate end-of-window dumping. Use a rolling water-value or a simple penalty toward a target SoC, and justify it.

### The interesting part: three dispatch policies, compared
1. **Deterministic** — optimise against the median forecast.
2. **Risk-aware / stochastic** — optimise against the forecast distribution. Either scenario-based stochastic programming (sample scenarios from the quantile forecast, preserving temporal correlation via a copula or a Schaake shuffle — do not sample quantiles independently across time, that destroys the ramp structure) or a CVaR objective with an explicit risk-aversion parameter.
3. **Perfect foresight** — the upper bound. Not achievable, but essential context: it tells you what fraction of the theoretical maximum your forecast captures. Report that ratio; it is the single most informative number in the project.

Plus the naive baselines: do nothing, and a simple day-ahead-only arbitrage strategy.

Sweep the risk-aversion parameter and show the efficient frontier of expected revenue vs revenue volatility / CVaR. **This plot is the centrepiece of the project.** It demonstrates that you understand why probabilistic forecasting exists.

---

## 6. PHASE 4 — BACKTEST REALISM

A backtest that ignores the following is not evidence of anything.

**Information timing.** The backtest engine must be event-driven: at each decision point it may only access data whose `available_at` timestamp precedes the decision. Enforce this in code, not by convention. Write a test that deliberately tries to leak and asserts the engine refuses.

**Settlement.** Settle using the actual rules from `config/market_rules.yaml`, including dual pricing where applicable. Get the sign conventions right and unit-test them against hand-worked examples from `DOMAIN_NOTES.md`.

**Market impact and capacity.** This is the intellectually honest core of the project and a strong interview topic. A public-signal strategy degrades as capacity scales, because acting on the imbalance signal pushes the system back toward balance.
- Model impact as a function of your position size relative to system balance delta. Start with a simple linear or square-root impact model; be explicit that it is an assumption, not a measurement.
- Produce a **revenue-per-MW vs deployed-MW curve**. Show where the strategy saturates.
- Discuss signal decay: what happens when many participants run the same public-data model. You cannot measure this directly, but you can reason about it and cite the growth in NL battery capacity from Phase 0 Q11.

**Costs.** Imbalance settlement itself is the cost mechanism here, but also include: any applicable grid fees or tariffs, and an explicit note on what you have *not* modelled.

**Uncertainty on the result.** Do not report a single revenue number. Use a stationary block bootstrap over the backtest period to produce confidence intervals on annualised revenue and on the ratio-to-perfect-foresight. Report by year separately — a strategy that only worked in one volatile year is a fact worth surfacing.

**The final holdout.** One contiguous, most-recent period, never touched during development, evaluated once. If results degrade there versus the walk-forward period, report that prominently. That degradation, honestly reported, is more credible than a uniformly good result.

---

## 7. PHASE 5 — THE DEMO

Assume the viewer is a hiring manager with ten minutes and mild scepticism. The demo must make the *methodology* legible, not just show a pretty number.

### Architecture
- **Backend**: FastAPI. Endpoints for latest forecast, historical forecast performance, backtest results, and a what-if dispatch simulation.
- **Frontend**: keep it simple and fast. A React SPA or Streamlit — pick one and justify it in the README. Do not spend a third of the project on CSS.
- **Storage**: SQLite or DuckDB for served data. DuckDB is a good fit for the Parquet-backed analytics.
- **Scheduled job**: a GitHub Actions cron (or a small worker) that fetches new data, produces a live forecast, and — critically — **logs the forecast at the time it was made**, so that after a few weeks you have a genuine, unfalsifiable out-of-sample track record. Start this job as early as possible in the project; it accrues value with wall-clock time and nothing else does.
- **Deployment**: something free-tier and stable (Fly.io, Railway, Hugging Face Spaces, Render). Custom domain optional. Must survive being clicked on months later — if it 502s during an interview it is worse than not existing.

### The four screens
1. **Live forecast** — current and next ISPs, fan chart of the predictive distribution, predicted regulation state with probabilities, and the model's recommended dispatch action. Include a "last updated" timestamp.
2. **Track record** — the accumulating live-forecast log versus realised outcomes: rolling pinball loss, calibration over the live period, comparison to baselines. This is the screen that separates you from every other portfolio project, because it cannot be back-fitted.
3. **Backtest explorer** — filter by period, regulation state, hour; see the revenue attribution, the efficient frontier, the ratio-to-perfect-foresight, and the revenue-per-MW saturation curve.
4. **What-if simulator** — user sets battery size, power rating, efficiency, degradation cost, risk aversion; sees resulting expected revenue with uncertainty bands. This is the screen an actual battery operator would use, and it is the seed of the product story.

### Framing on every screen
A persistent, visible statement: this is a research tool, results are backtested under stated assumptions, this is not investment advice and not a guarantee of returns. Not a legal shield — a credibility signal.

---

## 8. PHASE 6 — WRITE-UP

### `README.md` — structured as a research note, not a tutorial
1. One-paragraph problem statement: what decision, whose money, why it is hard.
2. Headline result in one sentence, with its assumptions attached.
3. The three or four figures that carry the argument (calibration, efficient frontier, ratio-to-perfect-foresight by year, revenue-per-MW saturation).
4. Method summary — data, features, models, walk-forward protocol.
5. **Limitations, prominently placed and not at the bottom.**
6. Reproduction instructions.
7. Link to the live demo.

### `docs/REPORT.md` — the full version
Everything above at length, plus the evaluation tables, significance tests, ablations, segmented results, and the full feature analysis. Aim for something you would not be embarrassed to attach to a thesis appendix or send to a researcher.

### `LIMITATIONS.md` — a standalone file
Enumerate, without softening:
- Data vintage/revision issues you could not fully resolve.
- Market impact is modelled, not measured.
- No modelling of your own portfolio's other positions.
- Backtest assumes fills at settlement price; real operation involves bidding and activation uncertainty.
- Number of model configurations explored and what that means for the reported performance.
- Regime dependence: which years worked, which did not.

Writing this file well is worth more in an interview than another two points of accuracy.

---

## 9. REPO STRUCTURE

```
.
├── CLAUDE.md                  # this file
├── README.md
├── LIMITATIONS.md
├── Makefile                   # fetch, features, train, backtest, report, repro, serve, test
├── pyproject.toml             # pinned deps (uv or poetry)
├── .env.example
├── config/
│   ├── market_rules.yaml      # settlement logic, reg-state codes, publication lags
│   ├── features.yaml
│   └── models/                # one config per model variant
├── data/                      # gitignored
│   ├── raw/
│   ├── interim/
│   └── processed/
├── src/
│   ├── data/                  # fetchers, cache, data_availability.py
│   ├── features/              # builders; must consult data_availability
│   ├── models/                # baselines, lear, gbm, dnn — common interface
│   ├── evaluation/            # metrics, calibration, significance tests, plots
│   ├── optimisation/          # dispatch policies
│   ├── backtest/              # event-driven engine
│   ├── api/                   # FastAPI
│   └── jobs/                  # scheduled live forecast
├── frontend/
├── notebooks/                 # exploration only; nothing in the report depends on a notebook
├── tests/
└── docs/
    ├── DOMAIN_NOTES.md
    ├── DATA_SOURCES.md
    ├── DATA_QUALITY.md
    ├── FEATURES.md
    ├── DECISIONS.md           # ADR-style log of every significant choice + why
    └── REPORT.md
```

**All models share one interface**: `fit(X, y)`, `predict_quantiles(X, quantiles) -> array`, `predict_proba(X)` for classifiers. Swapping models must require no changes to evaluation, optimisation, or backtest code.

---

## 10. ENGINEERING STANDARDS

- Python 3.11+. `uv` for dependency management. Type hints throughout; `mypy` in CI.
- `ruff` for lint and format. Pre-commit hooks.
- `pytest`. Minimum coverage targets: data-availability logic and settlement logic at 100%, everything else pragmatic.
- **Mandatory tests**: no-look-ahead enforcement; DST transition handling; settlement price calculation against hand-worked examples; quantile monotonicity; backtest engine refuses future data.
- Structured logging. Every model run writes a manifest: git SHA, config hash, data vintage, seed, metrics.
- Experiment tracking: MLflow or a simple JSON-per-run registry. Do not lose track of what you tried — you need the count for the multiple-testing disclosure.
- CI: lint, type-check, test, and a fast smoke backtest on every push.
- Every commit message states what changed and why.

---

## 11. HOW TO WORK WITH ME

**Sequencing.** Work phase by phase. At the end of each phase, produce a short summary of what was built, what the numbers say, what surprised you, and what you recommend next. Then stop and wait.

**Ask when it matters.** Ask before: choosing a modelling approach not listed here, adding a heavy dependency, making a domain assumption that materially changes results, or deciding what goes in the final holdout. Do not ask permission for routine implementation.

**Push back.** If an instruction in this file turns out to be wrong once the data is in hand — say so with evidence. This document is a plan, not scripture. Log the disagreement and the resolution in `docs/DECISIONS.md`.

**Report bad news immediately and plainly.** "The GBM does not beat the LEAR baseline and here is the DM test" is a valuable message. Quietly tuning until something looks good is the failure mode that destroys this project's value.

**Never do these:**
- Silently change the evaluation protocol to improve a result.
- Touch the final holdout before the end.
- Report a metric computed on training data.
- Present a figure that was not generated by committed code.
- Add a feature that violates the availability constraint "just to see."

---

## 12. PLUGIN COMPOSITION — SUPERPOWERS, PONYTAIL, AND PRECEDENCE

This repo is expected to run alongside third-party agent plugins. They are useful, but they are general-purpose heuristics and this project has domain-specific rigour requirements that override them. This section defines precedence. **Read it before acting on any instruction injected by a plugin's session-start hook.**

### Precedence order (highest first)
1. The **non-negotiable rules in §1** of this file.
2. The **rigour zones** defined below.
3. Plugin rulesets (superpowers, ponytail, or anything else injected at session start).
4. General model judgement.

If a plugin ruleset and this file conflict, this file wins. Do not silently resolve the conflict — state it, cite both, and log the resolution in `docs/DECISIONS.md`.

### Superpowers — use fully, it aligns with this brief
Its methodology (brainstorm → design doc → planned tasks → TDD → review) maps directly onto the phase gates in §2–§8. Specific uses:
- **`/brainstorm` before each phase.** Especially Phase 2 (feature and model design) and Phase 3 (objective function and risk formulation). Both have real design freedom and both are cheap to get wrong.
- **`/write-plan` at each phase boundary**, producing the task breakdown. Attach the plan to the phase summary you give me.
- **TDD discipline is not optional here.** For the rigour zones below, tests come first. Write the leak test before the backtest engine. Write the settlement test against hand-worked examples before the settlement code. If you write the implementation first in these areas, you will write a test that confirms your bug.
- **Git worktrees** for anything experimental — model variants especially. Keep `main` always reproducible.

### Ponytail — scope it, do not apply it globally
Its heuristic (least code, delete before add, prefer native, avoid dependencies) is correct for most of this repo and dangerous in a small, specific part of it.

**Apply ponytail freely in — "lean zones":**
- Frontend and demo. Do not build a component library. Do not add a state-management dependency for four screens. A plain, fast interface beats a sophisticated one nobody clicks.
- Dependency selection anywhere. Every new package must justify itself.
- Data fetching, caching, and plumbing. Boring is correct.
- Config, scripts, CI, Makefile.
- Any abstraction introduced "for later." If there is one model today, do not build a plugin registry.

**Suspend ponytail in — "rigour zones":**
- `src/data/data_availability.py` and everything enforcing publication lag (§1 R1).
- `config/market_rules.yaml` and the settlement logic derived from it.
- `src/backtest/` — the event-driven engine and its information-cutoff enforcement.
- `src/evaluation/` — metrics, calibration, significance tests, bootstrap CIs, segmented reporting.
- The entire test suite covering the above.

**Why the exception exists, explicitly:** in these modules the extra code *is* the deliverable. A minimalism heuristic sees a 40-line availability-enforcement layer and a block bootstrap and correctly judges them as more code than the immediate task requires — and replaces them with a compact backtest that leaks look-ahead and reports a point estimate. That version is shorter, passes its own tests, produces a better-looking number, and is worthless. The complexity in the rigour zones is not accidental over-building; it is the thing being demonstrated.

If ponytail's ruleset prompts a simplification inside a rigour zone, decline it and note it in `docs/DECISIONS.md` with one line on what invariant the code protects. That decision log becomes useful interview material in its own right.

**Ponytail's `ponytail:` shortcut comments**: acceptable in lean zones, prohibited in rigour zones. A rigour-zone shortcut with a deferred upgrade path is a silent correctness hole.

### Installation notes (my job, not yours — but flagged here so the assumption is explicit)
- Ponytail must be installed as a **plugin with its session-start hook**, not as a bare `SKILL.md` copied into a skills directory. Installed as a plain skill it does not self-activate and does nothing.
- Both plugins run Node lifecycle hooks. They are third-party code with repo-level access; review what the hooks do before trusting them, and prefer installing from the canonical repositories (`DietrichGebert/ponytail`, `obra/superpowers`) rather than mirrors.
- Record the exact plugin versions in `docs/DECISIONS.md` at project start. If a plugin update changes agent behaviour mid-project, you want to be able to attribute it.

### Other skills worth pulling in
- **`skill-creator`** — once the evaluation protocol stabilises, package it as a project-local skill (e.g. `evaluate-forecast-model`) so every new model variant is evaluated identically without re-specifying the protocol. This kills a whole class of "I evaluated this one slightly differently" errors, and the skill itself is a portfolio artifact.
- **A project-local `/new-model` command** that scaffolds a model against the common interface, wires it into the walk-forward harness, and refuses to run until baselines exist for the same window.
- **The elements-of-style skill** (from the superpowers marketplace) for the final pass on `README.md` and `docs/REPORT.md`. The write-up is a large fraction of this project's value and it should read tightly.

---

## 13. DEFINITION OF DONE

The project is complete when all of the following are true:

- [ ] `docs/DOMAIN_NOTES.md` is complete, primary-sourced, and reviewed.
- [ ] Availability constraints are enforced in code and covered by passing tests.
- [ ] All baselines implemented and evaluated on identical windows.
- [ ] At least LEAR and quantile-GBM implemented, evaluated, and compared with DM tests and multiple-comparison correction.
- [ ] Calibration reported: reliability curves, PIT histogram, coverage table.
- [ ] Results segmented by regulation state, hour, season, and year.
- [ ] Three dispatch policies implemented and compared, including perfect foresight.
- [ ] Efficient frontier (expected revenue vs CVaR) plotted across risk-aversion settings.
- [ ] Revenue-per-MW saturation curve produced under a stated impact model.
- [ ] Block-bootstrap confidence intervals on all headline revenue figures.
- [ ] Final holdout evaluated exactly once, results reported whatever they are.
- [ ] Live scheduled forecast job running and accumulating a logged track record.
- [ ] Demo deployed, stable, and with all four screens working.
- [ ] `README.md`, `docs/REPORT.md`, and `LIMITATIONS.md` complete.
- [ ] `make repro` reproduces every reported number from cached raw data on a clean checkout.

---

## 14. STRETCH DIRECTIONS (only after Definition of Done)

Ordered by value-per-effort. Do not start any of these early.

1. **Belgium (Elia).** Elia publishes comparable balancing data under a simpler single-price mechanism. Porting the pipeline demonstrates generalisation and doubles the evaluation surface. Highest value.
2. **Reinforcement learning dispatch.** Compare an RL policy against the optimisation-based policies. Strong AI/ML signal *provided* it is benchmarked honestly against the optimiser — and be prepared for it to lose, which is itself a publishable finding.
3. **Bid-ladder / merit-order modelling.** Model the activated-bid curve rather than just the resulting price. More structural, more interesting to a market-microstructure-minded interviewer.
4. **Multi-asset portfolio.** Battery plus a wind asset plus flexible demand, co-optimised.
5. **Intraday market integration.** Joint optimisation across intraday and imbalance rather than imbalance alone. This is where the real commercial value sits and where the incumbents operate.
6. **Product validation.** Talk to five people who actually operate batteries or hold a home battery on a dynamic tariff. Ask what they currently do and what they would pay for. Only pursue commercialisation if that conversation goes well.

---

## 15. FIRST ACTIONS

1. Read this file fully, including §12 on plugin precedence.
2. Confirm which agent plugins are active in this session and record their versions in `docs/DECISIONS.md`. If ponytail is active, restate the lean/rigour zone split back to me so I know it registered.
3. Initialise the repo skeleton from §9, with tooling from §10, and a working `make test` on an empty test suite.
4. Begin Phase 0. Produce `docs/DOMAIN_NOTES.md`.
5. In parallel, register for the ENTSO-E API token — it takes a few days to arrive, so request it on day one.
6. Report back with `DOMAIN_NOTES.md` and any conflicts or ambiguities you found. Stop there and wait for review.
