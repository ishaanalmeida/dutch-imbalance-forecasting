"""Backfill imbalance prices / day-ahead prices from ENTSO-E over a date range.

Lean zone: this is an occasional operator command, not a scheduled job (see
src/jobs/ for that) -- run it by hand when the cache has a gap.

Month-chunked and idempotent: `cache.write_frame` replaces a month wholesale,
so re-running this is always safe, and a chunk already close to fully cached
is skipped rather than re-fetched. A one-second delay between requests is the
only rate-limit handling this needs -- ENTSO-E's public limit (400 req/min)
is far above what a month-chunked backfill of a couple of years costs.

Usage:
    uv run python -m scripts.backfill_history imbalance 2024-10-18 2025-12-01
    uv run python -m scripts.backfill_history day_ahead 2024-10-18 2026-08-01
    uv run python -m scripts.backfill_history both 2024-10-18 2026-08-01 --force
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

import pandas as pd
from entsoe.exceptions import NoMatchingDataError

from src.data import cache, entsoe
from src.env import load_env

_DELAY_SECONDS = 1.0
_COVERAGE_THRESHOLD = 0.95


def _chunks(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Calendar-month-aligned chunks covering [start, end), first and last
    clipped to the requested range."""
    month_starts = [
        ts.to_pydatetime()
        for ts in pd.date_range(start, end, freq="MS", tz="UTC")
        if start < ts.to_pydatetime() < end
    ]
    boundaries = sorted({start, end, *month_starts})
    return list(zip(boundaries, boundaries[1:], strict=False))


def _already_covered(dataset: str, start: datetime, end: datetime, resolution_minutes: int) -> bool:
    existing = cache.read_frame(dataset, start, end)
    expected = (end - start).total_seconds() / (resolution_minutes * 60)
    return len(existing) >= _COVERAGE_THRESHOLD * expected


def _backfill(
    label: str,
    dataset: str,
    resolution_minutes: int,
    fetch: Callable[[datetime, datetime], pd.DataFrame | pd.Series[float]],
    start: datetime,
    end: datetime,
    force: bool,
) -> None:
    for chunk_start, chunk_end in _chunks(start, end):
        if not force and _already_covered(dataset, chunk_start, chunk_end, resolution_minutes):
            print(f"[{label}] {chunk_start:%Y-%m-%d}..{chunk_end:%Y-%m-%d}: already covered, skip")
            continue
        print(f"[{label}] {chunk_start:%Y-%m-%d}..{chunk_end:%Y-%m-%d}: fetching...", end=" ")
        try:
            result = fetch(chunk_start, chunk_end)
        except NoMatchingDataError:
            print("no data published for this window")
        except Exception as exc:
            # A single bad chunk (transient network error, an unpublished
            # window) must not abort a multi-month backfill -- report and
            # keep going, the chunk stays un-covered and a re-run retries it.
            print(f"FAILED: {exc!r}")
        else:
            print(f"{len(result)} rows")
        time.sleep(_DELAY_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=["imbalance", "day_ahead", "both"])
    parser.add_argument("start", help="YYYY-MM-DD, UTC")
    parser.add_argument("end", help="YYYY-MM-DD, UTC, exclusive")
    parser.add_argument(
        "--force", action="store_true", help="re-fetch chunks even if already cached"
    )
    args = parser.parse_args(argv)

    load_env()
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)

    if args.dataset in ("imbalance", "both"):
        _backfill(
            "imbalance",
            "imbalance_prices",
            15,
            entsoe.fetch_imbalance_prices,
            start,
            end,
            args.force,
        )
    if args.dataset in ("day_ahead", "both"):
        _backfill(
            "day_ahead",
            "day_ahead_price",
            60,
            entsoe.fetch_day_ahead_prices,
            start,
            end,
            args.force,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
