# Dutch Imbalance Market Forecasting & Battery Dispatch Lab

Probabilistic forecasting of the Dutch (TenneT) imbalance price and regulation
state at 15-minute resolution, and battery dispatch optimised against the full
predictive distribution — backtested under real settlement rules, real
publication latency, and an explicit market-impact model.

## Status: Phase 0 complete, awaiting review

| Phase | State |
|---|---|
| 0 — Domain verification | ✅ [`docs/DOMAIN_NOTES.md`](docs/DOMAIN_NOTES.md), [`config/market_rules.yaml`](config/market_rules.yaml) — **awaiting review** |
| 1 — Data layer | ⬜ blocked on ENTSO-E API token |
| 2 — Forecasting | ⬜ gated on Phase 0 review |
| 3 — Dispatch optimisation | ⬜ |
| 4 — Backtest | ⬜ |
| 5 — Demo | ⬜ |
| 6 — Write-up | ⬜ |

**Headline result:** `TODO: not yet measured.` No data has been fetched, no model
trained, no backtest run. This file will carry no number that was not produced by
code in this repo.

## What exists today

- **Settlement rules encoded and tested.** The regulation-state → price table
  from TenneT's *Imbalance Pricing System* v6.0, including dual pricing in state
  2 and the reverse-pricing mid-price correction, lives in
  [`config/market_rules.yaml`](config/market_rules.yaml) as executable config.
  [`src/market.py`](src/market.py) resolves it; 21 tests in
  [`tests/test_settlement.py`](tests/test_settlement.py) check it against
  hand-worked examples and a sign-convention property test.
- **Publication lags catalogued** per series, including the one that is *not
  constant over history* — see Limitations.

## Limitations

Read [`LIMITATIONS.md`](LIMITATIONS.md) before anything else. The two that
currently matter most:

1. TenneT's pricing methodology document predates the Netherlands joining
   PICASSO (18 Oct 2024) and no newer version was found. If price formation
   changed then, usable history is ~22 months, not 3+ years.
2. TenneT changed the balance-delta publication delay at least three times in
   2024–25 — deliberately, to alter passive-balancing profitability. A backtest
   using one constant lag measures a strategy that never existed.

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
