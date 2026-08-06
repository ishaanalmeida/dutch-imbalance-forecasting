# Data sources and licence position

R7: **no raw third-party market data is committed to this repo.** `data/` is
gitignored; fetch scripts ship instead. Derived analytics and model outputs are
publishable; raw licensed series are not.

| Source | What we take | Access | Licence position |
|---|---|---|---|
| **ENTSO-E Transparency Platform** | Imbalance prices & volumes, day-ahead prices, load & generation forecasts, actual generation, cross-border flows | Free REST API. Register, then email `transparency@entsoe.eu`, subject "RESTful API access", with your account email. Token in a few working days. `entsoe-py`. | ⚠ **Redistribution restricted for some items.** Parts of the platform's data are derived from exchange feeds (**EPEX SPOT**) whose terms restrict redistribution. **Do not publish raw series.** Model outputs, metrics and figures are fine. |
| **TenneT** (Dutch TSO) | NL balance delta (near-real-time), settled imbalance prices, regulation state, bid ladder | **Registered-access API** via `developer.tennet.eu` (free registration: name, email, privacy statement + fair-use policy). Higher resolution and more NL-specific than ENTSO-E. | To verify once registered. Public web pages and `api.tennet.eu` reject anonymous automated access — see "TenneT access" below. |
| **Open-Meteo** | **Historical *forecast* archive** (forecast as issued, not reanalysis) + live forecasts. Wind speed at hub height, irradiance, temperature. | Free, no key for non-commercial use. | Verify current terms before any commercial use. |
| **KNMI** | Dutch station observations. Optional. | Free with registration. | Verify. |

## TenneT access

**Corrected 2026-08-06.** An earlier pass characterised TenneT as returning
HTTP 403 to automated fetching with no route identified, implying the source
might be unusable. That was incomplete: 403 on the *public* surface is real,
but a separate, registered-access API portal exists and is reachable. The
probe results below are recorded verbatim as evidence.

| URL | Result |
|---|---|
| `www.tennet.eu/...` (any page) | 403 |
| `api.tennet.eu` and `/publications/v1/balancedelta` | 403, with or without JSON `Accept` header |
| `www.tennet.eu/api/publications` | 403 |
| **`https://developer.tennet.eu`** | **200 — a real API developer portal** |
| `https://developer.tennet.eu/specs/<api-name>` | 302 → redirects to login; specs are behind registration |
| `https://developer.tennet.eu/register/` | 200 — form asks only Name, Email, and two checkboxes (privacy statement, fair use policy) |

**Conclusion: public web pages and `api.tennet.eu` reject anonymous automated
access (403), but `developer.tennet.eu` hosts a registered-access API portal.**
Registration is free and requires only a name, an email address, and
acceptance of the privacy statement and the fair use policy — no commercial
agreement, no paid tier observed at this stage.

The portal lists ten APIs. Four matter to this project:

- **Balance Delta High Res** — the intra-ISP signal whose publication lag is
  currently unmeasured (see `LIMITATIONS.md` and `docs/DOMAIN_NOTES.md` Q7).
- **Settlement prices** — imbalance price per settlement period.
- **Settled imbalance volumes**.
- **Merit Order List aFRR & mFRRsa** — the bid ladder.

**What has NOT been seen (R3):** the endpoint paths, auth header/token
mechanism, query parameters, and response schemas for any of these four APIs
— the detailed specs at `developer.tennet.eu/specs/<api-name>` are behind the
login. Nothing about them is encoded anywhere in this repo; `src/data/` and
`scripts/measure_balance_delta_lag.py` name exactly what is missing rather
than guessing at it.

**Registration was not attempted here.** It requires the repo owner's
identity and agreement to legal terms, and is the owner's action to take, not
an automated one. **Read the fair-use policy before any scheduled polling
begins** — nothing about its rate limits or acceptable-use terms has been
verified yet.

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
