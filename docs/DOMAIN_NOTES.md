# Phase 0 — Domain Notes: Dutch (TenneT) Imbalance Settlement

**Status: awaiting review (CLAUDE.md §2 gate). Phase 2 modelling must not start until this is signed off.**

Compiled 2026-08-04. Machine-readable counterpart: [`config/market_rules.yaml`](../config/market_rules.yaml),
which is the single source of truth for all downstream modules.

## How to read this document

Every answer carries a confidence tag. This is not decoration — several answers
below are **not** primary-sourced, and the brief (§2) requires that be said out
loud rather than smoothed over.

| Tag | Meaning |
|---|---|
| **PRIMARY** | Taken from a TSO, regulator, or EU-law document I read directly. |
| **SECONDARY** | Trade press or vendor blog only. Must be upgraded before any result depends on it. |
| **UNRESOLVED** | I could not establish this. Named explicitly so it cannot be forgotten. |

### Sources

| Ref | Document | Type |
|---|---|---|
| **[IPS61]** | TenneT TSO B.V., *Imbalance Pricing System: how are the (directions of) payment determined?*, **version 6.1, 21 October 2024** — changelog: *"Clarification on PICASSO impact"*. [`docs/refs/`](refs/TenneT_Imbalance_Pricing_System_v6.1_2024-10-21.pdf) | Primary (TSO) |
| [IPS6] | Same document, **version 6.0, 30 March 2022**. Retained only for the v6.0→v6.1 diff. [PDF](https://tennet-drupal.s3.eu-central-1.amazonaws.com/default/2022-06/Imbalance_pricing_system_0.pdf) | Primary (TSO) |
| **[STCRT]** | Staatscourant 2024 nr. 29868, cited by [IPS61] fn.14 as the legal basis for the PICASSO price-determination clarification. [Link](https://zoek.officielebekendmakingen.nl/stcrt-2024-29868.html) | Primary (NL gazette) |
| **[TNT-12S]** | TenneT news, *"Balance delta now published every 12 seconds"*, 25 Nov 2025. | Primary (TSO) |
| **[TNT-MU]** | TenneT, *"Market update at a glance"*, 28 Oct 2025; *"Q1 Balancing Market Update"*, 20 Feb 2026. | Primary (TSO) |
| **[REG543]** | Commission Regulation (EU) No 543/2013 on submission and publication of data in electricity markets. [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2013/543/oj/eng) | Primary (EU law) |
| **[EBGL]** | Commission Regulation (EU) 2017/2195 establishing a guideline on electricity balancing. | Primary (EU law) |
| **[ACM2022]** | ACM, *Goedkeuring dubbele prijsstelling voor onbalansverrekening*, ref. ACM/UIT/570957, case ACM/21/052864, decision 2 March 2022. [PDF](https://www.acm.nl/sites/default/files/documents/goedkeuring-dubbele-prijsstelling-voor-onbalansverrekening.pdf) | Primary (regulator) |
| **[NEMO2025]** | Market Coupling Steering Committee, press release on 15-minute MTU go-live in SDAC, 12 Sept 2025. [PDF](https://www.nemo-committee.eu/assets/files/market-coupling-steering-committee-confirms-go-live-of-15-minute-mtu-in-sdac-on-trading-day-30-september-2025-for-delivery-day-1-october-2025.pdf) | Primary (NEMO/TSO body) |
| *[sec-timera]* | Timera Energy, "Netherlands joins PICASSO aFRR platform…" | Secondary |
| *[sec-dexter]* | Dexter Energy, "Go-live of PICASSO in the Netherlands" / "Market impact in short-term power trading" | Secondary |
| *[sec-comcam]* | COMCAM, "TenneT shortens imbalance market delay and increases price resolution" | Secondary |
| *[sec-ess]* | ess-news.com, Dutch grid-connection and battery capacity reporting, 2025 | Secondary |

> **Update, post-review.** The original draft of this document flagged as its
> largest open risk that [IPS6] (March 2022) predated PICASSO. **Resolved:**
> **v6.1, dated 21 October 2024**, was retrieved manually and is now the
> governing source. Its sole substantive change is the PICASSO clarification,
> and it *confirms* that price formation did not change. See [Q5](#q5).
>
> A second correction from the same review: an earlier version of Q7 below
> asserted a 3 → 5 → 2 minute balance-delta publication-**delay** timeline from
> trade press. Manual review of TenneT's own pages found only **cadence**
> changes. Those are different mechanisms and the claim has been withdrawn.
> See [Q7](#q7).

---

## Settlement mechanics

### Q1 — Imbalance Settlement Period length — **PRIMARY**

**15 minutes.** [IPS6] §2, Table 1, defining *Onbalansverrekeningsperiode (ISP)*:

> "The time unit over which the imbalance of BRPs is calculated (EB GL).
> Explanation: In the past, ISP was also known as Programme Time Unit (PTU).
> **The ISP is fixed at 15 minutes.**"

**When did it last change?** It did not. The Netherlands has settled on a
15-minute period since market opening; the term changed (PTU → ISP) when [EBGL]
harmonised terminology, but the duration did not. This is worth stating because
the EU-wide 15-minute ISP harmonisation obligation caused a real change in
several other member states (Germany moved from 15 min already, others from
30/60 min) — **but not in NL**. Do not import a "the ISP changed" structural
break from the literature into this project.

### Q2 — How the imbalance price is formed, and the regulation states — **PRIMARY**

Three component prices are determined per ISP ([IPS6] §4.2, §2):

| Symbol | Definition | Note |
|---|---|---|
| `p_up` | Price of the **highest-priced activated** aFRR upward bid in the ISP, or the upward mFRRda price if that is higher. | Marginal (uniform) pricing. |
| `p_down` | Price of the **lowest-priced activated** aFRR downward bid in the ISP, or the downward mFRRda price if that is lower. | **May be negative.** |
| `p_mid` | Average of the **lowest upward bid price** and the **highest downward bid price** on the merit order. | Based on **offered** bids, not activated ones. |

[IPS6] §4.2:

> "The price for upward regulation is equal to the price of the highest price
> activated aFRR bid in upward direction in that ISP, or, if it is higher, the
> price for upward incident reserve in the ISP."

If no price for a direction exists, that direction settles at **the previous
ISP's** regulation price ([IPS6] §4.2, third bullet). This is a lagged
dependency and must be reproduced in the backtest, not ignored.

**The four regulation states** ([IPS6] §4.3) — *these are the exact codes*:

| Code | Name | Definition |
|---|---|---|
| `0` | No regulation | TenneT regulates neither up nor down in the ISP. Also occurs when BRP deviations net out via cross-border imbalance netting (IGCC), and when Load Frequency Control is inactive (malfunction/blackout). |
| `+1` | Upward only | TenneT only regulates upward. |
| `-1` | Downward only | TenneT only regulates downward. |
| `2` | Both directions | Both directions activated **and** the intra-ISP balance-delta series both rises and falls. |

The tie-break when both directions are activated is decided by the **shape of
the intra-ISP balance-delta series**, not by volumes ([IPS6] §4.3):

> "If the series of balance deltas within the ISP continuously increases or is
> constant, then regulation state +1 applies; […] continuously decreases or is
> constant, then regulation state -1 applies; […] both increases and decreases,
> then regulation state 2 applies."

**Modelling consequence, and it is a large one.** The regulation state is a
function of the *path* of the balance delta through the ISP, not of its
endpoint. A classifier for T1 is therefore predicting a path property. This also
means state 2 is mechanically more likely in ISPs with high intra-period
volatility — which is exactly when prices are extreme. State 2 and price
extremity are not independent.

### Q3 — Dual pricing: which state, and how the two prices are set — **PRIMARY**

**Dual pricing applies in regulation state 2 only.** Approved by ACM under
[EBGL] Art. 11 of the ISH methodology; the standing approval is [ACM2022]
(decision of 2 March 2022, following TenneT's 15 July 2021 continuation request).

Two settlement prices exist per ISP ([IPS6] §5):

- `price_long` — settles a **BRP surplus** (injected more / withdrew less than schedule)
- `price_short` — settles a **BRP shortage** (injected less / withdrew more than schedule)

From [IPS6] §5, Table 2, reduced to closed form:

| State | `price_long` (surplus) | `price_short` (shortage) |
|---|---|---|
| `0` | `p_mid` | `p_mid` |
| `+1` | `p_up` | `p_up` |
| `-1` | `p_down` | `p_down` |
| `2` | `min(p_down, p_mid)` | `max(p_up, p_mid)` |

The state-2 `min`/`max` **is** the reverse-pricing correction. Table 2 states it
as two conditionals — short is `p_up` if `p_up ≥ p_mid` else `p_mid`; long is
`p_down` if `p_down ≤ p_mid` else `p_mid` — which collapse exactly to `max` and
`min`. Encoded in `config/market_rules.yaml` under `pricing.rules`.

#### Sign convention — **get this right or every revenue number is wrong**

[IPS6] §4.1, viewed **from the grid**:

- Upward bids inject into the grid → **positive**. Downward bids withdraw → **negative**.
- BRP **surplus** → **positive**. BRP **shortage** → **negative**.
- Direction of payment depends on *both* the imbalance position *and* the sign of the price.

With `e_surplus` and `e_shortage` as **positive MWh magnitudes**, the whole of
Table 2's direction-of-payment column reduces to one expression:

```
cash_to_brp = price_long * e_surplus  -  price_short * e_shortage
```

Negative result = the BRP pays TenneT. This is verified against all six
direction-of-payment rows in `tests/test_settlement.py`.

#### Worked example 1 — normal dual pricing (state 2)

Given `p_up = 120`, `p_down = −15`, `p_mid = 30` €/MWh:

- `price_short = max(120, 30) = 120` €/MWh
- `price_long  = min(−15, 30) = −15` €/MWh

A battery **discharging** 10 MWh above schedule is a BRP surplus:
`cash = −15 × 10 = −150 €` → **it pays 150 €** for injecting.
A battery **charging** 10 MWh above schedule is a BRP shortage:
`cash = −120 × 10 = −1200 €` → **it pays 1200 €**.

**Both directions lose money.** This is the entire economic point of dual
pricing and the reason a probabilistic forecast beats a point forecast here: a
median-price forecast that misses a state-2 ISP books revenue where reality
books a penalty. A dispatch policy that ignores `P(state = 2)` will
systematically overstate revenue.

#### Worked example 2 — reverse pricing (state 2)

Given `p_up = 25`, `p_down = 35`, `p_mid = 30` €/MWh — the activated upward
price sits *below* the mid-price and the downward *above* it:

- `price_short = max(25, 30) = 30`
- `price_long  = min(35, 30) = 30`

Both legs are corrected to the mid-price and **the dual price collapses to a
single price**. Naive dual-pricing code that just assigns `p_up` to shorts and
`p_down` to longs would here pay longs 35 and charge shorts 25 — paying out more
than it collects, which is precisely the arbitrage the mid-price rule exists to
close. Covered by `test_state_2_reverse_pricing_collapses_both_legs_to_mid`.

**Invariant, tested:** `price_short ≥ price_long` in every state, for all
component prices. A BRP can never be paid more for being long than it is charged
for being short in the same ISP.

### Q4 — Incentive component — **PRIMARY**

**It does not exist. It was abolished on 31 July 2020 and is zero thereafter.**

[IPS6] revision history, version 5.0, dated 31/07/2020: *"Abolishment of
incentive component"*. It had previously been moved out of this document into
the Implementation Guide (v3.4, 01/06/2010), and no incentive/*prikkel* term
appears anywhere in v6.0's price determination (§4.2, §5).

**This is a trap for anyone reading older literature.** Many papers and blog
posts on Dutch imbalance pricing describe an incentive component because they
predate mid-2020. Set to `0.0` in `config/market_rules.yaml` with the abolition
date recorded as a structural break; do not re-introduce it.

### Q5 — aFRR and mFRR mapping into the price; merit order — **PRIMARY, with a live caveat** {#q5}

**Marginal (uniform) pricing** ([IPS6] §4.2): one price per direction per ISP,
paid to all activated volume regardless of bid price. TenneT maintains two merit
orders — upward bids sorted low→high, downward bids sorted high→low. The
price-setting bid is the marginal (last, most extreme) activated one.

- **aFRR** (*regelvermogen*): bid-based; the merit order is built from these bids.
- **mFRRda** (*noodvermogen*, incident reserve): **no bids are submitted**, only
  availability is contracted. It enters pricing as a bound — it sets the price
  if it is *higher* than `p_up` (upward) or *lower* than `p_down` (downward).
- aFRR and mFRRda receive the **same** price per direction per ISP.
- Volumes avoided through IGCC imbalance netting are settled TSO-to-TSO at a
  price at least as favourable as the balancing energy price; this contributes
  to TenneT's financial residue but **does not** change the BRP's imbalance
  price ([IPS6] §6.1).

> ### ✅ PICASSO — RESOLVED, and the answer is the favourable one
>
> [IPS61] **v6.1 (21 October 2024)** was published three days after the
> Netherlands joined PICASSO, for the sole purpose of clarifying its impact.
> A v6.0→v6.1 diff shows the change is *additive*: §4.2's three original
> pricing bullets are untouched and Table 2 is unchanged. What v6.1 adds:
>
> §4.2, new fourth bullet:
> > "The CBMP determined by the PICASSO platform is **not explicitly taken into
> > account in the price determination**." (fn.14 → [STCRT])
>
> §4.3, new closing sentence:
> > "There is **no direct impact** of participation in IGCC or PICASSO on the
> > **regulation state**."
>
> Plus a new §3.4 explaining that PICASSO relocates the residual power imbalance
> "to the area with the most economical bids on the local merit order", and is
> preferred over IGCC netting because it can take prices into account.
>
> **Consequence: the rule table is valid across the whole sample.** Price
> formation and state determination did not change on 2024-10-18, and the
> pre-PICASSO history remains usable. This removes the risk that usable history
> collapses to ~22 months.
>
> **But the distribution shifted, and §3.4 explains the mechanism.** If the
> residual imbalance is relocated to whichever area has the cheapest bids, the
> domestic merit order is walked less far in either direction — so the marginal
> activated bid is less extreme, which is precisely the ~50% volatility drop the
> secondary sources report. Rules unchanged, distribution materially changed.
> 2024-10-18 stays registered as a structural break and results must be
> segmented across it.

### Q6 — Price caps, floors, scarcity rules — **SECONDARY** ⚠

There is **no cap on the Dutch imbalance price as such**. Because the imbalance
price is set by the marginal *activated bid*, it is bounded indirectly by the
**balancing-energy bid price limits** under the [EBGL] harmonised pricing
methodology:

| Limit | Value | Period |
|---|---|---|
| Transitional max / min | ±15 000 €/MWh | until July 2026 |
| Technical max / min | ±99 999 €/MWh | from July 2026 |

A TSO proposal to lower the transitional limit to ±10 000 €/MWh was reported.
ACER approved an amendment to the day-ahead/intraday harmonised clearing price
methodology on 4 January 2026.

**I did not read the ACER decision text itself** — only search summaries. Given
that "today" is August 2026, the ±99 999 technical limits should now be in
force, and whether the ±10 000 proposal was adopted is **UNRESOLVED**. Tagged
`confidence: secondary` in the config. This matters less than it looks (realised
prices sit orders of magnitude below these bounds) but it belongs in the
outlier-handling policy, not in a modeller's guess.

---

## Data timing — this determines what the model may see

### Q7 — Publication cadence and lag per series — **MIXED**

Lag is measured from the **end** of the period described to first retrievability.

| Series | Cadence | Lag | Confidence | Source |
|---|---|---|---|---|
| Balance delta | **12 s** (from 2025-11-25; 1 min before) | **UNRESOLVED — must be measured, not assumed** | cadence PRIMARY, lag UNRESOLVED ⚠ | [TNT-12S], [IPS61] fn.16 |
| Imbalance price — real-time estimate | ~1 min | ~2 min | SECONDARY | *[sec-dexter]* |
| **Imbalance price — settled** | 15 min | **D+1 from 10:00 CET** | **PRIMARY** | [IPS6] §3.2 |
| Regulation state (settled) | 15 min | D+1 (with the settled price) | PRIMARY | [IPS6] §3.2 |
| Day-ahead price | 15 min (60 min pre-2025-10-01) | **published D-1 ~13:00**, i.e. *before* delivery | PRIMARY | [REG543] Art. 12(2)(d) |
| Load forecast (day-ahead) | 15/60 min | ≥2 h before DA gate closure | PRIMARY | [REG543] Art. 6(2)(b) |
| Wind/solar forecast (day-ahead) | 15/60 min | by 17:00 on D-1 | PRIMARY | [REG543] Art. 14(2)(d) |
| Wind/solar forecast (intraday) | — | ≥1 update at 07:00 on D | PRIMARY | [REG543] Art. 14(2)(d) |
| Actual generation | 15/60 min | ≤1 h after the period | PRIMARY | [REG543] Art. 16(2)(b) |
| Cross-border physical flows | 15/60 min | ≤1 h after the period | PRIMARY | [REG543] Art. 12(2)(f) |
| Activated balancing volumes | 15 min | **not established** | UNRESOLVED ⚠ | — |

[IPS6] §3.2 on settlement, verbatim:

> "Settlement: After the delivery day (D+1), the process of financial settlement
> starts at 10.00 a.m. In this phase, the settlement prices are determined and
> published and the imbalance per BRP is then determined and invoiced."

**The critical structural point.** There are effectively *two* imbalance price
series with radically different timing:

1. A **near-real-time estimate**, available intra-ISP at ~2 min lag. Legitimate
   as a **feature**. Revised.
2. The **settled price**, available at D+1 ≥ 10:00. This is the **target**. Never
   a feature.

Conflating them is the single easiest way to leak look-ahead into this project,
and it would look like a spectacular model. `data_availability.py` must treat
them as two distinct fields with different lags, not one field.

#### ⚠ Cadence is not delay — a claim withdrawn {#q7}

**An earlier draft of this section asserted a 3 → 5 → 2 minute balance-delta
publication *delay* timeline, sourced from trade press. That claim is
withdrawn.** Manual review of TenneT's own pages found no support for it.

The distinction matters and TenneT's own text observes it:

- **Cadence** — how often a new value is published (1/min → 5/min → every 12 s).
- **Delay** — how long after the instant it describes a value becomes visible.

A frequency increase is not a lag reduction. Treating one as the other
misattributes the mechanism, and would mis-specify the exact quantity the
backtest is most sensitive to.

**What TenneT's pages actually confirm:**

| Date | Change | Kind |
|---|---|---|
| 28 Oct 2025 [TNT-MU] | "Publishing balance delta data five times per minute (instead of once per minute)." Immediately followed by: "Option to **additionally delay** price information on the balance delta. **Currently, there are no concrete plans for this.**" | Cadence (delay explicitly *not* implemented) |
| 25 Nov 2025 [TNT-12S] | "The information is published every 12 seconds instead of every minute." | Cadence |
| **3 Feb 2026** [TNT-12S], confirmed [TNT-MU] Q1 | "Starting 3 February 2026, we will determine the **regulation state** based on the new 12 second balance delta… Only the input changes from every minute to every 12 seconds." | **Target definition** — see below |

**The only primary statement on balance-delta timing** is [IPS61] fn.16: the
balance delta table *"shows these quantities, **approximately halfway each
minute**, together with the prices of the pricesetting bids."* That describes a
sub-minute publication point, not a multi-minute delay. Note also that the
2025-10-28 page lists an added delay as an **unexercised option** — which is
hard to reconcile with a 5-minute delay having been in force since Dec 2024.

**Resolution: measure it, do not read it.** The lag is now `null` in
`config/market_rules.yaml` with `lag_confidence: unresolved`, and
`data_availability.available_at()` must **refuse to serve** `balance_delta`
rather than fall back to a default. Phase 1 derives the lag empirically by
comparing each observation's publication timestamp to the instant it describes,
and writes the measured value back with evidence. A test fails if anyone fills
in a number without upgrading the confidence tag
(`test_balance_delta_lag_stays_unresolved_until_it_is_measured`).

This is strictly better than the withdrawn table: an empirically measured lag is
primary evidence about the actual data, whereas even a correct documented figure
would still need verifying against what the API returns.

#### 🔴 3 February 2026 — the regulation state changed meaning

This is the most consequential thing found in this review, and it is not a
pricing change.

The state rule ([IPS61] §4.3) asks whether the intra-ISP balance-delta series
*"continuously increases"*, *"continuously decreases"*, or *"both increases and
decreases"* (→ state 2, the only dual-priced state). From 3 February 2026 the
input to that test is the **12-second** series rather than the **1-minute**
series: **75 samples per ISP instead of 15.**

The wording is unchanged. The *effect* is not. Exact monotonicity over 75 noisy
samples is strictly less likely than over 15, so **the frequency of state 2
should rise on 2026-02-03 with no change whatsoever in the physical system.**

Consequences:

1. **T1 class priors break at 2026-02-03.** A classifier trained across it is
   trained on two different labelling procedures.
2. **Dual pricing becomes more common**, which *raises* the payoff to a
   well-calibrated `P(state = 2)` in the dispatch policy — the risk-aware policy
   should beat the deterministic one by more after this date than before.
3. **Only ~6 months of post-change data exist** (2026-02-03 → today). Not enough
   for a walk-forward on post-change data alone.

This is a **falsifiable prediction**: the empirical frequency of state 2 should
jump at 2026-02-03. Testing it in Phase 1 doubles as a check that the pipeline
is reading the state correctly. If the jump is absent, my reasoning above is
wrong and I want to know early.

### Q8 — Which series are revised — **PARTIALLY UNRESOLVED** ⚠

| Final at first publication | Revised |
|---|---|
| Settled imbalance price and regulation state (D+1) | Real-time imbalance price estimate → superseded by settled value |
| Day-ahead price (auction result) | Load forecasts (successive vintages) |
| | Wind/solar forecasts (successive vintages — **this is a feature, not a bug**: see below) |
| | Actual generation (late meter data) |
| | Cross-border flows |

The brief's R1 requires the **first-published vintage**. Two consequences:

- **ENTSO-E's API returns the current vintage, not the historical one.** For
  revised series, a history pulled today is *not* what was visible then. I
  cannot fully resolve this from the API. Recommended handling: for revised
  series used as features, either (a) restrict to series where the revision is
  known to be immaterial, or (b) reconstruct vintages from the live logging job
  going forward and quantify the bias on the backtest period. This must go in
  `LIMITATIONS.md` with a quantified bound, per R1.
- **Forecast revision is itself the signal.** [REG543] Art. 14(2)(d) mandates a
  D-1 17:00 vintage *and* a D 07:00 update. The difference between them is a
  legitimately-available, non-leaking leading indicator of imbalance — precisely
  the "forecast error proxy" the brief asks for (§4 Features). Capturing both
  vintages from day one is the highest-value thing the Phase 1 fetcher can do,
  and it is **irrecoverable if not captured live**. Open-Meteo's historical
  *forecast* archive is the equivalent for weather.

### Q9 — The realistic decision timestamp — **ASSUMED (my choice, needs your sign-off)**

**Proposal: the decision for ISP `t` is taken at `start(t)`, with information
cutoff `start(t) − lag(field, t)`.**

Justification:

- A battery bidding into imbalance must have its position set as the ISP opens;
  it cannot condition on how `t` unfolds.
- With balance-delta lag `L`, the newest usable observation at `start(t)` is
  from `start(t) − L`. Under the 2-minute regime that is 2 minutes before the
  ISP opens; under the 5-minute regime, 5 minutes. The *same* decision rule
  therefore has a different information set in different years — which is
  correct and is exactly what the time-varying lag encodes.
- This is the **conservative** reading of R1.

**What this deliberately excludes.** Real passive balancers re-decide
*continuously within* the ISP as the balance delta updates (every 12 s since
late 2025). That intra-ISP policy is a strictly richer and more realistic
problem, and it is where the real money is. Modelling it requires an intra-ISP
dispatch formulation, not just a different cutoff. **I recommend deferring it
and saying so in `LIMITATIONS.md`** rather than half-implementing it. If you
want it in scope, say so now — it changes the Phase 3 optimisation formulation,
not just a constant.

---

## Market structure

### Q10 — Day-ahead and intraday MTU — **PRIMARY**

**The day-ahead MTU changed from 60 to 15 minutes on delivery day 1 October
2025** (trading day 30 September 2025), across all SDAC bidding zones
simultaneously, Ireland excepted (30 min) [NEMO2025]. The 12:00 CET day-ahead
auction now clears 96 quarter-hourly products per day. Intraday (SIDC/XBID) has
supported 15-minute products for considerably longer.

**Interaction with the ISP, and why it matters here:** before 2025-10-01 the DA
MTU (60 min) and the ISP (15 min) were *mismatched* — one DA price covered four
ISPs, so the "spread vs day-ahead" target (T3) had a step-function component
that was pure artefact of the MTU mismatch. From 2025-10-01 they align 1:1.

**Consequence for T3.** The DA-vs-imbalance spread is **not comparable across
2025-10-01**. Modelling T3 across that boundary without handling it will produce
a spurious regime change. Options: model T3 only post-alignment, or construct a
consistent pre-period DA reference (e.g. hourly DA broadcast to four ISPs) and
segment results at the boundary. My recommendation is the latter plus explicit
segmentation, because restricting to post-2025-10-01 leaves under a year of data.

### Q11 — NL grid-scale battery capacity — **SECONDARY, and the sources conflict badly** ⚠

I am not willing to state a single number. What the sources actually say:

| Claim | Source | Type |
|---|---|---|
| ~4.1 GW of battery projects in the *realisation* phase as of 1 Sept 2025 | *[sec-ess]* | Secondary |
| ~6 GW of battery projects gaining grid access via time-dependent transmission rights | *[sec-ess]* | Secondary |
| Energy Storage NL expected capacity to **double during 2025**, to ~2 GWh | NL Times | Secondary |
| ~2.5–3.0 GW / 4.5–6.0 GWh installed at end-2025 | SEO-style market-report sites | **Low quality — likely AI-generated. Discard.** |
| ~60–70 GW of battery storage in TenneT's connection queue (vs ~20 GW peak load) | *[sec-ess]* | Secondary |
| 6.7 GW projected by 2030 (up from a 4.9 GW estimate the year before) | *[sec-ess]* | Secondary |

Some of these mix **GW (power)** and **GWh (energy)**, and others mix
*operational*, *in realisation*, and *queued* capacity. Those are three very
different quantities and the trade press routinely conflates them.

**What is nonetheless robust enough to carry the market-impact argument** (§6 of
the brief): NL grid-scale storage is on the order of **single-digit GW and
growing at tens of percent per year**, against a system peak load of ~20 GW, with
a connection queue an order of magnitude larger than what is built. The
capacity racing to exploit the imbalance signal is growing far faster than the
signal's exploitable depth. That is the qualitative claim the revenue-per-MW
saturation curve needs, and it holds under every source above.

**Before this goes in the report** it needs one authoritative figure — TenneT's
own published statistics or CBS/Energy Storage NL primary data — clearly
labelled operational-only, with GW and GWh stated separately.

---

## Open questions for you

Ordered by how much damage a wrong answer does.

**Resolved in review (2026-08-04):**

- ~~PICASSO (Q5)~~ — **closed.** v6.1 confirms price formation unchanged.
- ~~Decision timestamp (Q9)~~ — **ISP start**, confirmed. Intra-ISP re-decision
  is out of scope and documented in `LIMITATIONS.md`.
- ~~T3 across the MTU change (Q10)~~ — **broadcast-and-segment**, confirmed.
- ~~Balance-delta delay timeline (Q7)~~ — claim **withdrawn**; superseded by
  "measure it in Phase 1" (below).

**Still open:**

1. **Final holdout.** Per §11 this is your call and I have not touched it.
   The 2026-02-03 state-definition change complicates the obvious choice: the
   most recent contiguous period is now the *only* period on the new
   state-labelling regime, which makes it both the most relevant holdout and the
   least comparable to the walk-forward period. My recommendation is to hold out
   the most recent ~3 months anyway and report the incomparability explicitly —
   but flag this as a decision I want you to make.
2. **Revision vintages (Q8).** Accept the limitation and quantify the bias, or
   restrict features to non-revised series only? The latter is cleaner and costs
   real predictive power. Deferrable to Phase 1 once the data is in hand.
3. **Price limits (Q6).** Still secondary. Low impact — realised prices sit
   orders of magnitude inside these bounds — so it only affects outlier policy.
4. **Balance-delta lag.** Not a question for you any more: Phase 1 measures it.
   But if you ever find a TenneT netcode amendment or GEN/consultation document
   that states a delay in minutes, it would let us cross-check the measurement.

## What I did not do

- Did **not** register for the ENTSO-E API token — it needs your account and
  email (§3). `.env.example` documents the process. **Do this today**; it takes
  a few working days and blocks Phase 1.
- Did **not** start the live forecast logging job. The brief (§7) is right that
  it accrues value with wall-clock time and nothing else does — but it needs the
  token first.
- Did **not** verify TenneT's own transparency pages directly: `tennet.eu`
  returned HTTP 403 to every automated fetch attempt.
