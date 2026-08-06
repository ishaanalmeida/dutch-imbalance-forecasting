"""Render docs/DATA_QUALITY.md from cached processed data.

Orchestration only -- the diagnostics themselves (the rigour zone) live in
src/data/quality.py. This script's own rigour is narrower and simpler: R3
("never fabricate a number") means it must refuse to write anything rather
than emit an empty-but-formatted report when no data is cached yet. An empty
report reads as "zero data-quality issues found"; the honest statement when
there is no data at all is "not yet measured", which is what
docs/DATA_QUALITY.md says as a placeholder until this script actually runs
against real cached data.

STATUS 2026-08-06: no ENTSO-E token or TenneT registration exists yet (see
docs/DATA_SOURCES.md), so nothing is cached under data/processed/ and running
this script today exits via the guard below rather than writing the doc.

    uv run python scripts/build_quality_report.py [--dataset NAME]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# Run directly as `python scripts/build_quality_report.py`, Python puts this
# file's own directory (not the repo root) on sys.path[0], so `src` would not
# import. Insert the repo root explicitly rather than requiring callers to
# know to use `python -m scripts.build_quality_report` instead.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.data.cache import read_frame
from src.data.quality import (
    duplicate_report,
    gap_report,
    regulation_state_distribution,
    structural_break_check,
)

OUT = Path(__file__).resolve().parents[1] / "docs" / "DATA_QUALITY.md"

# read_frame filters to [start, end); this window is deliberately wide enough
# to catch "all cached history" without needing to know it in advance.
_WIDE_START = datetime(2000, 1, 1, tzinfo=UTC)
_WIDE_END = datetime(2100, 1, 1, tzinfo=UTC)


def _body(df: pd.DataFrame) -> str:
    return "_none_" if df.empty else f"```\n{df.to_string(index=False)}\n```"


def _section(title: str, df: pd.DataFrame) -> str:
    return f"## {title}\n\n{_body(df)}\n"


def build(dataset: str = "imbalance_prices") -> None:
    """Read `dataset` from the processed cache and (re)write docs/DATA_QUALITY.md.

    Raises SystemExit without touching the doc if nothing is cached -- see
    module docstring on why an empty report is worse than no report (R3).
    """
    df = read_frame(dataset, _WIDE_START, _WIDE_END)
    if df.empty:
        raise SystemExit(
            f"No cached data found for dataset {dataset!r} under data/processed/. "
            "Run the fetchers first, then re-run this script. Refusing to "
            "overwrite docs/DATA_QUALITY.md from an empty frame -- see "
            "CLAUDE.md R3 and this script's module docstring."
        )

    sections = [
        "# Data Quality Report\n",
        f"_Generated {datetime.now(UTC).isoformat()} from dataset `{dataset}`, "
        f"{len(df)} cached rows._\n",
        _section("Gaps", gap_report(df)),
        _section("Duplicates", duplicate_report(df)),
    ]
    if "regulation_state" in df.columns:
        for by in ("hour", "month", "year"):
            dist = regulation_state_distribution(df, by=by).reset_index()
            sections.append(_section(f"Regulation state distribution by {by}", dist))
        breaks = structural_break_check(df)
        sections.append(
            "## Structural breaks -- state-2 share before/after\n\n"
            "ADR-007 (docs/DECISIONS.md) predicts state-2 share RISES at "
            "2026-02-03 with no change in the physical system. Reported "
            "plainly below whichever way it goes; if it did not rise, "
            "DOMAIN_NOTES.md Q7's reasoning is wrong and must be corrected.\n\n"
            f"{_body(breaks)}\n"
        )
    else:
        sections.append(
            "## Regulation state distribution / structural breaks\n\n"
            "_skipped: no `regulation_state` column in this dataset_\n"
        )

    OUT.write_text("\n".join(sections), encoding="utf-8")
    print(f"wrote {OUT}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default="imbalance_prices",
        help="processed-cache dataset name to read (default: imbalance_prices)",
    )
    args = parser.parse_args()
    build(dataset=args.dataset)


if __name__ == "__main__":
    main()
