# Data sources and licence position

R7: **no raw third-party market data is committed to this repo.** `data/` is
gitignored; fetch scripts ship instead. Derived analytics and model outputs are
publishable; raw licensed series are not.

| Source | What we take | Access | Licence position |
|---|---|---|---|
| **ENTSO-E Transparency Platform** | Imbalance prices & volumes, day-ahead prices, load & generation forecasts, actual generation, cross-border flows | Free REST API. Register, then email `transparency@entsoe.eu`, subject "RESTful API access", with your account email. Token in a few working days. `entsoe-py`. | ⚠ **Redistribution restricted for some items.** Parts of the platform's data are derived from exchange feeds (**EPEX SPOT**) whose terms restrict redistribution. **Do not publish raw series.** Model outputs, metrics and figures are fine. |
| **TenneT** (Dutch TSO) | NL balance delta (near-real-time), settled imbalance prices, regulation state, bid ladder | Public data export. Higher resolution and more NL-specific than ENTSO-E. | To verify. ⚠ `tennet.eu` returned **HTTP 403 to automated fetching** during Phase 0 — the fetcher may need a browser-like user agent, or manual retrieval. Resolve in Phase 1. |
| **Open-Meteo** | **Historical *forecast* archive** (forecast as issued, not reanalysis) + live forecasts. Wind speed at hub height, irradiance, temperature. | Free, no key for non-commercial use. | Verify current terms before any commercial use. |
| **KNMI** | Dutch station observations. Optional. | Free with registration. | Verify. |

## The weather trap — read before writing the weather fetcher

Use Open-Meteo's **historical forecast** endpoint, never the ERA5 reanalysis
endpoint. Reanalysis is what the weather *was*; a model fed reanalysis will look
brilliant and be worthless, and it will not fail any test that does not
specifically look for it. This is the most likely way this project silently
fails (CLAUDE.md §3).

The Phase 1 gate test must assert that no weather feature originates from a
reanalysis endpoint.

## Vintages

[REG543] Art. 14(2)(d) mandates both a D-1 17:00 wind/solar forecast and a
D 07:00 intraday update. **Capture both vintages from the first fetch.** Their
difference is the forecast-error proxy the brief requires (§4), it does not leak,
and it is **irrecoverable if not captured live** — ENTSO-E's API serves the
current vintage, not the historical one. See `docs/DOMAIN_NOTES.md` Q8.
