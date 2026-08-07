"""Scheduled job: record the weather forecast as it stands right now.

CLAUDE.md §7 on the live logging job: *"it accrues value with wall-clock time
and nothing else does."* This is the earliest piece of that job that can run,
because Open-Meteo needs no credentials. The imbalance-price half is added in
Phase 2 once a model exists, and the ENTSO-E half once a token arrives.

What it buys, concretely:

- **A vintage history that cannot be reconstructed.** Open-Meteo serves the
  current forecast only. Every run that does not happen is a vintage that
  never existed.
- **The forecast-error proxy CLAUDE.md §4 asks for.** The difference between
  successive vintages of the same target hour is a legitimate, non-leaking
  leading indicator of imbalance — available only because both were kept.
- **An unfalsifiable timestamp.** Records written by a scheduled run, committed
  as they were produced, cannot be back-fitted later.

Run it as often as the forecast updates (Open-Meteo refreshes roughly hourly);
four times a day is enough to capture the vintage structure that matters.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from src.data.openmeteo import NL_LAT, NL_LON, fetch_live_forecast
from src.data.vintage import append_vintage

DATASET = "weather_forecast"


def run(
    forecast_days: int = 7,
    latitude: float = NL_LAT,
    longitude: float = NL_LON,
    observed_at: datetime | None = None,
) -> int:
    """Fetch the current forecast and record it as one vintage.

    Returns the number of target periods recorded. ``observed_at`` defaults to
    now and exists so tests can pin it; production should not pass it.
    """
    observed_at = observed_at or datetime.now(UTC)
    frame = fetch_live_forecast(forecast_days=forecast_days, latitude=latitude, longitude=longitude)
    append_vintage(DATASET, frame, observed_at)
    return len(frame)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast-days", type=int, default=7)
    parser.add_argument("--latitude", type=float, default=NL_LAT)
    parser.add_argument("--longitude", type=float, default=NL_LON)
    args = parser.parse_args(argv)

    observed_at = datetime.now(UTC)
    rows = run(
        forecast_days=args.forecast_days,
        latitude=args.latitude,
        longitude=args.longitude,
        observed_at=observed_at,
    )
    print(f"recorded {rows} target periods, observed_at={observed_at.isoformat()}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
