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
