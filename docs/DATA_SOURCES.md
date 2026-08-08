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
  measured at 134 s on 2026-08-07 (630 samples); see `LIMITATIONS.md` for the
  coverage caveat and `docs/DECISIONS.md` ADR-020.
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

### How to get a TenneT API key (verified 2026-08-07)

1. Register at <https://developer.tennet.eu/register/> — name, email, and
   acceptance of the privacy statement and fair use policy.
2. Log in at <https://developer.tennet.eu/login/>. Two routes are offered: a
   **MyTenneT account**, or **email login**.
3. **The "API Keys" tab appears only once logged in.** It is not in the public
   navigation, which is why it is invisible before signing in. From TenneT's
   own FAQ: *"You can log into the portal, and on the tab API Keys you can find
   and revoke all your keys and also request new ones."*
4. Put the key in `.env` as `TENNET_API_KEY`. Never in code or a commit (R8).

### Auth and error codes (from TenneT's FAQ and verified against the live API)

| Signal | Meaning |
|---|---|
| Header name | **`apikey`** — confirmed from the spec page's `Authorize` dialog (scheme `apikey`, in `header`). **Not** Azure's default `Ocp-Apim-Subscription-Key`, despite Azure API Management fronting the service. |
| `403` | `apikey` header missing entirely. Reproduced live without the header. |
| `401` | Header present, key invalid. |
| `429` | Rate limit exceeded. |
| TLS | **TLS 1.2 was disabled on 11/11**; clients must support **TLS 1.3**. Verified our stack negotiates it (OpenSSL 3.5.7). |

Assuming the Azure default header would have produced a `401` that reads like a
bad key rather than a bad header name — an hour of debugging the wrong thing.

### Rate limits and how to get history

The FAQ says there are limits **per second, per hour and per day**, and that
they differ per API — check each spec. For `Balance Delta High Res`:

- `/balance-delta-high-res/latest`: 1 req/sec, **10 req/min**; refresh every
  12 s; TenneT recommends polling 1 s after each refresh (`:01 :13 :25 :37 :49`).
- `/balance-delta-high-res` (by timeframe): **8 req/DAY**, max 4-hour window.

**Do not try to backfill history through the API.** TenneT is explicit:
*"The API is not the proper channel for this, to consume larger amounts of
historic data please use the download function on the website"* — up to 100 MB
per download from the transparency download page. Requests for higher quotas
are declined for this reason.

Support: `apisupport@tennet.eu`.

### APIs available on the portal

Ten in total. Beyond `balance-delta-high-res`, `settlement-prices` and
`settled-imbalance-volumes`, note **`merit-order-list`** and
**`merit-order-list-bid-prices-incident-reserve`** — the bid ladder, which
would allow reconstructing `p_up` / `p_down` / `p_mid` from first principles
rather than consuming published prices. That is the Phase-0 stretch direction
on market microstructure, now known to be reachable.

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
