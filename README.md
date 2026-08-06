# Dutch Imbalance Market Forecasting & Battery Dispatch Lab

Probabilistic forecasting of the Dutch (TenneT) imbalance price and regulation
state at 15-minute resolution, and battery dispatch optimised against the full
predictive distribution — backtested under real settlement rules, real
publication latency, and an explicit market-impact model.

## Status: Phase 1 built, no data fetched yet

| Phase | State |
|---|---|
| 0 — Domain verification | ✅ [`docs/DOMAIN_NOTES.md`](docs/DOMAIN_NOTES.md), [`config/market_rules.yaml`](config/market_rules.yaml) |
| 1 — Data layer | ✅ built and tested — ⚠️ **no data fetched**: awaiting ENTSO-E token and TenneT registration |
| 2 — Forecasting | ⬜ blocked on data |
| 3 — Dispatch optimisation | ⬜ |
| 4 — Backtest | ⬜ |
| 5 — Demo | ⬜ |
| 6 — Write-up | ⬜ |

**Headline result:** `TODO: not yet measured.` No data has been fetched, no model
trained, no backtest run. This file will carry no number that was not produced by
code in this repo.

## What exists today

**117 tests, `ruff` and `mypy --strict` clean, verified from a clean clone.**

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

**Not yet built:** feature builder, models, backtest, optimisation, demo (Phases 2–5).
No CI workflow yet.

## Limitations

Read [`LIMITATIONS.md`](LIMITATIONS.md) before anything else. The three that
currently matter most:

1. **The regulation state changed meaning on 2026-02-03** — determined from the
   12-second balance delta instead of the 1-minute series (75 samples per ISP,
   not 15). Same rule wording, but monotonicity is less likely over more
   samples, so state 2 (the only dual-priced state) should get more frequent
   with no change in the physical system. Only ~6 months of post-change data.
2. **The balance-delta publication lag is unknown.** The widely-cited
   3 → 5 → 2 minute timeline could not be substantiated — TenneT's own pages
   document *cadence* changes, not *delay* changes. It is `null` in config and
   Phase 1 measures it from data; until then no feature may use balance delta.
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
