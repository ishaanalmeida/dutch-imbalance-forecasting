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
