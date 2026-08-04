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

**Rigour-zone note.** `tests/test_settlement.py` (21 tests) is longer than the
40 lines of logic it covers. Ponytail's ladder would flag that. Declined: the
invariant it protects is that every euro in this project is settled with the
correct sign, and the property test (`price_short >= price_long` across all
states and price combinations) catches an inverted rule table that
example-based tests would miss. Expected values are hand-worked from [IPS6]
Table 2, not recorded from the implementation's output.

## ADR-005 — Decision timestamp set at ISP start (provisional)

Backtest information cutoff for target ISP `t` is
`start(t) − lag(field, t)`. See `docs/DOMAIN_NOTES.md` Q9 for the argument and
what it excludes (intra-ISP re-decision). **Provisional — awaiting review.**
This is a §11 "ask before" item: it materially changes results.

## ADR-006 — Publication lag is a function of time, not a constant

**Context.** TenneT changed the balance-delta publication delay at least three
times between late 2024 and late 2025 (3 → 5 → 2 minutes), explicitly to alter
how aggressively market parties passively balance.

**Decision.** `publication.balance_delta.time_varying: true` in the config, and
`data_availability.available_at(field, target_period)` must resolve the lag *in
force at `target_period`*, not today's.

**Why.** A constant 2-minute lag applied to 2024 data hands the strategy
information that did not exist then — and inflates revenue precisely in the
periods TenneT's intervention was designed to make less profitable. This is a
look-ahead violation (R1) that would not look like one in code review.

**Caveat.** The dates are SECONDARY-sourced and unverified; `tennet.eu` blocks
automated fetching. Flagged in DOMAIN_NOTES Q7 as a blocking item.
