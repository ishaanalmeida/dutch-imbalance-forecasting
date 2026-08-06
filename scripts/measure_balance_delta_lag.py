"""Measure the balance-delta publication lag empirically.

ADR-006 (docs/DECISIONS.md): the widely-cited 3/5/2-minute delay timeline
could not be substantiated and may confuse cadence with delay. Rather than
trust any document, poll the live endpoint and observe.

STATUS 2026-08-06: a registered-access route to TenneT's data now exists at
developer.tennet.eu ("Balance Delta High Res"), but registration is the repo
owner's action (identity + legal terms) and has not been performed -- see
docs/DATA_SOURCES.md "TenneT access". The endpoint path, auth mechanism, query
parameters and response schema are behind that registration and have not been
seen (R3: nothing below is a guess at them). `fetch_latest_balance_delta`
below is the single place that gap lives; it raises NotImplementedError naming
exactly what is missing. Everything else -- the poll loop, the sample
schema, the CLI -- is written and ready to run the moment that one function is
filled in from the spec.

Run for at least 2 hours to span several ISPs, then write the result into
config/market_rules.yaml WITH the sample size and observation window (see
task-7-brief.md Step 5). Report the median, p95 and max lag: the p95, not the
median, is the honest number for R1, because using the median would grant
look-ahead on half the observations.

    uv run python scripts/measure_balance_delta_lag.py --minutes 120 --poll-seconds 5
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Repo-root-relative, matching src/data/cache.py's DATA_ROOT convention so the
# output lands in the same place regardless of the caller's cwd.
OUT = Path(__file__).resolve().parents[1] / "data" / "interim" / "balance_delta_lag_samples.jsonl"


def fetch_latest_balance_delta() -> list[dict[str, Any]]:
    """Fetch the most recently published balance-delta observation(s).

    UNIMPLEMENTED. To fill this in after registering at
    https://developer.tennet.eu/register/:

    1. Read the "Balance Delta High Res" spec at
       https://developer.tennet.eu/specs/<api-name> (unseen until login).
    2. Fill in: the request URL, the auth header/token mechanism, and any
       required query parameters.
    3. Parse the response into a list of dicts, each with at minimum a
       `"timestamp"` key holding an ISO-8601 instant (UTC) describing the
       period the observation covers -- that is the only field `poll()` reads.
    4. Delete the `raise NotImplementedError` below.

    Must raise, never return a placeholder: a silently-empty or fabricated
    result here would let an unmeasured lag look measured.
    """
    raise NotImplementedError(
        "TenneT 'Balance Delta High Res' access is unregistered and its spec "
        "unseen. Register at https://developer.tennet.eu/register/, read the "
        "spec at https://developer.tennet.eu/specs/<api-name>, then implement "
        "the HTTP call (endpoint URL, auth header, query params) and response "
        "parsing in fetch_latest_balance_delta() -- see this function's "
        "docstring and docs/DATA_SOURCES.md 'TenneT access'."
    )


def poll(minutes: int, poll_seconds: int, out_path: Path = OUT) -> None:
    """Poll `fetch_latest_balance_delta` for `minutes`, appending one JSON
    object per newly-seen observation to `out_path`: the instant it
    describes, the wall-clock instant we first saw it, and their difference.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + minutes * 60
    seen: set[str] = set()
    with out_path.open("a", encoding="utf-8") as fh:
        while time.time() < deadline:
            observed_at = datetime.now(UTC)
            for point in fetch_latest_balance_delta():
                stamp = point["timestamp"]
                if stamp in seen:
                    continue
                seen.add(stamp)
                lag = (observed_at - datetime.fromisoformat(stamp)).total_seconds()
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
                fh.flush()
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--minutes", type=int, default=120, help="how long to poll for (default: 120)"
    )
    parser.add_argument(
        "--poll-seconds", type=int, default=5, help="seconds between polls (default: 5)"
    )
    args = parser.parse_args()
    poll(minutes=args.minutes, poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
