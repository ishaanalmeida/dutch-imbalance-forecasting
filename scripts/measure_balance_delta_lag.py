"""Measure the balance-delta publication lag empirically.

ADR-006: the widely-cited 3/5/2-minute delay timeline could not be
substantiated from TenneT's public pages, so the lag is `null` in
`config/market_rules.yaml` and `data_availability` refuses to serve the field.

**Update 2026-08-07 — a delay demonstrably exists.** TenneT's own API
documentation for `Balance Delta High Res` states that responses "return the
most recent available 30 minutes, **subject to a configurable delay imposed by
TenneT**". So the delay is real and is a knob TenneT turns; its *current value*
is still not published anywhere we can read. That is exactly what this script
measures, and "configurable" is why the measurement must be repeatable rather
than done once.

METHOD. Poll `/latest` and, for each observation not seen before, record the
instant it describes and the wall-clock instant we could first see it. The lag
is the difference. Report median, p95 and max: **p95, not median, is the honest
number for R1** -- a median lag would grant look-ahead on half the observations.

RATE LIMITS (from TenneT's published API docs; exceeding them can get the key
blocked):

    /balance-delta-high-res/latest   1 req/sec, 10 req/min
    /balance-delta-high-res          8 req/DAY, max 4-hour window

TenneT's recommended cadence is five polls per minute, one second after each
12-second refresh event -- at :13, :25, :37, :49 and :01 of the next minute.
This script follows that exactly, which is 5 req/min, comfortably inside the
10/min cap. Do not "improve" it by polling faster.

    uv run python scripts/measure_balance_delta_lag.py --probe      # one call, dump shape
    uv run python scripts/measure_balance_delta_lag.py --minutes 120
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

# Runnable as `python scripts/measure_balance_delta_lag.py` as well as
# `python -m scripts.measure_balance_delta_lag`. The former does not put the
# repo root on sys.path (pythonpath in pyproject.toml is a pytest setting, not
# a runtime one), so `from src.env import ...` would fail. Fix it here rather
# than making the operator remember which invocation works.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

OUT = Path(__file__).resolve().parents[1] / "data" / "interim" / "balance_delta_lag_samples.jsonl"

BASE_URL = "https://api.tennet.eu"
LATEST_PATH = "/publications/v1/balance-delta-high-res/latest"

# CONFIRMED 2026-08-07 from the spec page's "Authorize" dialog:
#   scheme: apikey (apiKey)   name: apikey   in: header
# Azure API Management fronts this API (its 403 is served by
# Microsoft-Azure-Application-Gateway), but TenneT has renamed the subscription
# header from Azure's default `Ocp-Apim-Subscription-Key` to plain `apikey`.
# Assuming the Azure default would have produced a 401 that looked like a bad
# key rather than a bad header name. Overridable in case it changes.
KEY_HEADER = os.getenv("TENNET_API_KEY_HEADER", "apikey")

# TenneT's recommended poll offsets within each minute: one second after each
# 12-second data-refresh event.
POLL_OFFSETS = (1, 13, 25, 37, 49)
MAX_REQUESTS_PER_MINUTE = 10


class MissingKeyError(RuntimeError):
    """TENNET_API_KEY is not set."""


def _require_key() -> str:
    key = os.getenv("TENNET_API_KEY")
    if not key:
        raise MissingKeyError(
            "TENNET_API_KEY is not set. Register at https://developer.tennet.eu/register/, "
            "obtain a subscription key, and add it to .env (which is gitignored). "
            "See docs/DATA_SOURCES.md."
        )
    return key


def fetch_latest_balance_delta(timeout: float = 30.0) -> dict[str, Any]:
    """One call to `/latest`. Returns the decoded JSON payload verbatim.

    Deliberately does not interpret the payload: the response schema has not
    been seen, and guessing field names is exactly the kind of fabrication R3
    forbids. Run `--probe` to dump the real shape, then teach `extract_records`
    about it.
    """
    response = httpx.get(
        f"{BASE_URL}{LATEST_PATH}",
        headers={KEY_HEADER: _require_key(), "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def extract_records(payload: Any) -> list[dict[str, Any]]:
    """Pull the 12-second observations out of TenneT's Aether envelope.

    Schema confirmed live 2026-08-07:

        Response
          informationType: "BALANCE_DELTA_HIGH_RES"
          period.timeInterval: {start, end}      # the ~30-minute window
          TimeSeries[]
            Period[]
              points[]                           # one per 12 seconds
                timeInterval_start / timeInterval_end
                sequence
                power_{afrr,igcc,mfrrda,picasso,mari}_{in,out}
                max_upw_regulation_price         # p_up   (null when no upward)
                min_downw_regulation_price       # p_down (null when no downward)
                mid_price                        # p_mid

    Each point's `timestamp` is its **timeInterval_end**: the point covers
    [start, end), so the observation is only complete at `end`. Using `start`
    would overstate the measured lag by one 12-second tick.

    Raises on an unrecognised envelope rather than returning nothing -- an
    empty result reads as "no new data" and would silently yield a lag
    measurement of zero samples.
    """
    if not isinstance(payload, dict) or "Response" not in payload:
        raise ValueError(
            f"Unexpected envelope; expected a top-level 'Response'. "
            f"Got: {sorted(payload) if isinstance(payload, dict) else type(payload).__name__}"
        )
    response = payload["Response"]

    out: list[dict[str, Any]] = []
    for series in response.get("TimeSeries", []) or []:
        for period in series.get("Period", []) or []:
            for point in period.get("points", []) or []:
                stamp = point.get("timeInterval_end")
                if not stamp:
                    raise ValueError(f"point has no timeInterval_end. Keys: {sorted(point)}")
                out.append({"timestamp": str(stamp), "record": point})

    if not out:
        raise ValueError(
            "Envelope parsed but contained no points. Window: "
            f"{response.get('period.timeInterval')}"
        )
    return out


def _parse_instant(stamp: str) -> datetime:
    """TenneT states all `/latest` timestamps are UTC."""
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def probe() -> int:
    """One request; print the response shape so the parser can be written."""
    payload = fetch_latest_balance_delta()
    print(json.dumps(payload, indent=2)[:4000])
    print("\n--- shape ---")
    if isinstance(payload, dict):
        print("top-level keys:", sorted(payload))
    try:
        records = extract_records(payload)
        print(f"records found: {len(records)}")
        if records:
            print("first record:", json.dumps(records[0]["record"], indent=2)[:1000])
            lag = (datetime.now(UTC) - _parse_instant(records[0]["timestamp"])).total_seconds()
            print(f"\napparent lag of newest record: {lag:.1f} s")
    except ValueError as exc:
        print(f"extract_records could not parse it: {exc}")
        return 1
    return 0


def poll(minutes: int) -> int:
    """Poll `/latest` and record, for each newly published point, how long
    after the instant it describes we could first see it.

    WARM-UP MATTERS. Each response carries the most recent ~30 minutes, i.e.
    ~150 points. On the very first call every one of those is "new" to us, but
    almost all were published long before we started looking -- their apparent
    lag would range up to 30 minutes and would wreck the p95. So the first
    response is used ONLY to seed the seen-set; no samples are emitted from it.
    Only points that appear in a LATER response were genuinely published while
    we were watching.

    Residual measurement noise: we poll every ~12 s, so a point can sit
    published for up to one poll interval before we notice. Measured lags are
    therefore biased UP by 0..12 s. That is the conservative direction for R1
    (it never makes data look available earlier than it was), and it is small
    against a lag of order two minutes -- but it is reported, not hidden.
    """
    OUT.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + minutes * 60
    seen: set[str] = set()
    written = 0
    warmed_up = False
    requests_this_minute = 0
    minute_mark = int(time.time() // 60)

    print(f"polling {LATEST_PATH} at seconds {POLL_OFFSETS} of each minute -> {OUT}")
    print("first response is warm-up only (its backlog was published before we looked)")

    while time.time() < deadline:
        now = datetime.now(UTC)
        target = next((o for o in POLL_OFFSETS if o > now.second), None)
        time.sleep((target - now.second) if target else (60 - now.second + POLL_OFFSETS[0]))

        current_minute = int(time.time() // 60)
        if current_minute != minute_mark:
            minute_mark, requests_this_minute = current_minute, 0
        if requests_this_minute >= MAX_REQUESTS_PER_MINUTE:
            continue  # never exceed TenneT's published cap

        observed_at = datetime.now(UTC)
        try:
            payload = fetch_latest_balance_delta()
            requests_this_minute += 1
        except httpx.HTTPError as exc:
            print(f"\n  request failed: {exc}")
            continue

        try:
            records = extract_records(payload)
        except ValueError as exc:
            print(f"\n  unparseable response: {exc}")
            continue

        if not warmed_up:
            seen.update(r["timestamp"] for r in records)
            warmed_up = True
            print(f"  warm-up: {len(seen)} existing points ignored")
            continue

        with OUT.open("a", encoding="utf-8") as fh:
            for item in records:
                stamp = item["timestamp"]
                if stamp in seen:
                    continue
                seen.add(stamp)
                lag = (observed_at - _parse_instant(stamp)).total_seconds()
                fh.write(
                    json.dumps(
                        {
                            "describes": stamp,
                            "first_seen": observed_at.isoformat(),
                            "lag_seconds": lag,
                        }
                    )
                    + "\n"
                )
                written += 1
        print(f"  {observed_at:%H:%M:%S}  new samples: {written}", end="\r")

    print(f"\nwrote {written} samples to {OUT}")
    return 0 if written else 1


def summarise() -> int:
    if not OUT.exists():
        print(f"no samples at {OUT}")
        return 1
    lags = [
        json.loads(line)["lag_seconds"] for line in OUT.read_text(encoding="utf-8").splitlines()
    ]
    if not lags:
        print("no samples recorded")
        return 1
    lags.sort()
    p95 = lags[min(int(0.95 * len(lags)), len(lags) - 1)]
    print(f"samples {len(lags)}")
    print(f"median  {lags[len(lags) // 2]:.1f} s")
    print(f"p95     {p95:.1f} s   <-- write THIS (rounded up) to market_rules.yaml")
    print(f"max     {lags[-1]:.1f} s")
    print("\np95, not median: a median lag would grant look-ahead on half the observations.")
    return 0


def burst(burst_minutes: int, every_minutes: int, hours: float) -> int:
    """Duty-cycled sampling: poll for `burst_minutes`, sleep, repeat.

    Why not just poll continuously for 24 hours. The open question about the
    lag is whether it VARIES BY TIME OF DAY -- overnight, at weekends, during
    scarcity -- not whether it is stable minute to minute, which the first run
    already answered (630 samples spanning 1.0 second).

    Continuous polling for 24 h is ~7,200 requests. TenneT's support FAQ says
    there are limits "per second, per hour and per day" and that they differ
    per API; the Balance Delta spec publishes only per-second and per-minute,
    so an hourly or daily cap may exist that we cannot see. Exceeding it "may
    result in temporary blocking of your API keys" -- which would cost far more
    than the extra samples are worth.

    Ten minutes per hour gives ~50 samples in each hourly bucket, which is
    ample to detect a step change in a quantity whose within-window spread is
    one second, at a sixth of the request volume.
    """
    end = time.time() + hours * 3600
    cycle = 0
    while time.time() < end:
        cycle += 1
        print(f"\n--- burst {cycle} @ {datetime.now(UTC):%H:%M}Z ---")
        poll(burst_minutes)
        idle = max(0.0, (every_minutes - burst_minutes) * 60)
        if time.time() + idle >= end:
            break
        time.sleep(idle)
    return summarise()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="one request, dump the shape")
    parser.add_argument("--summarise", action="store_true", help="stats from existing samples")
    parser.add_argument("--minutes", type=int, default=120)
    parser.add_argument(
        "--burst-hours",
        type=float,
        help="duty-cycled run over this many hours (for time-of-day coverage)",
    )
    parser.add_argument("--burst-minutes", type=int, default=10)
    parser.add_argument("--every-minutes", type=int, default=60)
    args = parser.parse_args(argv)

    from src.env import load_env

    load_env()

    if args.probe:
        return probe()
    if args.summarise:
        return summarise()
    if args.burst_hours:
        return burst(args.burst_minutes, args.every_minutes, args.burst_hours)
    return poll(args.minutes)


if __name__ == "__main__":
    raise SystemExit(main())
