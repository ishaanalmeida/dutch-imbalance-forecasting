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
  from TenneT's *Imbalance Pricing System* **v6.1 (21 Oct 2024)**, including dual
  pricing in state 2 and the reverse-pricing mid-price correction, lives in
  [`config/market_rules.yaml`](config/market_rules.yaml) as executable config.
  [`src/market.py`](src/market.py) resolves it; 23 tests in
  [`tests/test_settlement.py`](tests/test_settlement.py) check it against
  hand-worked examples and a sign-convention property test.
- **Publication lags catalogued** per series, each confidence-tagged, with the
  one unresolved lag left `null` rather than guessed — see Limitations.

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
