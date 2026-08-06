"""Task 7: the TenneT balance-delta lag measurement harness.

registration at developer.tennet.eu was not performed as part of this spike
(docs/DATA_SOURCES.md "TenneT access") -- the endpoint, auth and response
schema are unseen (R3). These tests assert the harness fails loudly rather
than producing samples: an unmeasured lag must never look measured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.measure_balance_delta_lag import fetch_latest_balance_delta, poll


def test_fetch_latest_balance_delta_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="developer.tennet.eu"):
        fetch_latest_balance_delta()


def test_poll_raises_rather_than_producing_samples(tmp_path: Path) -> None:
    """The poll loop must propagate the NotImplementedError on its first
    call rather than swallowing it, sleeping, and retrying forever."""
    out = tmp_path / "balance_delta_lag_samples.jsonl"
    with pytest.raises(NotImplementedError):
        poll(minutes=120, poll_seconds=5, out_path=out)
    assert out.read_text(encoding="utf-8") == ""
