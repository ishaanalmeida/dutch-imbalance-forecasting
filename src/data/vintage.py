"""Append-only vintage store: what a revised series *said*, when it said it.

RIGOUR ZONE (CLAUDE.md §12) — ponytail simplification does not apply here and
`ponytail:` shortcut comments are prohibited.

Six of the ten series in `config/market_rules.yaml` are `revised: true`: load
forecasts, wind/solar forecasts, actual generation, cross-border flows and the
real-time imbalance estimate are all restated after first publication. R1
requires the *first-published* vintage, but ENTSO-E and Open-Meteo both serve
only the current one. So a history pulled today is not what was visible then,
and no amount of later effort recovers the difference.

This store is the fix, and it only works forward in time: every observation is
kept with the instant it was observed, nothing is ever overwritten, and
`latest_as_of` reconstructs exactly what a decision-maker could have seen.

Two consequences worth stating plainly:

1. Running the logging job earlier is the only way to make this data exist.
   Nothing else in the project has that property.
2. The difference between successive vintages is itself a legitimate feature —
   the forecast-error proxy CLAUDE.md §4 asks for. It is available precisely
   because both vintages were kept.

Storage is `vintage_log/<dataset>/<YYYY-MM>.parquet` in long format with an
`observed_at` column. It lives OUTSIDE `data/` deliberately: `data/` is
gitignored under R7, and these records are meant to be committed so the track
record survives a clean checkout. Only our own forecast captures belong here —
never raw licensed market data.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.data.data_availability import require_aware as _require_aware

VINTAGE_ROOT = Path(__file__).resolve().parents[2] / "vintage_log"

UTC = ZoneInfo("UTC")
_OBSERVED = "observed_at"
_TARGET = "target_time"


def _dataset_dir(dataset: str) -> Path:
    directory = VINTAGE_ROOT / dataset
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def append_vintage(dataset: str, frame: pd.DataFrame, observed_at: datetime) -> list[Path]:
    """Record one observation of ``frame``, taken at ``observed_at``.

    ``frame`` is indexed by target period (tz-aware UTC); its columns are the
    observed values. Existing vintages are never modified.

    Idempotent on ``(observed_at, target_time)``: re-running the same job in the
    same window adds nothing. If a repeat write carries *different* values for a
    pair already recorded, **the first write wins** — a given `observed_at` names
    a single observation, and silently replacing it would rewrite history.
    """
    observed_at = _require_aware(observed_at, "observed_at").astimezone(UTC)

    if frame.index.tz is None:  # type: ignore[attr-defined]
        raise ValueError("refusing to record a naive index; supply tz-aware UTC target times")

    if frame.empty:
        # Recording an empty vintage would assert "we observed nothing", which
        # is indistinguishable from "the job did not run". Say nothing instead.
        return []

    incoming = frame.copy()
    incoming.index.name = _TARGET
    incoming = incoming.reset_index()
    incoming[_TARGET] = pd.DatetimeIndex(incoming[_TARGET]).tz_convert("UTC")
    incoming.insert(0, _OBSERVED, pd.Timestamp(observed_at))

    written: list[Path] = []
    for month, chunk in incoming.groupby(incoming[_TARGET].dt.strftime("%Y-%m")):
        path = _dataset_dir(dataset) / f"{month}.parquet"
        if path.exists():
            combined = pd.concat([pd.read_parquet(path), chunk], ignore_index=True)
        else:
            combined = chunk
        # keep="first" is what makes the first write win.
        combined = combined.drop_duplicates(subset=[_OBSERVED, _TARGET], keep="first")
        combined = combined.sort_values([_OBSERVED, _TARGET]).reset_index(drop=True)
        combined.to_parquet(path, index=False)
        written.append(path)

    return sorted(written)


def read_vintages(dataset: str) -> pd.DataFrame:
    """Every recorded vintage for ``dataset``, long format. Empty if none."""
    files = sorted(_dataset_dir(dataset).glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    return frame.sort_values([_OBSERVED, _TARGET]).reset_index(drop=True)


def latest_as_of(dataset: str, as_of: datetime) -> pd.DataFrame:
    """What was visible at ``as_of``: the most recent vintage of each target
    period observed **strictly before** that instant.

    Strictly, not inclusively — R1. A vintage observed at exactly the decision
    instant was not available for that decision.

    Falls back per target period, so a newer vintage covering only part of the
    horizon does not hide an older vintage's coverage of the rest.
    """
    as_of = _require_aware(as_of, "as_of").astimezone(UTC)

    frame = read_vintages(dataset)
    if frame.empty:
        return frame

    visible = frame[frame[_OBSERVED] < pd.Timestamp(as_of)]
    if visible.empty:
        return visible.reset_index(drop=True)

    latest = visible.sort_values(_OBSERVED).drop_duplicates(subset=[_TARGET], keep="last")
    return latest.sort_values(_TARGET).reset_index(drop=True)
