# Decision log

ADR-style. Every significant choice, and why. Newest last.

---

## ADR-000 — Active agent plugins and their versions

Recorded at project start per CLAUDE.md §12, so that a mid-project plugin update
that changes agent behaviour can be attributed.

| Plugin | Version | Activation |
|---|---|---|
| **ponytail** | **4.8.4** | Session-start hook, level `full`. Correctly installed as a plugin, not a bare skill. |
| **superpowers** | **6.2.0** | Session-start hook (`using-superpowers`). |
| skill-creator | unspecified in `plugin.json` | Skill only |

Resolved from `~/.claude/plugins/cache/`.

### Lean / rigour zone split — restated back, as §12 requires

Ponytail is active, so here is the split as I have registered it:

**Ponytail applies freely — lean zones:**
- Frontend and demo. No component library, no state-management dependency.
- Dependency selection *everywhere*. Every package justifies itself.
- Data fetching, caching, plumbing. Boring is correct.
- Config, scripts, CI, Makefile.
- Any abstraction introduced "for later".

**Ponytail is suspended — rigour zones:**
- `src/data/data_availability.py` and everything enforcing publication lag (R1).
- `config/market_rules.yaml` and the settlement logic derived from it.
- `src/backtest/` — the event-driven engine and its information-cutoff enforcement.
- `src/evaluation/` — metrics, calibration, significance tests, bootstrap CIs, segmented reporting.
- The entire test suite covering the above.

In rigour zones the extra code *is* the deliverable, and `ponytail:` shortcut
comments are prohibited there. Precedence when the two conflict: CLAUDE.md §1
rules → rigour zones → plugin rulesets → general judgement.

**Conflicts so far:** none requiring resolution. Ponytail's ladder and the brief
agree on everything built in Phase 0 except test verbosity in
`tests/test_settlement.py`, where the rigour-zone rule wins by design — see
ADR-004.

---

## ADR-001 — Python pinned to `>=3.11,<3.13`; environment via `uv`

**Context.** The machine had only Python 3.10 (below the brief's 3.11+ floor),
no `uv`, and no `make`. Left to itself `uv` selected CPython **3.14.6**, which
satisfies `>=3.11` and on which the suite passed.

**Decision.** Installed `uv` (0.12.1). Added an upper bound `<3.13` and a
`.python-version` of 3.12.

**Why.** 3.14 would have worked for Phase 0 — which needs only PyYAML — and
broken in Phase 2/3, where `lightgbm`, `cvxpy` and `pyarrow` lag new CPython
releases by months. Discovering that after writing the modelling code costs far
more than the bound costs now. Raise the bound when those wheels exist.

## ADR-002 — Dependencies added when used, not in advance

`pyproject.toml` declares only `pyyaml` plus a dev group. No pandas, no
`entsoe-py`, no `lightgbm` yet — nothing in Phase 0 imports them, and pinning
versions months before first use means pinning stale ones. Ponytail lean zone;
no tension with the brief.

## ADR-003 — `make` is absent on this machine; Makefile shipped regardless

**Context.** CLAUDE.md §9/§15 require a Makefile and a working `make test`. GNU
make is not installed on Windows and is not available via `uv`.

**Decision.** Ship the Makefile as specified. Every target is a one-line wrapper
over `uv run ...`, so the same commands work verbatim without `make`. `make test`
→ `uv run pytest`.

**Status: partially unmet.** `make test` itself cannot run here until make is
installed (`winget install ezwinports.make`). The *verification* is unaffected —
`uv run pytest` is what CI will run — but the literal §15.3 requirement is
outstanding and is flagged rather than quietly reinterpreted.

Unbuilt phase targets (`fetch`, `train`, …) `exit 1` rather than no-op, so
`make repro` can never appear to succeed while silently skipping a step (R6).

## ADR-004 — `market_rules.yaml` is executable config, not documentation

**Context.** CLAUDE.md §2 requires the settlement rules to live in exactly one
place, read by every downstream module.

**Decision.** The regulation-state → price-selection table lives in the YAML as
data (`min(p_down, p_mid)` etc.). `src/market.py` *resolves* that table rather
than restating it, via a ~20-line parser accepting only `p_up`, `p_down`,
`p_mid`, `min(a,b)`, `max(a,b)` and raising on anything else.

**Why not `eval`.** An unexpected expression must fail loudly. `eval` would
silently evaluate something plausible, and in a settlement path "plausible but
wrong" is the worst possible failure mode.

**Why not just write the rules in Python.** Then the YAML would be decorative
and would drift from the code within weeks — the exact failure §2 legislates
against.

**Rigour-zone note.** `tests/test_settlement.py` (23 tests) is longer than the
40 lines of logic it covers. Ponytail's ladder would flag that. Declined: the
invariant it protects is that every euro in this project is settled with the
correct sign, and the property test (`price_short >= price_long` across all
states and price combinations) catches an inverted rule table that
example-based tests would miss. Expected values are hand-worked from [IPS61]
Table 2, not recorded from the implementation's output.

## ADR-005 — Decision timestamp set at ISP start (confirmed)

Backtest information cutoff for target ISP `t` is
`start(t) − lag(field, t)`. See `docs/DOMAIN_NOTES.md` Q9 for the argument and
what it excludes (intra-ISP re-decision).

**CONFIRMED 2026-08-04.** Intra-ISP re-decision is out of scope and recorded in
`LIMITATIONS.md`. Same review confirmed ADR-008 below.

## ADR-006 — Balance-delta publication lag is measured in Phase 1, not read from docs

**Superseded the original ADR-006** ("publication lag is a function of time"),
which was built on a claim that did not survive review.

**Context.** The original version asserted a 3 → 5 → 2 minute balance-delta
publication *delay* timeline from trade press. Manual review of TenneT's own
pages (2025-10-28, 2025-11-25, 2026-02-20) found **only cadence changes**
(1/min → 5/min → every 12 s) and found an added delay described as an option
with *"no concrete plans"* as of 2025-10-28. The only primary statement on
balance-delta timing, [IPS61] fn.16, describes publication *"approximately
halfway each minute"* — a sub-minute point, not a multi-minute lag.

**Cadence and delay are different mechanisms.** Cadence is how often a value is
published; delay is how long after the instant it describes it becomes visible.
A frequency increase is not a lag reduction, and the secondary sources appear to
conflate them.

**Decision.** `publication.balance_delta.lag_seconds` is `null` with
`lag_confidence: unresolved`. `data_availability.available_at()` must **refuse
to serve this field** rather than default to a guess. Phase 1 measures the lag
empirically — comparing each observation's publication timestamp to the instant
it describes — and writes the measured value back with evidence.
`test_balance_delta_lag_stays_unresolved_until_it_is_measured` fails if anyone
sets a number without upgrading the confidence tag to `measured` or `primary`.

**Why this is better than the withdrawn version.** An over-generous lag is a
silent R1 look-ahead violation that inflates every revenue figure and looks
correct in code review. A measured lag is primary evidence about the actual
data; even a correct documented figure would still need checking against what
the API returns.

**What survives from the original ADR.** The *principle* that the lag may be
time-varying, and that `available_at` must therefore take the target timestamp
rather than return a constant. `time_varying: unknown` until measured.

## ADR-007 — 2026-02-03 is a target-variable regime change, not a data change

**Context.** From 3 February 2026 TenneT determines the regulation state from
the 12-second balance delta instead of the 1-minute series — 75 samples per ISP
instead of 15. The rule wording in [IPS61] §4.3 is unchanged.

**Finding.** The rule asks whether the intra-ISP series is monotonic (→ ±1) or
both rises and falls (→ 2, the only dual-priced state). Exact monotonicity over
75 noisy samples is strictly less likely than over 15, so **the frequency of
state 2 should rise at this date with no change in the physical system.**

**Decision.** Registered as a structural break with `rules_changed: false`, and
`regulation_states.state_determination_input` records both regimes. T1 results
must be segmented at 2026-02-03, and the Phase 1 data-quality report must test
the predicted jump in state-2 frequency.

**Why it is logged as a decision and not just a note.** It is the kind of change
that is invisible in the price data — nothing about price *formation* changed —
so a model trained across the boundary would silently learn two different
labelling procedures. It also *raises* the value of the risk-aware dispatch
policy after that date, which is a prediction the Phase 3 comparison can test.

**Falsifiable.** If the empirical state-2 frequency does not jump at 2026-02-03,
this reasoning is wrong. Check it early.

## ADR-008 — T3 spans the 2025-10-01 MTU change via broadcast-and-segment

**CONFIRMED 2026-08-04.** Day-ahead prices before 2025-10-01 are hourly; they
are broadcast across the four ISPs of each hour so that T3 (spread vs day-ahead)
is defined over the full history. Every T3 result is segmented at 2025-10-01.

**Why not restrict to post-2025-10-01.** That leaves under a year of data —
too thin for walk-forward plus an untouched holdout (R2).

**The cost, stated.** Pre-alignment, one day-ahead price covers four ISPs, so
broadcast T3 carries a step-function artefact that is a property of the MTU
mismatch and not of the market. Segmentation is what keeps that honest; the
segmented tables are the reported result, the pooled number is not.

## ADR-009 — `rule: unresolved` always pairs with `lag_seconds: null`

**Context.** Task 2 review found `publication.activated_balancing_volumes`
carrying `rule: unresolved` next to `lag_seconds: 3600` — a live-looking
number left over from before the field had a `rule` at all. It was safe only
because `available_at()` (Task 3) dispatches on `rule` before touching
`lag_seconds`. The moment any code path reads `lag_seconds` without checking
`rule` first, it silently uses a made-up lag — the same hazard the `-1`
sentinel refactor (this task) existed to remove, recurring at smaller scale.

**Decision.** Any `publication` field with `rule: unresolved` must set
`lag_seconds: null`. A placeholder number, however clearly labelled `assumed`
in the neighbouring `confidence` field, is indistinguishable from a real
measurement at the call site that only reads `lag_seconds`.
`activated_balancing_volumes.lag_seconds` is now `null`; the note records that
the prior `3600` was an unverified assumption, not a measurement, and has been
removed for that reason.
`test_unresolved_fields_carry_no_usable_lag` makes this structural: it fails
if any future `unresolved` field is given a numeric `lag_seconds`.

**Same reasoning as ADR-006.** `balance_delta` already established the
pattern (`lag_seconds: null` until the lag is *measured*, not asserted). This
ADR generalises it: it is not particular to balance delta, it is the rule for
every `unresolved` publication field.

## ADR-010 — `.gitignore`'s `data/` pattern was unanchored and silently shadowed `src/data/`

**Context.** Found while committing Task 3 (`src/data/data_availability.py`).
`.gitignore` line 2 read `data/` with no leading slash. Gitignore patterns
without a `/` elsewhere in them match a directory of that name at *any* depth,
not just at the repo root — so `data/` matched both the intended
`<root>/data/` (raw-data cache, R7) **and** `src/data/`, an actual Python
package. `git add` silently skips ignored paths unless forced, so this had
already dropped `src/data/__init__.py` from every commit since Phase 0 with no
error at any point (`git ls-files src/data/` showed only `timebase.py`, added
via an unrecorded force-add). It was about to do the same to
`data_availability.py` — the exact deliverable this rigour zone exists to
protect — silently.

**Decision.** Anchored the pattern to the repo root: `data/` → `/data/` (and
the three `!data/.../.gitkeep` negations the same way). `src/data/__init__.py`
restored to tracking in this commit. Documented the fix inline in
`.gitignore` itself so a future edit doesn't casually strip the leading slash.

**Separate finding, NOT fixed here (out of scope for Task 3).** Even after
anchoring, `git ls-tree HEAD -- data/` shows none of `data/raw/.gitkeep`,
`data/interim/.gitkeep`, `data/processed/.gitkeep` have ever been tracked.
This is the classic gitignore limitation: a negation cannot re-include a file
inside a directory that the parent pattern already excludes — `/data/`
excludes the directories themselves, so git prunes them during traversal and
never evaluates the per-file `!` rules inside. Fixing it needs `/data/*` +
`!/data/raw/` (etc.) rather than `/data/` + `!/data/raw/.gitkeep`. Flagged for
whoever next touches repo scaffolding; not fixed here to keep this task's diff
scoped to what it was asked to deliver.

**Why this belongs in the decision log and not a silent side-fix.** The whole
point of Task 3 is refusing to silently do the wrong thing. A collision in the
mechanism that decides what even reaches version control is the same failure
class at the tooling layer, and deserved the same treatment: caught, explained,
fixed where it blocked delivery, and disclosed rather than quietly patched.

## ADR-011 — Closed the ADR-010 `.gitkeep` deferral; also wired `write_frame` into the fetchers

**Context.** Final whole-branch review flagged the item ADR-010 explicitly
deferred (`git ls-files data/` was empty; `git check-ignore -v
data/raw/.gitkeep` showed the negation lines were dead), plus a real gap in
the data layer: `cache.write_frame()` had no production caller.
`src/data/entsoe.py` and `src/data/openmeteo.py` fetchers called `store_raw()`
for the raw payload but never persisted the parsed frame, so
`scripts/build_quality_report.py`'s `read_frame()` call had nothing to read
once the ENTSO-E token arrives — a silent, undiagnosable empty report.

**Decision, `.gitkeep`.** Implemented ADR-010's own proposed fix rather than
the alternative (dropping the negations): `/data/*` + per-subdirectory
`!/data/raw/` / `/data/raw/*` / `!/data/raw/.gitkeep` triplets, one per
subdirectory. Verified both directions explicitly:
`git check-ignore -v data/raw/.gitkeep` now reports *not* ignored (exit 1, no
output) and `git check-ignore -v data/raw/anything.parquet` still reports
ignored (via `/data/raw/*`, `*.parquet` as backstop) — R7 intact. The three
`.gitkeep` files are added to tracking in this commit so a fresh clone gets
the `data/` tree back.

**Decision, `write_frame` wiring.** Each fetcher now calls `write_frame()`
with a dataset name after `store_raw()`, not instead of it: `imbalance_prices`,
`day_ahead_price`, `load_forecast`, `wind_solar_forecast` (entsoe.py),
`weather_forecast` (openmeteo.py). An empty parsed frame is never written —
writing an empty partition would read as "we have data for this month, and
it's empty" when the true state is "we have no data at all" (R3). Covered by
offline tests that monkeypatch the ENTSO-E client / `httpx` with hand-built,
explicitly-labelled-synthetic payloads and assert the Parquet file appears
under a `tmp_path`-monkeypatched `cache.DATA_ROOT` and round-trips, plus a
counterpart test per module proving the empty-frame path writes nothing.

**Also closed while in this file.** `read_frame()` now raises the same
explicit `ValueError` as `write_frame()` on a naive `start`/`end`, instead of
surfacing pandas' incidental `TypeError`. `cache.py`'s module docstring now
states `load_raw` is the intended re-parse entry point and is not yet wired
into any pipeline, rather than leaving that claim only implicit.

## ADR-012 — `available_at` gains a vintage dimension; default stays conservative

**Context.** Six of the ten publication fields are `revised: true`. The final
Phase 1 review flagged that `available_at(field, target)` returned a single
answer per field, so the feature builder could not ask "when was the *second*
vintage retrievable?" — blocking the forecast-error proxy CLAUDE.md §4 names as
a key feature. Reg. 543/2013 Art. 14(2)(d) mandates exactly two wind/solar
vintages: 17:00 on D-1, and an intraday update at 07:00 on D.

**Decision.** Fields may declare a `vintages:` list in
`config/market_rules.yaml`, earliest-published first. `available_at` takes an
optional `vintage` name; **omitting it resolves the earliest vintage**, so code
that does not reason about revisions can never accidentally read a later one.
`available_vintages(field, target, decision_time)` returns the names visible at
a decision instant. A new rule, `published_same_day_at`, expresses the intraday
update.

**Why the default is the earliest and not the latest.** The latest vintage is
the most informative and the most dangerous: it is the one that did not exist
at decision time. Defaulting to it would make every un-annotated call site a
potential R1 violation. Defaulting to the earliest makes the failure mode
"slightly less information than we could have had", which is recoverable.

**Why an unknown vintage raises instead of falling back.** A caller that asked
for the intraday revision and silently received the day-ahead publication time
would believe a later revision was available earlier than it was — a look-ahead
error that reads as correct code. Verified by mutation: adding a fallback is
killed by the test suite.

**Guard.** The unresolved-lag check runs against the parent spec *before* any
vintage is resolved, so a vintage argument cannot route around it.

**Scope.** Only `wind_solar_forecast_day_ahead` has a declared schedule. The
other revised fields are revised on schedules not established from primary
sources; they expose no named vintages and return their conservative
first-publication time. Those schedules should be *measured* from the vintage
log (ADR-013), not invented.

## ADR-013 — the vintage log lives outside `data/` and is committed

**Context.** ENTSO-E and Open-Meteo both serve the current vintage only. R1
requires the first-published vintage. A history pulled later is therefore not
what was visible at the time, and the difference is unrecoverable.

**Decision.** `src/data/vintage.py` is an append-only store keyed on
`(observed_at, target_time)`; nothing is ever overwritten, and `latest_as_of`
returns only what was observed strictly before a given instant. Records live in
`vintage_log/`, **outside** `data/`, and are committed.

**Why outside `data/`.** `data/` is gitignored under R7 because it holds raw
licensed market data. The vintage log is our own forecast captures, and it must
survive a clean checkout — a track record that vanishes on clone is not a track
record. Keeping the two in separate trees makes the licensing distinction
structural rather than a matter of remembering.

**Why first-write-wins on a duplicate `(observed_at, target_time)`.** A given
`observed_at` names one observation. Silently replacing its values on a re-run
would rewrite history, which is precisely what this store exists to prevent.

**Cost, stated.** This only works forward in time. Every scheduled run that does
not happen is a vintage that never existed. That is why the job was started
before the model exists rather than after.

## ADR-014 — settled prices publish on a wall clock, not at a fixed lag

**Found by running the CLI, not by reading the code.** `src.cli availability`
printed the settled price as available `2026-08-08T20:15Z` for a `10:00Z` ISP,
which looked plausible until checked against the source.

**Context.** [IPS61] §3.2: *"After the delivery day (D+1), the process of
financial settlement starts at 10.00 a.m."* That is a wall-clock rule, and one
run settles the **whole** delivery day. Phase 0 encoded it as a fixed
`lag_seconds: 122400` (34 h), described as "worst case".

**The error.** A fixed lag is wrong in *shape*, not just in value. Measured
against D+1 10:00 local it was **up to 21 hours too late** for mid-day ISPs,
and it made different ISPs of the same delivery day settle at different
instants, which cannot happen.

**Why it mattered far more than "conservative on the target" suggests.** The
settled price is the target, so a late availability time is harmless *for the
target*. But CLAUDE.md §4 mandates baselines that consume **lagged settled
prices** — persistence, and seasonal naive (same period, previous day/week).
Under the fixed lag, a seasonal-naive baseline could not use yesterday's settled
price at **0 of 6** sampled ISPs, though it genuinely had it.

An artificially weak baseline flatters every model measured against it. This was
therefore a bias in the *favourable* direction — the exact failure R4 ("baselines
first, always") exists to prevent, and the kind that survives review because
nothing looks broken.

**Decision.** New rule `published_day_after_at`, applied to
`imbalance_price_settled` and `regulation_state`. The three wall-clock rules
(`_day_before`, `_same_day`, `_day_after`) now share one implementation
differing only by a day offset.

**Verification.** Reverting the config to the fixed lag is killed by the suite.
Tests pin: one publication instant per delivery day; the target never usable at
its own decision time (checked across every ISP of a day); the baseline can
reach D-1; and both boundary cases where it legitimately cannot.

**Honest caveat.** [IPS61] says the settlement *process starts* at 10:00 — not
that prices are retrievable at 10:00. And we will fetch from ENTSO-E, whose own
publication deadline for imbalance prices was never verified against Reg.
543/2013 (still listed UNRESOLVED in DOMAIN_NOTES Q7). So 10:00 local is the
best primary-sourced estimate, not a measured fact. Measure it from the vintage
log once the token lands, and tighten this entry then.

**Recurring lesson.** All three of my own mistakes while fixing this were the
same one: treating a UTC calendar day as a delivery day. 00:00 UTC is already
02:00 in Amsterdam. Any "same period yesterday" arithmetic must be done in local
delivery days, and `test_late_evening_utc_isp_belongs_to_the_next_local_delivery_day`
pins that for whoever writes the feature builder.

## ADR-015 — ENTSO-E carries dual pricing; the Q3 open question is closed

**Verified live on 2026-08-07**, first call with a real token.

- The NL imbalance item returns **two distinct columns**, `Long` and `Short`,
  renamed at the boundary to `price_long` / `price_short`. **TenneT's own feed
  is therefore NOT required for settlement**, which removes the fallback risk
  DOMAIN_NOTES Q3 flagged.
- 96 rows/day exactly, UTC, 15-minute spacing — the ISP length is confirmed
  empirically, not just from documentation.
- **`price_short >= price_long` held across 8,064 ISPs (2025-11 to 2026-07)
  with zero violations.** That invariant was derived by hand from [IPS61]
  Table 2 before any data existed. Real market data agreeing with it is the
  strongest available evidence that the settlement logic is right.
- The two prices differ in roughly **35%** of ISPs — dual pricing is common,
  not an edge case. This materially raises the expected value of the
  risk-aware dispatch policy over the deterministic one, which is the
  comparison Phase 3 exists to make.

The live assertions now live in the integration test, so a change in ENTSO-E's
schema or in the settlement rules surfaces as a failure rather than as a
silently wrong backtest.

## ADR-016 — ADR-007's structural-break prediction: consistent, not proven

ADR-007 predicted that state 2 would become **more** frequent from 2026-02-03,
when TenneT switched the regulation-state input from the 1-minute to the
12-second balance delta (15 -> 75 samples per ISP), with no change in the
physical system.

Measured on the share of ISPs where `price_long != price_short`, which is a
**lower bound** on state 2 (a fully reverse-priced state-2 ISP has the two
equal):

| Window | Input | Dual-priced |
|---|---|---|
| 2025-11-01..15 | 1-min | 32.1% |
| 2025-12-01..15 | 1-min | 28.9% |
| 2026-01-10..24 | 1-min | 33.6% |
| 2026-02-10..24 | 12s | 33.9% |
| 2026-04-01..15 | 12s | 42.9% |
| 2026-07-01..15 | 12s | 42.1% |

Pooled: **31.5% before, 39.6% after — +8.1 points**, in the predicted
direction.

**But this is not proof, and it should not be reported as such.** Three
reasons: the windows straddle winter and summer, so seasonality is an
uncontrolled confound; the window immediately after the change (Feb 10-24,
33.9%) is barely above the pre-period, with the rise concentrated in April and
July, which is what a seasonal explanation would also look like; and the sample
is six fortnights, not a designed test.

**Status: consistent with the mechanism, confounded with season.** To separate
them, compare like-for-like calendar windows across years once more history is
loaded, and reconstruct the regulation state directly rather than inferring it
from the price columns. Until then this is reported as a hypothesis with
supporting evidence, never as a demonstrated causal effect.

## ADR-017 — the balance-delta delay is real and configurable; I over-corrected

**Correcting ADR-006 in part.** ADR-006 withdrew the trade-press claim of a
3 → 5 → 2 minute publication *delay*, on the grounds that TenneT's public pages
documented only **cadence** changes and listed an added delay as an option with
"no concrete plans". That was right about the evidence and **too strong about
the conclusion**: I let "the timeline is unsubstantiated" drift toward "a delay
may not exist at all".

**New primary evidence (2026-08-07)**, from TenneT's own API documentation for
`Balance Delta High Res`:

> "Responses return the most recent available 30 minutes, **subject to a
> configurable delay imposed by TenneT**."

So a delay mechanism demonstrably exists, and it is a knob TenneT turns. What
remains unpublished is its *current value* — which is what the harness measures.

**What this changes:**

- The delay is real. Say so, rather than implying the concept was invented.
- "Configurable" means it can change without notice, so the measurement must be
  **repeatable and re-run**, not done once and trusted forever. `time_varying`
  stays, and now on primary grounds rather than as a hedge.
- The lag stays `null` / `unresolved` in config. Knowing a delay exists is not
  knowing its value, and the field must keep refusing until measured.

**What ADR-006 got right and keeps:** cadence and delay are different
mechanisms, the trade press conflated them, and the specific 3/5/2 timeline is
still unsupported by anything primary. Measuring beats citing either way.

**Also now known from the same source**, and encoded in the harness:

| Fact | Value |
|---|---|
| Endpoint | `https://api.tennet.eu/publications/v1/balance-delta-high-res/latest` |
| Rate limit (`/latest`) | 1 req/sec, **10 req/min** |
| Rate limit (timeframe endpoint) | **8 req/DAY**, max 4-hour window |
| Refresh cadence | every 12 s; poll 1 s after each event (:01, :13, :25, :37, :49) |
| Response window | most recent 30 minutes |
| Timestamps | UTC |
| Auth | Azure API Management subscription key (403 is served by Azure App Gateway) |
| Bulk history | manual download, up to 100 MB, from TenneT's transparency download page |

**A bug this caught.** The harness previously defaulted to polling every 5
seconds — 12 requests/minute, over TenneT's 10/min cap, which their docs say
"may result in temporary blocking of your API keys". It now follows TenneT's
recommended five-per-minute schedule and counts requests per minute against the
cap. A test pins the cadence so nobody "optimises" it back over the limit.

## ADR-018 — the balance-delta feed carries the settlement component prices

**Discovered by probing the live API, 2026-08-07.** Each 12-second point in
`balance-delta-high-res` carries, alongside the power decomposition:

| Field | Settlement meaning |
|---|---|
| `max_upw_regulation_price` | `p_up` — highest activated upward price |
| `min_downw_regulation_price` | `p_down` — lowest activated downward price |
| `mid_price` | `p_mid` — the mid-price |

Those are exactly the three inputs to `src/market.py: imbalance_prices()`.

**Consequence.** The imbalance price for an ISP can be computed in **near-real
time** from this feed via the settlement rules, instead of waiting for the
settled publication at D+1 10:00. That is a materially different information
position from the one Phase 0 assumed, and it is the natural input to a live
forecast/dispatch loop.

`null` is meaningful, not missing: `max_upw_regulation_price` is null when no
upward regulation is active. `imbalance_prices()` already accepts `None` for a
component its state's rule does not reference, so the two fit together without
special-casing.

The same points also decompose the balance delta by mechanism —
`power_{afrr,igcc,mfrrda,picasso,mari}_{in,out}` — which makes PICASSO and IGCC
exchange directly observable rather than inferred (cf. ADR-015 on PICASSO's
effect on the price distribution).

**Not yet exploited.** This is recorded now because it changes what Phase 2 can
build; no feature depends on it until the lag is measured and the field stops
being refused.

## ADR-019 — the lag harness must discard its first response

**A bug caught by running it, not by reading it.** Each `/latest` response
carries the most recent ~30 minutes — about 150 points. On the first call every
one is unseen, so a naive loop records ~150 "new" observations whose apparent
lag ranges up to 30 minutes, because almost all were published long before we
started watching. That would have dominated the sample and wrecked the p95 —
in the **permissive** direction if it had gone the other way, and in any case
producing a confidently wrong number.

**Fix.** The first response seeds the seen-set only; no samples are emitted
from it. Only points appearing in a *later* response were published while we
were watching.

**Residual bias, stated.** We poll every 12 s, so a point can sit published for
up to one poll interval before we notice: measured lags are biased **up** by
0–12 s. That is the conservative direction for R1 — it can never make data look
available earlier than it was — and small against a lag of order two minutes.
Reported, not hidden.

**Validation run (6 min, 30 samples, 2026-08-07 ~12:15 UTC):** median 133.6 s,
p95 134.0 s, max 134.0 s. Exactly one new point per 12-second poll, so the
poll schedule and the feed are in lockstep.

**Deliberately NOT written to config yet.** Six minutes spans less than one
ISP and one time of day. The distribution is strikingly tight, which suggests a
deterministic configured delay rather than a noisy one — but ADR-017 established
that TenneT can reconfigure it, and a value measured at midday is not evidence
about 03:00. The field stays `unresolved` until a run of at least two hours.
Writing a plausible number early is precisely the failure this project is built
to avoid.

## ADR-020 — balance-delta lag measured at 134 s; field enabled

**630 samples, 10 ISPs, 2026-08-07 12:15–14:25 UTC.**

| | seconds |
|---|---|
| min | 133.0 |
| median | 133.5 |
| **p95 (encoded)** | **134.0** |
| max | 134.0 |
| stdev | 0.29 |

Total range across 630 samples is **1.0 second**. That is consistent with a
deterministic configured constant rather than a noisy pipeline — which matches
ADR-017's finding that the delay is a knob TenneT sets.

`rule` moves from `unresolved` to `lag_after_period`, `lag_confidence` to
`measured`, and `available_at` now serves the field. Full provenance —
method, script, window, sample count, and the p95-not-median rationale — is
recorded inline in `config/market_rules.yaml`.

**On the trade press.** The widely-repeated "2 minutes" is close to right as a
*current value* (133.5 s median). ADR-006 was still correct to withdraw it: the
3 → 5 → 2 minute *timeline* remains unsupported by anything primary, and this
measurement says nothing whatsoever about historical values. We now have our
own number for today, not a reconstructed history.

**Coverage limitation — the reason this is not finished.** 2.18 hours of one
weekday afternoon. Not observed: overnight, weekends, or scarcity periods —
which is exactly when a battery earns most and when a TSO is most likely to
intervene. Re-measure across a full 24 h before any headline revenue figure
depends on this, and again after any TenneT platform change. Recorded in the
config as `lag_coverage_caveat` and in `LIMITATIONS.md`.

## ADR-021 — ISP-level availability for a sub-ISP signal is deliberately conservative

Balance delta publishes every **12 seconds**, but `lag_after_period` answers at
ISP granularity: it reports the whole of ISP *t* as arriving 134 s after *t*
**ends**. In reality each 12-second point arrives 134 s after *that point*
ends, so most of ISP *t−1* is visible well before this rule admits.

**Decision: keep the ISP-level rule as the default.** It can only ever withhold
information, never grant it early, so it is safe under R1 — and a wrong answer
in the safe direction is recoverable, while a wrong answer in the permissive
direction is the failure this whole layer exists to prevent.

**Cost, stated plainly.** The default discards up to ~12.8 minutes of the
previous ISP's intra-period signal — and intra-ISP balance-delta shape is
precisely what determines the regulation state (whether the series is monotonic
or not). So this conservatism bites hardest on the T1 classification target.

**Next step, for the Phase 2 feature builder:** point-level availability,
`point_end + lag`, rather than the ISP-level answer. That needs a per-point
signature, not `available_at(field, isp)`. Pinned by
`test_balance_delta_isp_level_availability_is_deliberately_conservative` so the
limitation is a recorded decision rather than something discovered later.

**Also worth knowing:** even at ISP level the newest usable balance delta at
decision time is **t−2**, not t−1 — t−1 ends exactly at the decision instant
and publishes 134 s after it. Same shape as the real-time price estimate, and
for the same reason.

## ADR-022 — ADR-007 withdrawn: the dominant break is PICASSO, not the 12s state input

**I got this wrong, and the earlier "confirmation" was an artefact of a crude
before/after split on a rising series.**

ADR-007 predicted state 2 would become more frequent from 2026-02-03 (the
12-second state-determination input). ADR-016 reported +10.9pp across that date
and called it "consistent, confounded with season". Sampling one week per
quarter back to 2021 shows the real shape:

| Quarter | Dual-priced | | Quarter | Dual-priced |
|---|---|---|---|---|
| 2021-02 | 6.8% | | 2024-08 | 12.5% |
| 2021-11 | 6.0% | | **2024-11** | **25.7%** ← after PICASSO |
| 2022-08 | 3.9% | | 2025-05 | 26.2% |
| 2023-05 | 4.8% | | 2025-11 | 32.6% |
| 2024-02 | 5.7% | | **2026-02** | **26.5%** ← after 12s input |
| 2024-05 | 8.0% | | 2026-05 | 40.5% |

Two things kill the ADR-007 story:

1. **The share roughly quadruples at PICASSO (Oct 2024)** — 5.7% in 2024-02 and
   8.0% in 2024-05, then 25.7% by 2024-11. That dwarfs anything at 2026-02-03.
2. **2026-02 (26.5%) is LOWER than 2025-11 (32.6%).** The quarter immediately
   after the state-input change sits *below* the quarter before it. A crude
   split at 2026-02-03 showed a rise only because it pooled all of 2026-03..07
   against all of 2025-11..2026-01 on a series that was trending up anyway.

**Splitting a trending series at an arbitrary date always produces a "jump".**
That is what ADR-016 actually measured. The season caveat it carried was real
but insufficient — the deeper problem was the trend, which I had no visibility
into until I looked before 2025.

**ADR-007's mechanism is not disproven** — more samples per ISP really should
make exact monotonicity rarer — but it is **not detectable** in this data and
must not be presented as observed. Withdrawn as an empirical claim; retained
only as an untested hypothesis.

### The better-supported mechanism, from TenneT's own text

[IPS61] §3.4, added in v6.1: *"As a result of participation in PICASSO a Dutch
shortage can for example change into a surplus, when the other participants
have a surplus, or vice versa."*

A within-ISP flip from shortage to surplus is **exactly** the condition for
regulation state 2: the balance-delta series both rises and falls. So PICASSO
mechanically manufactures state-2 periods, and the data shows precisely that
at precisely that date. This is primary-sourced and matches a fourfold jump —
far stronger than the ADR-007 speculation ever was.

### Consequence for Phase 2 (this is why it matters)

Pre-PICASSO ISPs are dual-priced ~6% of the time; post-PICASSO 26–40%. A model
trained across that boundary would be badly miscalibrated on P(state 2) — the
quantity that decides whether the risk-aware dispatch policy beats the
deterministic one. **Training is therefore restricted to 2024-10-18 onward**
(~22 months), accepted knowingly as below CLAUDE.md §3's 3-year target, and
recorded in LIMITATIONS.md.

**Method lesson, worth keeping.** The check that caught this was cheap: look
further back than the window you are arguing about. I had 9 months cached and
formed a conclusion; 5 years of one-week-per-quarter samples reversed it for
about twenty API calls.

---

## ADR-023 — Task 5 feature builder: `day_ahead_price` made unconditional; `lag_price_short_1` removed as impossible, availability now enforced per row

**`day_ahead_price` catalogue/column mismatch.** The Task 5 brief's
`build_features` only wrote a `day_ahead_price` column when a `day_ahead`
series was passed, but `CATALOGUE` never listed it — so the mandatory
"built columns match the catalogue exactly" test would fail the moment a
caller omitted `day_ahead`, and `DayAheadBaseline` (`src/models/baselines.py`),
which unconditionally reads `X["day_ahead_price"]`, would `KeyError` whenever
it was.

**Decision.** Added a `day_ahead_price` entry to `CATALOGUE` and made the
column unconditional: `build_features` always emits it, NaN-filled when no
`day_ahead` series is supplied. Chosen over the alternative (keep it
conditional, carve it out of the columns-match test) because it requires no
test weakening, keeps the emitted column set a pure function of the
catalogue with no caller-dependent branching, and gives `DayAheadBaseline` a
column that always exists rather than one that exists only if the caller
remembered to pass day-ahead data.

**Separately — a real, unresolved R1 tension, not this task's bug to fix.**
The Task 5 brief's own "Interfaces" line and its module docstring claim the
builder "asks `data_availability` for permission per field per period, so a
leaking feature is impossible by construction", and lists `assert_available`,
`available_vintages` and a `decision_offset` parameter as consumed. The
brief's actual `build_features` code implements none of this — no
`assert_available` call exists anywhere in the module, and `decision_offset`
was dropped from the signature. This is not an oversight I could silently
patch: wiring `assert_available` in at the canonical ISP-start decision time
(`ADR-005`, restated in `tests/test_no_lookahead.py`) against the catalogue's
current `source_field` choice would raise `LookAheadError` on nearly every
row of `lag_price_short_1`, and on every row before ~10:00 local for
`lag_price_short_96`. Reason: `imbalance_price_settled` publishes once daily
at D+1 10:00 (`config/market_rules.yaml`, which annotates it *"the TARGET
variable, never a feature"*), so a 1-ISP or 1-day shift of it does not
correspond to genuinely-available information at ISP-start decision time —
unlike, say, `imbalance_price_realtime_estimate` (lag_after_period, ~120 s),
which the config explicitly marks *"Usable as a feature."*

This mirrors ADR-021's own conclusion: it already named "point-level
availability... for the Phase 2 feature builder" as a **next step**, not
something already done. Task 6's self-review table nonetheless lists
"Availability enforcement in the builder" as delivered by Task 5 — that line
is aspirational against the brief's own sample code, not yet true against
what ships here.

**Initial resolution (superseded below).** First pass implemented Task 5 as
specified — catalogue metadata only, no per-row enforcement — and left the
tension above open for explicit review rather than silently resolving it in
either direction, per CLAUDE.md §11 ("ask before... making a domain
assumption that materially changes results").

### Follow-up — resolved: `lag_price_short_1` removed, enforcement wired in with NaN masking

The project owner reviewed the finding above, verified it directly against
`data_availability`, and confirmed: **`lag_price_short_1` can never be
available.** Settled prices publish D+1 10:00, so at ISP-start decision time
no settled value from the target period's own delivery day exists yet, at
any lag shorter than "yesterday, and only after ~10:00 local." Their
measurement:

```
decision = ISP start        t-1     t-96(1d)   t-192(2d)   t-672(7d)
2026-06-17 06:00 UTC         no        no         YES         YES
2026-06-17 12:00 UTC         no       YES         YES         YES
yesterday-same-ISP available for 55/96 ISPs of a day (57%)
```

**Catalogue changes.**
- `lag_price_short_1` and `lag_spread_1` **removed**, not deprecated — an
  impossible feature left in place invites someone to "fix" the enforcement
  around it instead of removing it.
- `lag_price_short_192` **added** (settled price two days back, same ISP):
  always available regardless of decision-time hour-of-day, since D+1 10:00
  settlement of a two-day-old period is always in the past by the time any
  ISP of the current day starts.
- `lag_price_short_freshest` **added**: `lag_price_short_96` where available,
  else `lag_price_short_192` (which always is). This is the actual "last
  observed value" a real decision has access to, and is now what
  `PersistenceBaseline` reads (`src/models/baselines.py`) — its docstring now
  states plainly that "last observed" means 1-2 days old, a property of this
  market's settlement mechanics, not a baseline weakness.
- `lag_price_short_96` and `lag_price_short_672` kept (already legitimately
  laggy enough to often clear the settlement rule). `lag_spread_1` replaced
  by `lag_spread_96`, masked the same way.

**Enforcement.** `build_features` now calls
`src.data.data_availability.is_available(field, target_isp_of_the_lagged_value,
decision_time=ISP_start)` per row for every settled-price-derived lag, via a
new `_availability_mask`/`_masked_lag` pair in `src/features/builder.py`, and
sets the value to NaN where unavailable — it does **not** raise, since
non-availability is a legitimate per-row state (~43-45% of rows for the
1-day lags in the measurements below), not an error. A catalogue entry naming
a field `data_availability` refuses outright (`UnresolvedLagError`, e.g. an
unmeasured lag) is left to propagate rather than caught, since that is a
genuine catalogue bug, not a per-row state.

**Availability rates measured**, 30 days of synthetic ISPs (2,880 rows,
`day_ahead` supplied so that column gets a fair test too):

| Column | Available | Rate | Note |
|---|---|---|---|
| `lag_price_short_96` | 1,595 / 2,880 | 55.4% (57.3% of eligible rows once the 96-row lead-in is excluded) | Matches the owner's measured 57% exactly |
| `lag_spread_96` | 1,595 / 2,880 | 55.4% | Same mask as above (same field, same lag) |
| `lag_price_short_192` | 2,688 / 2,880 | 93.3% | Shortfall is purely the 192-row lead-in (`2880-192=2688`); always available once there is enough history |
| `lag_price_short_672` | 2,208 / 2,880 | 76.7% | Shortfall is purely the 672-row (7-day) lead-in (`2880-672=2208`); always available once there is enough history |
| `lag_price_short_freshest` | 2,743 / 2,880 | 95.2% | NaN only inside the 192-row lead-in, and even there recovers via `lag_price_short_96` wherever that clears the settlement rule |
| calendar (`hour_sin`/`hour_cos`/`dow_sin`/`dow_cos`/`hour`/`dayofweek`) | 2,880 / 2,880 | 100% | Pure functions of the timestamp; never masked |
| `day_ahead_price` | 2,880 / 2,880 | 100% | Published D-1 13:00 for the whole of day D, so always available by any ISP of day D; here because a `day_ahead` series was supplied |

`lag_price_short_192`/`_672`/`freshest` never fall below 100% for a *reason
other than* insufficient lead-in history in a finite synthetic window — on
real cached data (26,208+ ISPs) the lead-in cost is one-time and negligible.

**Do not change `src/data/data_availability.py`.** Confirmed: it is correct
and mutation-tested; the feature design (source field choice, not the
enforcement layer) was what was wrong, and this fix changes only the
catalogue and the builder that consumes it.

Test coverage: `tests/test_feature_builder.py::test_no_catalogued_feature_is_ever_unavailable_for_every_row`
(with a deviation from the literal test as proposed — `day_ahead` is supplied
so `day_ahead_price` gets a fair chance to be non-NaN, since an all-NaN
column from an *omitted optional input* is a different, non-bug condition
from an *impossible* feature, which is what this test is meant to catch),
`::test_same_day_settled_price_is_never_used`,
`::test_yesterdays_price_is_masked_before_the_settlement_run`.

## ADR-024 — encode the observed MAX lag, not the p95; and the lag is not constant

**Two corrections to ADR-019/ADR-020, both from a 60-hour measurement.**

### 1. The lag varies by time of day

ADR-020 called the lag "a deterministic configured constant" on the strength of
630 samples spanning 2.18 hours of one afternoon (range: 1.0 second). A 974-sample,
60.1-hour run says otherwise:

| Hours (UTC) | median | max |
|---|---|---|
| 12:00–14:00 | 133.5 s | 134.0 s |
| 15:00–17:00, 21:00–00:00 | 121.5 s | 122.0 s |
| 23:00 | 121.7 s | **145.8 s** |

The two clusters differ by **12.0 s — exactly one publication cadence tick**.
That points at poll-phase aliasing (our documented 0–12 s upward bias) rather
than two TSO settings, and the 23:00 outlier at 145.8 s is two ticks. Either
way it is measurement noise in the conservative direction, so it is treated as
noise and not as a signal about TenneT's configuration.

**The method lesson repeats ADR-022's:** a tight distribution inside a short
window is not evidence of stability outside it. Two hours said "constant";
sixty hours said otherwise.

### 2. p95 was the wrong statistic, and that was my instruction

ADR-019 and the Phase 1 plan both said to encode the **p95**, reasoning that a
median "would grant look-ahead on half the observations". Correct about the
median, one step short of the right conclusion.

**R1 is asymmetric.** Understating the lag claims data was retrievable earlier
than it was — look-ahead, on exactly the fraction of rows in the upper tail.
Overstating it only withholds signal. Measured against the 974-sample set:

| Encoded | Rows with a longer true lag | |
|---|---|---|
| median (133.2 s) | 486 / 974 | **49.9% leak** |
| p95 (133.9 s) | 48 / 974 | **4.9% leak** |
| p99 (134.0 s) | 9 / 974 | 0.9% leak |
| **max (145.8 s)** | **0 / 974** | **0%** |

The previously-encoded 134 s would have leaked on about **1 row in 20** — not
catastrophic, but exactly the kind of quiet, flattering error this project
exists to avoid, and invisible in any result.

**Decision: encode the observed maximum, rounded up. `lag_seconds: 146`.**
Re-encode upward if a longer tail ever appears; never downward without a
larger sample than the one that produced the current value.

The test no longer hard-codes a number. It asserts
`lag_seconds >= lag_measurement.max_seconds`, so the encoded value is pinned to
its own evidence and cannot silently drift below it.

### Still unobserved

Hours 01:00–11:00 and 18:00–20:00 UTC, weekends, and scarcity periods. The
coverage caveat in `config/market_rules.yaml` stands.
