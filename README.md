# Dutch Imbalance Market Forecasting & Battery Dispatch Lab

Probabilistic forecasting of the Dutch (TenneT) imbalance price and regulation
state at 15-minute resolution, and battery dispatch optimised against the full
predictive distribution — backtested under real settlement rules, real
publication latency, and an explicit market-impact model.

## Status: Phase 1 complete; Phase 2 has a first measured checkpoint

| Phase | State |
|---|---|
| 0 — Domain verification | ✅ [`docs/DOMAIN_NOTES.md`](docs/DOMAIN_NOTES.md), [`config/market_rules.yaml`](config/market_rules.yaml) |
| 1 — Data layer | ✅ built, tested, and **connected to live data** (ENTSO-E), gap-free 2024-10-18 to 2026-04-30 |
| 2 — Forecasting | 🟡 baselines + LEAR wired to real walk-forward folds; GBM, DM tests, calibration still open |
| 3 — Dispatch optimisation | ⬜ |
| 4 — Backtest | ⬜ |
| 5 — Demo | ⬜ |
| 6 — Write-up | ⬜ |

**Headline result (provisional checkpoint, not a validated finding):** on 18
walk-forward folds (2024-11 through 2026-04, holdout untouched), an
L1-regularised quantile-regression model (LEAR) scores **23.2 mean pinball
loss (EUR/MWh)** predicting the imbalance short price, against 25.5 for the
climatological baseline (CLAUDE.md's own "one to beat") and 42.5 for
persistence. Produced by [`scripts/run_walkforward_evaluation.py`](scripts/run_walkforward_evaluation.py);
full numbers and method in [`docs/DECISIONS.md`](docs/DECISIONS.md) ADR-027.
**Not yet done:** significance testing (Diebold-Mariano) against the
baselines, calibration/coverage, segmented reporting, or a check across the
2025-10-01 day-ahead MTU change — so "LEAR wins" is not yet a claim this
project is prepared to stand behind, only a number this project is prepared
to show its work for. This file will carry no number that was not produced by
code in this repo.

## What exists today

**264 tests, `ruff` and `mypy --strict` clean, verified from a clean clone.**

- **The no-look-ahead gate** ([`src/data/data_availability.py`](src/data/data_availability.py),
  [`tests/test_no_lookahead.py`](tests/test_no_lookahead.py)). `available_at(field, isp)`
  answers when a datum first became retrievable, and **refuses rather than guessing**
  when a lag is unresolved. Mutation-tested: 13 deliberate breakages, including
  `<`→`<=` and measuring lag from period start instead of period end.
- **Settlement rules encoded and tested.** The regulation-state → price table
  from TenneT's *Imbalance Pricing System* **v6.1 (21 Oct 2024)**, including dual
  pricing in state 2 and the reverse-pricing mid-price correction, lives in
  [`config/market_rules.yaml`](config/market_rules.yaml) as executable config.
  [`src/market.py`](src/market.py) resolves it rather than restating it.
- **DST-correct time base** — the 23-hour and 25-hour Amsterdam days are asserted
  explicitly (92 and 100 ISPs), because a naive 96-per-day assumption fails
  silently twice a year.
- **Fetchers** for ENTSO-E and Open-Meteo, writing through a raw-response store
  and a month-partitioned Parquet cache. The weather fetcher enforces an
  **allowlist** on the endpoint so ERA5 reanalysis can never be used as a feature.
- **Publication lags catalogued** per series, each confidence-tagged, with the
  one unresolved lag left `null` rather than guessed — see Limitations.

- **An append-only vintage log** ([`src/data/vintage.py`](src/data/vintage.py)) and a
  scheduled job that records what the forecast said, when it said it. Nothing is
  ever overwritten, so `latest_as_of()` reconstructs exactly what was visible
  strictly before any instant — and the difference between two vintages is the
  forecast-error proxy. **This is the only artefact that cannot be back-filled**,
  which is why it runs before a model exists.
- **CI** enforcing lint, `mypy --strict`, tests, and three repository-level
  invariants: nothing under `data/` may become committable (R7), `.env` is never
  tracked (R8), and every source file on disk is tracked by git.

**Not yet built:** feature builder, models, backtest, optimisation, demo (Phases 2–5).

## Try it

```bash
uv run python -m src.cli status         # what is built, what is blocked, why
uv run python -m src.cli availability   # the no-look-ahead gate, for the current ISP
uv run python -m src.cli settle         # the settlement table on a worked example
uv run python -m src.cli log-vintage    # record the current forecast
uv run python -m src.cli track-record   # what has accumulated so far
uv run python -m src.cli reparse        # rebuild the cache from raw, no refetch
```

`availability` is the one worth looking at: it prints, per field, when the datum
became retrievable and whether it is usable at the decision instant — including
why `balance_delta` is *refused* rather than guessed.

## Limitations

Read [`LIMITATIONS.md`](LIMITATIONS.md) before anything else. The three that
currently matter most:

1. **The regulation state changed meaning on 2026-02-03** — determined from the
   12-second balance delta instead of the 1-minute series (75 samples per ISP,
   not 15). Same rule wording, but monotonicity is less likely over more
   samples, so state 2 (the only dual-priced state) should get more frequent
   with no change in the physical system. Only ~6 months of post-change data.
2. **The balance-delta lag is measured (134 s) but over a narrow window** —
   2.18 hours of one weekday afternoon. Not overnight, weekends, or scarcity,
   which is when a battery earns most. TenneT describe the delay as
   *configurable*, so re-measure across 24 h before trusting a revenue figure.
3. PICASSO (18 Oct 2024) left price formation unchanged — confirmed by v6.1 —
   but shifted the price *distribution*, plausibly halving volatility.

## Reproduce

```bash
uv sync
uv run pytest        # or: make test
uv run ruff check .
uv run mypy
```

Requires [`uv`](https://docs.astral.sh/uv/); it fetches the pinned Python itself.
Every `make` target is a one-line wrapper over the equivalent `uv run` command,
so `make` is optional.

Copy `.env.example` to `.env` and add an ENTSO-E API token before Phase 1.

---

*Research tool. Backtested results under stated assumptions. Not investment
advice and not a guarantee of returns.*
