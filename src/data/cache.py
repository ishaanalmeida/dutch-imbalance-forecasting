"""Local cache. Raw responses are stored before parsing so a parser change
never forces a re-fetch (and never burns API quota).

Lean zone: this is plumbing. Parquet + a directory layout, nothing more.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import cast

import pandas as pd

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def _raw_dir(source: str) -> Path:
    d = DATA_ROOT / "raw" / source
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(key: str) -> str:
    """Keys can contain characters Windows rejects in filenames (`:`, `/`)."""
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:80]
    return f"{cleaned}-{digest}"


def store_raw(source: str, key: str, payload: bytes, fetched_at: datetime) -> Path:
    """Persist a raw API response plus a sidecar recording when it was fetched.

    The sidecar is the data vintage: it is what lets us reason about revisions
    later, and it is irrecoverable if not written at fetch time.
    """
    path = _raw_dir(source) / f"{_safe(key)}.bin"
    path.write_bytes(payload)
    path.with_suffix(".json").write_text(
        json.dumps({"key": key, "fetched_at": fetched_at.isoformat(), "bytes": len(payload)}),
        encoding="utf-8",
    )
    return path


def load_raw(source: str, key: str) -> bytes | None:
    path = _raw_dir(source) / f"{_safe(key)}.bin"
    return path.read_bytes() if path.exists() else None


def _dataset_dir(dataset: str) -> Path:
    d = DATA_ROOT / "processed" / dataset
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_frame(dataset: str, df: pd.DataFrame) -> list[Path]:
    """Write a UTC-indexed frame, partitioned by month. Rewriting a month
    replaces it wholesale, so writes are idempotent."""
    idx = cast(pd.DatetimeIndex, df.index)
    if idx.tz is None:
        raise ValueError("refusing to cache a naive index; supply tz-aware UTC")
    # ponytail: `idx.to_period("M")` silently drops tz on a tz-aware
    # DatetimeIndex (and warns). Group by a formatted string instead so the
    # stored frame keeps its tz-aware UTC index untouched.
    months = idx.strftime("%Y-%m")
    written = []
    for month, chunk in df.groupby(months):
        path = _dataset_dir(dataset) / f"{month}.parquet"
        chunk.to_parquet(path)
        written.append(path)
    return sorted(written)


def read_frame(dataset: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Read [start, end). Returns an empty frame if nothing is cached."""
    files = sorted(_dataset_dir(dataset).glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    return df[(df.index >= start) & (df.index < end)]
