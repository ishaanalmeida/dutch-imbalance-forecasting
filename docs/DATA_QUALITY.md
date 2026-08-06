# Data Quality Report

TODO: not yet measured.

No ENTSO-E API token or TenneT data-access registration exists yet (see
`docs/DATA_SOURCES.md`), so nothing is cached under `data/processed/`. Per
CLAUDE.md R3 ("never fabricate a number"), this file deliberately carries no
numbers rather than a plausible-looking placeholder — an empty-but-formatted
report would read as "zero data-quality issues found", which is a different
and false claim from "no data has been fetched".

The diagnostic functions this report will be built from
(`gap_report`, `duplicate_report`, `regulation_state_distribution`,
`structural_break_check`) are implemented and unit-tested against synthetic
frames in `src/data/quality.py` / `tests/test_quality.py`. In particular,
`structural_break_check` is wired to test the ADR-007 prediction
(`docs/DECISIONS.md`): that the share of regulation state 2 (the only
dual-priced state) should **rise** at 2026-02-03, when TenneT switched the
regulation-state determination input from the 1-minute to the 12-second
balance delta, with no change to the physical system. If it does not rise,
that is reported here plainly and `docs/DOMAIN_NOTES.md` Q7's reasoning must
be corrected.

Regenerate this file once data is cached:

```
uv run python scripts/build_quality_report.py [--dataset NAME]
```

The script refuses to overwrite this file from an empty cache (same R3
reasoning) — see its module docstring for details.
