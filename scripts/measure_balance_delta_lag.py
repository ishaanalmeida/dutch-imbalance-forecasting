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
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

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
    """Pull observations out of the payload, each needing a timestamp field.

    Handles the common envelope shapes (bare list, or a list under a single
    obvious key) and searches each record for a plausible timestamp field.
    Raises with the actual payload shape if it cannot find one, rather than
    returning nothing -- an empty result would look like "no new data" and
    silently produce a lag measurement of zero samples.
    """
    records: list[Any]
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        lists = [v for v in payload.values() if isinstance(v, list)]
        if len(lists) != 1:
            raise ValueError(
                f"Cannot locate the record list in the response. Top-level keys: "
                f"{sorted(payload)}. Run --probe and update extract_records()."
            )
        records = lists[0]
    else:
        raise ValueError(f"Unexpected payload type {type(payload).__name__}. Run --probe.")

    out: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        stamp = next(
            (
                str(v)
                for k, v in record.items()
                if any(t in k.lower() for t in ("time", "stamp", "date", "period"))
                and isinstance(v, str)
            ),
            None,
        )
        if stamp is None:
            raise ValueError(
                f"No timestamp-like field in record. Keys: {sorted(record)}. "
                f"Run --probe and update extract_records()."
            )
        out.append({"timestamp": stamp, "record": record})
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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + minutes * 60
    seen: set[str] = set()
    written = 0
    requests_this_minute = 0
    minute_mark = int(time.time() // 60)

    print(f"polling {LATEST_PATH} at seconds {POLL_OFFSETS} of each minute -> {OUT}")
    while time.time() < deadline:
        now = datetime.now(UTC)
        # Sleep until the next recommended offset.
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
            print(f"  request failed: {exc}")
            continue

        with OUT.open("a", encoding="utf-8") as fh:
            for item in extract_records(payload):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="one request, dump the shape")
    parser.add_argument("--summarise", action="store_true", help="stats from existing samples")
    parser.add_argument("--minutes", type=int, default=120)
    args = parser.parse_args(argv)

    from src.env import load_env

    load_env()

    if args.probe:
        return probe()
    if args.summarise:
        return summarise()
    return poll(args.minutes)


if __name__ == "__main__":
    raise SystemExit(main())
