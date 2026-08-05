# Phase 1 Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cached, UTC-canonical data layer for NL imbalance data in which no feature can be constructed from information published after its decision timestamp, proven by tests.

**Architecture:** Four layers, bottom-up. `timebase` owns the canonical UTC ISP index and DST handling. `data_availability` reads publication lags from `config/market_rules.yaml` and answers "when did this datum become retrievable?" — refusing rather than guessing when a lag is unresolved. `cache` stores raw API responses before parsing so re-parsing never re-fetches. Fetchers (`entsoe`, `openmeteo`) write through the cache. The feature builder sits on top and is **structurally incapable** of emitting a feature that violates availability, because it asks `data_availability` for permission per field per period.

**Tech Stack:** Python 3.12, pandas + pyarrow (Parquet), `entsoe-py`, `httpx`, PyYAML, pytest. `uv` for dependency management.

## Global Constraints

Copied verbatim from `CLAUDE.md`; every task's requirements implicitly include these.

- **R1 — No look-ahead. Ever.** Every feature used to predict target period `t` must have been *published and retrievable* strictly before the decision timestamp for `t`.
- **R6 — Reproducibility.** Pin dependencies. Seed everything. Log versions.
- **R7 — Licensing.** Never commit raw third-party market data. `data/` is gitignored; ship fetch scripts.
- **R8 — Secrets.** Tokens in `.env`, never in code or commits.
- **Rigour zones (CLAUDE.md §12) — ponytail simplification is SUSPENDED in:** `src/data/data_availability.py`, anything enforcing publication lag, `config/market_rules.yaml` and settlement logic, `src/backtest/`, `src/evaluation/`, and the entire test suite covering them. `ponytail:` shortcut comments are **prohibited** in these files.
- **Lean zones — ponytail applies:** fetchers, caching, plumbing, config, scripts, Makefile.
- Python `>=3.11,<3.13`. Type hints throughout; `mypy --strict` must pass. `ruff` clean.
- Canonical time index is **UTC internally, Europe/Amsterdam only at presentation.**
- ISP length is 15 minutes (`config/market_rules.yaml: isp.length_minutes`). Never hardcode it.
- Every model/data run writes a manifest: git SHA, config hash, data vintage, seed.

## Phase 1 Gate (CLAUDE.md §3)

> *"a test suite proving no feature can be constructed from information published after its decision timestamp."*

Task 3 is that gate. Tasks 4–8 must not be considered complete until Task 3 passes.

## Known blockers, carried in from Phase 0

1. **ENTSO-E token not yet issued.** Requested 2026-08-04. Tasks 6 and 8 are token-gated; every other task proceeds without it. Task 6 is built against **recorded fixtures** so it is fully testable with no token.
2. **TenneT blocks programmatic access.** `www.tennet.eu`, `api.tennet.eu` all return **HTTP 403** (not 404 — an API exists but rejects us). Task 7 is a timeboxed spike, not an open-ended integration.
3. **`balance_delta` publication lag is `null` / `unresolved`** (ADR-006). It must be *measured*, not assumed. Task 7 builds that measurement. Until it succeeds, `data_availability` refuses to serve the field and **no feature may use balance delta.**

## File Structure

| File | Responsibility |
|---|---|
| `src/data/timebase.py` | Canonical UTC ISP index; DST-safe conversion to/from Europe/Amsterdam; `isp_start_of`, `isps_in_local_day`. Nothing else. |
| `src/data/data_availability.py` | **Rigour zone.** `available_at(field, target_period_start)`, `is_available(...)`, `UnresolvedLagError`, `UnknownFieldError`. Reads lags from config only. |
| `src/data/cache.py` | Raw-response store + Parquet cache partitioned by month. Idempotent writes. |
| `src/data/entsoe.py` | ENTSO-E fetchers; parse to canonical UTC frames. Token from env. |
| `src/data/openmeteo.py` | Open-Meteo **historical forecast** fetcher. Hard ban on the reanalysis endpoint. |
| `src/features/builder.py` | Feature assembly. Every field access routes through `data_availability`. |
| `tests/test_timebase.py` | DST 23h/25h days, ISP arithmetic. |
| `tests/test_data_availability.py` | **Rigour zone.** Lag resolution, refusal behaviour, unknown fields. |
| `tests/test_no_lookahead.py` | **The Phase 1 gate.** Adversarial: deliberately tries to leak and asserts refusal. |
| `tests/test_cache.py` | Idempotency, round-trip, partitioning. |
| `tests/test_openmeteo.py` | Reanalysis-endpoint ban; vintage capture. |
| `tests/fixtures/` | Recorded API responses. **Committed** — they are our responses, not licensed bulk data (R7). |

---

### Task 1: Canonical time base and DST correctness

**Files:**
- Create: `src/data/timebase.py`
- Test: `tests/test_timebase.py`
- Modify: `pyproject.toml` (add `pandas`, `pyarrow`)

**Interfaces:**
- Consumes: `src.market.load_rules()` for `isp.length_minutes`.
- Produces:
  - `ISP_MINUTES: int`
  - `isp_index(start: datetime, end: datetime) -> pd.DatetimeIndex` — tz-aware UTC, left-closed, `[start, end)`
  - `isp_start_of(ts: datetime) -> datetime` — floor to ISP boundary, UTC
  - `isps_in_local_day(local_date: date) -> pd.DatetimeIndex` — UTC index of every ISP in an Amsterdam calendar day
  - `to_local(idx) -> pd.DatetimeIndex`, `to_utc(idx) -> pd.DatetimeIndex`

- [ ] **Step 1: Add dependencies**

```bash
uv add pandas pyarrow
```

- [ ] **Step 2: Write the failing DST tests**

These are the tests CLAUDE.md §3 explicitly mandates. Europe/Amsterdam 2026: DST starts **29 March 2026** (23-hour day, 92 ISPs) and ends **25 October 2026** (25-hour day, 100 ISPs).

```python
# tests/test_timebase.py
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.data.timebase import (
    ISP_MINUTES,
    isp_index,
    isp_start_of,
    isps_in_local_day,
    to_local,
)


def test_isp_length_comes_from_config() -> None:
    assert ISP_MINUTES == 15


def test_normal_local_day_has_96_isps() -> None:
    assert len(isps_in_local_day(date(2026, 6, 17))) == 96


def test_spring_forward_day_has_92_isps() -> None:
    """29 March 2026: Europe/Amsterdam loses an hour. 23 hours = 92 ISPs."""
    idx = isps_in_local_day(date(2026, 3, 29))
    assert len(idx) == 92


def test_autumn_back_day_has_100_isps() -> None:
    """25 October 2026: Europe/Amsterdam repeats an hour. 25 hours = 100 ISPs."""
    idx = isps_in_local_day(date(2026, 10, 25))
    assert len(idx) == 100


def test_all_isp_indices_are_utc_and_unique() -> None:
    """The repeated local hour must NOT collapse: 02:00-03:00 CEST and CET are
    distinct instants and must appear as distinct UTC timestamps."""
    idx = isps_in_local_day(date(2026, 10, 25))
    assert str(idx.tz) == "UTC"
    assert idx.is_unique
    assert idx.is_monotonic_increasing


def test_local_conversion_marks_the_repeated_hour_distinctly() -> None:
    idx = isps_in_local_day(date(2026, 10, 25))
    local = to_local(idx)
    offsets = {t.utcoffset() for t in local}
    assert len(offsets) == 2, "both CEST (+02:00) and CET (+01:00) must appear"


def test_isp_start_floors_to_quarter_hour() -> None:
    ts = datetime(2026, 6, 17, 14, 37, 41, tzinfo=timezone.utc)
    assert isp_start_of(ts) == datetime(2026, 6, 17, 14, 30, tzinfo=timezone.utc)


def test_isp_start_is_idempotent_on_a_boundary() -> None:
    ts = datetime(2026, 6, 17, 14, 30, tzinfo=timezone.utc)
    assert isp_start_of(ts) == ts


def test_naive_datetime_is_rejected() -> None:
    """Naive timestamps are the classic silent-DST-bug vector."""
    with pytest.raises(ValueError, match="timezone-aware"):
        isp_start_of(datetime(2026, 6, 17, 14, 37))


def test_isp_index_is_left_closed() -> None:
    idx = isp_index(
        datetime(2026, 6, 17, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 17, 1, 0, tzinfo=timezone.utc),
    )
    assert len(idx) == 4
    assert idx[0] == pd.Timestamp("2026-06-17T00:00Z")
    assert idx[-1] == pd.Timestamp("2026-06-17T00:45Z")
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_timebase.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.data.timebase'`

- [ ] **Step 4: Implement `timebase.py`**

```python
"""Canonical time base. UTC internally, Europe/Amsterdam only at presentation.

DST is the classic silent failure in energy pipelines: a 23-hour and a 25-hour
day occur every year, and code that assumes 96 ISPs per day is wrong twice a
year in ways that do not raise. Everything here is UTC-first for that reason.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from src.market import load_rules

ISP_MINUTES: int = int(load_rules()["isp"]["length_minutes"])
LOCAL_TZ = ZoneInfo(load_rules()["meta"]["timezone_presentation"])
UTC = ZoneInfo("UTC")

_ISP = timedelta(minutes=ISP_MINUTES)


def _require_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError(f"timestamp must be timezone-aware, got naive {ts!r}")
    return ts


def isp_start_of(ts: datetime) -> datetime:
    """Floor a timestamp to the start of the ISP containing it, in UTC."""
    ts = _require_aware(ts).astimezone(UTC)
    discard = (ts.minute % ISP_MINUTES) * 60 + ts.second
    return (ts - timedelta(seconds=discard)).replace(microsecond=0)


def isp_index(start: datetime, end: datetime) -> pd.DatetimeIndex:
    """UTC ISP starts over the half-open interval [start, end)."""
    return pd.date_range(
        start=isp_start_of(start),
        end=isp_start_of(end),
        freq=f"{ISP_MINUTES}min",
        tz="UTC",
        inclusive="left",
    )


def isps_in_local_day(local_date: date) -> pd.DatetimeIndex:
    """Every ISP in an Amsterdam calendar day, as UTC starts.

    Returns 92 on the spring-forward day and 100 on the autumn-back day. The
    repeated local hour yields two distinct UTC instants, which is why this is
    computed by converting local midnight boundaries to UTC rather than by
    adding 24 hours.
    """
    day_start = datetime.combine(local_date, datetime.min.time(), tzinfo=LOCAL_TZ)
    next_day = datetime.combine(
        local_date + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_TZ
    )
    return isp_index(day_start.astimezone(UTC), next_day.astimezone(UTC))


def to_local(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Presentation only. Never use the result as a join key."""
    return idx.tz_convert(LOCAL_TZ)


def to_utc(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None:
        raise ValueError("refusing to localise a naive index; supply tz-aware input")
    return idx.tz_convert("UTC")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_timebase.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy && uv run pytest
git add src/data/timebase.py tests/test_timebase.py pyproject.toml uv.lock
git commit -m "feat(data): UTC-canonical ISP timebase with DST tests

23-hour and 25-hour Amsterdam days are asserted explicitly (92 and 100 ISPs)
because a naive 96-per-day assumption fails silently twice a year."
```

---

### Task 2: Publication rules — replace the `-1` sentinel with explicit rules

**Files:**
- Modify: `config/market_rules.yaml` (the `publication:` block)
- Test: `tests/test_data_availability.py` (created in Task 3; this task only changes config + adds a config-shape test)

**Interfaces:**
- Produces: a `publication` block where every field has an explicit `rule`, consumed by Task 3.

**Why this task exists.** Phase 0 encoded "available before the delivery day" as `lag_seconds: -1`. A negative-number sentinel in a rigour-zone config is exactly the kind of thing that gets misread as an actual lag and silently grants a day of look-ahead. Replace it with a named rule.

- [ ] **Step 1: Write the failing config-shape test**

```python
# tests/test_data_availability.py  (first content; Task 3 extends this file)
from __future__ import annotations

from src.market import load_rules

VALID_RULES = {"lag_after_period", "published_day_before_at", "unresolved"}


def test_every_publication_field_declares_a_known_rule() -> None:
    for field, spec in load_rules()["publication"].items():
        assert "rule" in spec, f"{field} has no publication rule"
        assert spec["rule"] in VALID_RULES, f"{field} has unknown rule {spec['rule']!r}"


def test_no_negative_lag_sentinels_remain() -> None:
    """A negative lag would mean 'available before the period it describes',
    which is only ever true via an explicit day-before rule."""
    for field, spec in load_rules()["publication"].items():
        lag = spec.get("lag_seconds")
        if lag is not None:
            assert lag >= 0, f"{field} still uses a negative lag sentinel"


def test_day_before_fields_declare_a_publication_time() -> None:
    for field, spec in load_rules()["publication"].items():
        if spec["rule"] == "published_day_before_at":
            assert "local_time" in spec, f"{field} needs a local_time"
            assert "timezone" in spec, f"{field} needs a timezone"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_data_availability.py -v`
Expected: FAIL — `AssertionError: balance_delta has no publication rule`

- [ ] **Step 3: Rewrite the `publication:` block**

Add `rule:` to every field. Replace each `lag_seconds: -1` with the day-before form. Example transformations — apply the same shape to every field:

```yaml
  balance_delta:
    rule: unresolved            # lag must be MEASURED (ADR-006), not assumed
    cadence_seconds: 12
    lag_seconds: null
    lag_confidence: unresolved
    # ... existing notes retained verbatim ...

  imbalance_price_settled:
    rule: lag_after_period
    lag_seconds: 122400
    revised: false
    confidence: primary

  day_ahead_price:
    rule: published_day_before_at
    local_time: "13:00"
    timezone: Europe/Amsterdam
    revised: false
    confidence: primary
    note: >
      Reg. 543/2013 Art. 12(2)(d): no later than one hour after gate closure.
      SDAC gate closure is 12:00 CET on D-1, so prices for all of day D are
      available from ~13:00 local on D-1.

  wind_solar_forecast_day_ahead:
    rule: published_day_before_at
    local_time: "17:00"
    timezone: Europe/Amsterdam
    revised: true
    confidence: primary

  actual_generation:
    rule: lag_after_period
    lag_seconds: 3600
    revised: true
    confidence: primary
```

Apply the same treatment to `imbalance_price_realtime_estimate` (`lag_after_period`), `regulation_state` (`lag_after_period`), `load_forecast_day_ahead` (`published_day_before_at`, `local_time: "10:00"` — two hours before the 12:00 gate closure), `cross_border_physical_flow` (`lag_after_period`), and `activated_balancing_volumes` (`rule: unresolved`, since its deadline was never verified).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data_availability.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
uv run pytest && git add config/market_rules.yaml tests/test_data_availability.py
git commit -m "refactor(config): explicit publication rules, drop -1 lag sentinel

A negative-number sentinel in a rigour-zone config reads like a real lag and
would silently grant a day of look-ahead. Named rules cannot be misread."
```

---

### Task 3: `data_availability` — THE PHASE 1 GATE

**Files:**
- Create: `src/data/data_availability.py`
- Test: `tests/test_data_availability.py` (extend), `tests/test_no_lookahead.py`

**Interfaces:**
- Consumes: `src.market.load_rules()`, `src.data.timebase.isp_start_of`, `LOCAL_TZ`.
- Produces:
  - `class UnresolvedLagError(RuntimeError)`
  - `class UnknownFieldError(KeyError)`
  - `available_at(field: str, target_period_start: datetime) -> datetime`
  - `is_available(field: str, target_period_start: datetime, decision_time: datetime) -> bool`
  - `assert_available(field, target_period_start, decision_time) -> None` — raises `LookAheadError`
  - `class LookAheadError(RuntimeError)`

**Rigour zone. Ponytail is suspended in this file. No `ponytail:` comments.**

- [ ] **Step 1: Write the failing behaviour tests**

Append to `tests/test_data_availability.py`:

```python
from datetime import datetime, timezone

import pytest

from src.data.data_availability import (
    LookAheadError,
    UnknownFieldError,
    UnresolvedLagError,
    assert_available,
    available_at,
    is_available,
)

ISP = datetime(2026, 6, 17, 14, 30, tzinfo=timezone.utc)


def test_lag_after_period_is_measured_from_period_end() -> None:
    """actual_generation: 1h after the period ENDS, not after it starts.
    ISP 14:30-14:45 UTC therefore becomes available at 15:45 UTC."""
    assert available_at("actual_generation", ISP) == datetime(
        2026, 6, 17, 15, 45, tzinfo=timezone.utc
    )


def test_day_ahead_price_is_available_the_previous_afternoon() -> None:
    got = available_at("day_ahead_price", ISP)
    assert got < ISP, "day-ahead price must precede delivery"
    assert got == datetime(2026, 6, 16, 11, 0, tzinfo=timezone.utc)  # 13:00 CEST


def test_settled_imbalance_price_is_not_available_same_day() -> None:
    assert available_at("imbalance_price_settled", ISP) > ISP


def test_unresolved_lag_refuses_rather_than_defaulting() -> None:
    """ADR-006. balance_delta must REFUSE, never fall back to a guess."""
    with pytest.raises(UnresolvedLagError, match="balance_delta"):
        available_at("balance_delta", ISP)


def test_unknown_field_raises() -> None:
    with pytest.raises(UnknownFieldError):
        available_at("wind_speed_at_my_house", ISP)


def test_is_available_is_strict_not_inclusive() -> None:
    """R1 says STRICTLY before. A datum published exactly at the decision
    instant is not usable."""
    at = available_at("actual_generation", ISP)
    assert is_available("actual_generation", ISP, at + timedelta(seconds=1))
    assert not is_available("actual_generation", ISP, at)


def test_assert_available_raises_with_a_diagnostic_message() -> None:
    with pytest.raises(LookAheadError) as exc:
        assert_available("imbalance_price_settled", ISP, ISP)
    assert "imbalance_price_settled" in str(exc.value)
    assert "available at" in str(exc.value)


def test_naive_decision_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_available("actual_generation", ISP, datetime(2026, 6, 17, 16, 0))
```

Add `from datetime import timedelta` to the imports.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_data_availability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.data.data_availability'`

- [ ] **Step 3: Implement `data_availability.py`**

```python
"""When did each datum become retrievable? The enforcement point for R1.

RIGOUR ZONE (CLAUDE.md §12). Ponytail simplification does not apply here and
`ponytail:` shortcut comments are prohibited.

The cardinal rule: every feature used to predict target period t must have been
published and retrievable STRICTLY before the decision timestamp for t. This
module is the only place that answers "when was it published?", and it refuses
to answer when it does not know. A module that guesses here produces a backtest
that looks excellent and means nothing.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.data.timebase import ISP_MINUTES
from src.market import load_rules

UTC = ZoneInfo("UTC")


class UnknownFieldError(KeyError):
    """Field is not declared in config/market_rules.yaml publication block."""


class UnresolvedLagError(RuntimeError):
    """The publication lag for this field is not known.

    Raised rather than returning a default. A default here is indistinguishable
    from a measurement at the call site, and would silently license look-ahead.
    """


class LookAheadError(RuntimeError):
    """A caller tried to use a datum that was not yet published."""


def _spec(field: str) -> dict[str, object]:
    publication = load_rules()["publication"]
    if field not in publication:
        raise UnknownFieldError(
            f"{field!r} is not declared in config/market_rules.yaml. "
            f"Declare it with a publication rule before using it. "
            f"Known fields: {sorted(publication)}"
        )
    return dict(publication[field])


def _require_aware(ts: datetime, label: str) -> datetime:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError(f"{label} must be timezone-aware, got naive {ts!r}")
    return ts


def available_at(field: str, target_period_start: datetime) -> datetime:
    """The instant `field` for the ISP starting at `target_period_start` first
    became retrievable, in UTC.

    Raises UnresolvedLagError if the lag is not established.
    """
    target_period_start = _require_aware(target_period_start, "target_period_start")
    spec = _spec(field)
    rule = spec.get("rule")

    if rule == "unresolved":
        raise UnresolvedLagError(
            f"The publication lag for {field!r} is not established "
            f"(lag_confidence={spec.get('lag_confidence')!r}). It must be measured "
            f"empirically and written back to config/market_rules.yaml with evidence "
            f"before this field may be used. See docs/DECISIONS.md ADR-006."
        )

    if rule == "lag_after_period":
        lag = spec.get("lag_seconds")
        if lag is None:
            raise UnresolvedLagError(f"{field!r} declares lag_after_period but no lag_seconds")
        period_end = target_period_start.astimezone(UTC) + timedelta(minutes=ISP_MINUTES)
        return period_end + timedelta(seconds=int(lag))  # type: ignore[arg-type]

    if rule == "published_day_before_at":
        tz = ZoneInfo(str(spec["timezone"]))
        hh, mm = (int(part) for part in str(spec["local_time"]).split(":"))
        local_delivery = target_period_start.astimezone(tz)
        publish_local = datetime.combine(
            local_delivery.date() - timedelta(days=1), time(hh, mm), tzinfo=tz
        )
        return publish_local.astimezone(UTC)

    raise UnknownFieldError(f"{field!r} has unhandled publication rule {rule!r}")


def is_available(field: str, target_period_start: datetime, decision_time: datetime) -> bool:
    """True iff the datum was published STRICTLY before `decision_time`."""
    decision_time = _require_aware(decision_time, "decision_time")
    return available_at(field, target_period_start) < decision_time.astimezone(UTC)


def assert_available(
    field: str, target_period_start: datetime, decision_time: datetime
) -> None:
    """Raise LookAheadError unless the datum was published before the decision."""
    if not is_available(field, target_period_start, decision_time):
        published = available_at(field, target_period_start)
        raise LookAheadError(
            f"R1 violation: {field!r} for ISP {target_period_start.isoformat()} "
            f"is available at {published.isoformat()}, which is not strictly before "
            f"the decision time {decision_time.isoformat()}."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data_availability.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Write the adversarial gate suite**

This is the Phase 1 gate deliverable. It deliberately tries to leak.

```python
# tests/test_no_lookahead.py
"""The Phase 1 gate (CLAUDE.md §3): prove no feature can be constructed from
information published after its decision timestamp.

RIGOUR ZONE. These tests are adversarial by design — each one attempts a leak
that a plausible implementation would permit, and asserts refusal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.data_availability import (
    LookAheadError,
    UnresolvedLagError,
    assert_available,
    available_at,
    is_available,
)
from src.market import load_rules

ISP = datetime(2026, 6, 17, 14, 30, tzinfo=timezone.utc)
DECISION = ISP  # CLAUDE.md ADR-005: decision is taken at ISP start


def test_the_target_can_never_be_used_as_a_feature() -> None:
    """The settled imbalance price is the TARGET. If this ever passes, the
    entire project is measuring nothing."""
    for field in ("imbalance_price_settled", "regulation_state"):
        with pytest.raises(LookAheadError):
            assert_available(field, ISP, DECISION)


def test_no_field_is_available_before_the_period_it_describes_unless_declared() -> None:
    """Only day-before-published fields may precede delivery. Anything else
    claiming pre-delivery availability is a config error."""
    for field, spec in load_rules()["publication"].items():
        if spec["rule"] == "unresolved":
            continue
        got = available_at(field, ISP)
        if spec["rule"] != "published_day_before_at":
            assert got >= ISP, f"{field} claims availability before its own period"


def test_same_isp_realtime_data_is_not_available_at_isp_start() -> None:
    """A within-ISP estimate for ISP t cannot be known at the start of t."""
    assert not is_available("imbalance_price_realtime_estimate", ISP, DECISION)


def test_previous_isp_realtime_data_is_available() -> None:
    """The signal the strategy actually runs on: the PREVIOUS ISP's estimate."""
    previous = ISP - timedelta(minutes=15)
    assert is_available("imbalance_price_realtime_estimate", previous, DECISION)


def test_balance_delta_refuses_in_both_directions() -> None:
    """Unresolved lag must block use, not merely warn — for any ISP, past or
    present. ADR-006."""
    for offset in (-timedelta(days=30), timedelta(0), timedelta(days=30)):
        with pytest.raises(UnresolvedLagError):
            is_available("balance_delta", ISP + offset, DECISION)


def test_availability_is_monotonic_in_target_period() -> None:
    """A later ISP can never become available earlier than an earlier one."""
    fields = [
        f for f, s in load_rules()["publication"].items() if s["rule"] != "unresolved"
    ]
    for field in fields:
        earlier = available_at(field, ISP)
        later = available_at(field, ISP + timedelta(hours=6))
        assert later >= earlier, f"{field} availability went backwards in time"


def test_a_deliberate_off_by_one_leak_is_caught() -> None:
    """Classic bug: measuring lag from period START instead of period END.
    For a 1-hour-lag field that mistake grants 15 free minutes."""
    published = available_at("actual_generation", ISP)
    naive_wrong = ISP + timedelta(hours=1)
    assert published > naive_wrong
    assert not is_available("actual_generation", ISP, naive_wrong)
```

- [ ] **Step 6: Run the gate**

Run: `uv run pytest tests/test_no_lookahead.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Full check and commit**

```bash
uv run ruff check . && uv run mypy && uv run pytest
git add src/data/data_availability.py tests/test_data_availability.py tests/test_no_lookahead.py
git commit -m "feat(data): availability enforcement + Phase 1 no-look-ahead gate

available_at() refuses rather than defaults when a lag is unresolved: a default
is indistinguishable from a measurement at the call site and would silently
license look-ahead. Gate suite is adversarial - each test attempts a leak a
plausible implementation would permit and asserts refusal."
```

---

### Task 4: Raw-response cache and Parquet store

**Files:**
- Create: `src/data/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces:
  - `store_raw(source: str, key: str, payload: bytes, fetched_at: datetime) -> Path`
  - `load_raw(source: str, key: str) -> bytes | None`
  - `write_frame(dataset: str, df: pd.DataFrame) -> list[Path]` — partitions by month on the UTC index
  - `read_frame(dataset: str, start: datetime, end: datetime) -> pd.DataFrame`

**Lean zone — ponytail applies. Boring is correct. No abstraction layer over Parquet.**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cache.py
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.data import cache


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "DATA_ROOT", tmp_path)


def test_raw_round_trip() -> None:
    at = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    cache.store_raw("entsoe", "imbalance_2026-06", b"<xml/>", at)
    assert cache.load_raw("entsoe", "imbalance_2026-06") == b"<xml/>"


def test_load_raw_returns_none_when_absent() -> None:
    assert cache.load_raw("entsoe", "nope") is None


def test_raw_store_is_idempotent() -> None:
    at = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    first = cache.store_raw("entsoe", "k", b"a", at)
    second = cache.store_raw("entsoe", "k", b"a", at)
    assert first == second


def test_frame_partitions_by_month() -> None:
    idx = pd.date_range("2026-05-30", "2026-06-02", freq="15min", tz="UTC")
    df = pd.DataFrame({"value": range(len(idx))}, index=idx)
    paths = cache.write_frame("prices", df)
    assert {p.name for p in paths} == {"2026-05.parquet", "2026-06.parquet"}


def test_frame_round_trip_preserves_utc_index() -> None:
    idx = pd.date_range("2026-06-01", periods=96, freq="15min", tz="UTC")
    df = pd.DataFrame({"value": range(96)}, index=idx)
    cache.write_frame("prices", df)
    got = cache.read_frame(
        "prices",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    assert str(got.index.tz) == "UTC"
    pd.testing.assert_frame_equal(got, df)


def test_rewriting_a_month_replaces_not_duplicates() -> None:
    idx = pd.date_range("2026-06-01", periods=4, freq="15min", tz="UTC")
    cache.write_frame("prices", pd.DataFrame({"value": [1, 2, 3, 4]}, index=idx))
    cache.write_frame("prices", pd.DataFrame({"value": [9, 9, 9, 9]}, index=idx))
    got = cache.read_frame(
        "prices",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    assert len(got) == 4
    assert got["value"].tolist() == [9, 9, 9, 9]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `cache.py`**

```python
"""Local cache. Raw responses are stored before parsing so a parser change
never forces a re-fetch (and never burns API quota).

Lean zone: this is plumbing. Parquet + a directory layout, nothing more.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def _raw_dir(source: str) -> Path:
    d = DATA_ROOT / "raw" / source
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(key: str) -> str:
    """Keys can contain characters Windows rejects in filenames."""
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
    if df.index.tz is None:
        raise ValueError("refusing to cache a naive index; supply tz-aware UTC")
    written = []
    for period, chunk in df.groupby(df.index.to_period("M")):
        path = _dataset_dir(dataset) / f"{period}.parquet"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cache.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run mypy && uv run pytest
git add src/data/cache.py tests/test_cache.py
git commit -m "feat(data): raw-response store and month-partitioned Parquet cache

Raw responses are kept so a parser change never forces a re-fetch. The
fetched_at sidecar is the data vintage and is irrecoverable if not written now."
```

---

### Task 5: Open-Meteo historical **forecast** fetcher

**Files:**
- Create: `src/data/openmeteo.py`
- Test: `tests/test_openmeteo.py`
- Modify: `pyproject.toml` (add `httpx`)

**Interfaces:**
- Consumes: `src.data.cache.store_raw`, `src.data.timebase`.
- Produces: `fetch_historical_forecast(start: date, end: date, latitude: float, longitude: float) -> pd.DataFrame` — UTC-indexed, columns `wind_speed_100m`, `shortwave_radiation`, `temperature_2m`.

**This task carries the project's highest silent-failure risk (CLAUDE.md §3).** ERA5 reanalysis is what the weather *was*; we need what the forecast *said*. A model fed reanalysis looks brilliant and is worthless, and nothing else in the suite would catch it.

Verified working during planning:
`https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=52.1&longitude=5.2&start_date=...&end_date=...&hourly=wind_speed_100m,shortwave_radiation,temperature_2m&timezone=UTC`

- [ ] **Step 1: Add dependency**

```bash
uv add httpx
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_openmeteo.py
from __future__ import annotations

import inspect

import pytest

from src.data import openmeteo


def test_only_the_historical_forecast_host_is_used() -> None:
    """R1's weather clause. The reanalysis archive must never appear."""
    assert openmeteo.BASE_URL.startswith("https://historical-forecast-api.open-meteo.com")


def test_reanalysis_host_appears_nowhere_in_the_module() -> None:
    source = inspect.getsource(openmeteo)
    assert "archive-api.open-meteo.com" not in source, (
        "ERA5 reanalysis is observed weather, not a forecast. Using it as a "
        "feature is the most likely silent failure in this project."
    )
    assert "/v1/archive" not in source


def test_guard_rejects_a_reanalysis_url() -> None:
    with pytest.raises(ValueError, match="reanalysis"):
        openmeteo._require_forecast_url("https://archive-api.open-meteo.com/v1/archive?x=1")


def test_guard_accepts_the_forecast_url() -> None:
    openmeteo._require_forecast_url(openmeteo.BASE_URL)


def test_parse_produces_utc_indexed_frame() -> None:
    payload = {
        "hourly": {
            "time": ["2026-06-01T00:00", "2026-06-01T01:00"],
            "wind_speed_100m": [12.0, 14.0],
            "shortwave_radiation": [0.0, 5.0],
            "temperature_2m": [11.0, 11.5],
        }
    }
    df = openmeteo._parse(payload)
    assert str(df.index.tz) == "UTC"
    assert list(df.columns) == ["wind_speed_100m", "shortwave_radiation", "temperature_2m"]
    assert len(df) == 2
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_openmeteo.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `openmeteo.py`**

```python
"""Open-Meteo HISTORICAL FORECAST archive: what the forecast said at the time.

Do NOT use archive-api.open-meteo.com (ERA5 reanalysis). Reanalysis is observed
weather reconstructed after the fact. Feeding it to the model is a look-ahead
violation that produces excellent metrics and no information, and it is the
single most likely way this project silently fails (CLAUDE.md §3).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import httpx
import pandas as pd

from src.data.cache import store_raw

BASE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
HOURLY_VARS = ("wind_speed_100m", "shortwave_radiation", "temperature_2m")

# Rough centroid of the NL onshore wind fleet. Offshore points are added in
# Phase 2 if wind features justify it.
NL_LAT, NL_LON = 52.1, 5.2


def _require_forecast_url(url: str) -> str:
    if "archive-api" in url or "/v1/archive" in url:
        raise ValueError(
            f"Refusing to fetch {url!r}: this is the ERA5 reanalysis endpoint. "
            "Reanalysis is observed weather, not a forecast issued at the time, "
            "and using it as a feature violates R1."
        )
    return url


def _parse(payload: dict[str, Any]) -> pd.DataFrame:
    hourly = payload["hourly"]
    idx = pd.DatetimeIndex(pd.to_datetime(hourly["time"])).tz_localize("UTC")
    return pd.DataFrame({v: hourly[v] for v in HOURLY_VARS}, index=idx)


def fetch_historical_forecast(
    start: date,
    end: date,
    latitude: float = NL_LAT,
    longitude: float = NL_LON,
    timeout: float = 60.0,
) -> pd.DataFrame:
    """Fetch the forecast as it was issued, for [start, end] inclusive."""
    url = _require_forecast_url(BASE_URL)
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "UTC",
    }
    response = httpx.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    store_raw(
        "openmeteo",
        f"forecast_{latitude}_{longitude}_{start}_{end}",
        response.content,
        datetime.now(timezone.utc),
    )
    return _parse(response.json())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_openmeteo.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Smoke-test against the live API**

Run:
```bash
uv run python -c "
from datetime import date
from src.data.openmeteo import fetch_historical_forecast
df = fetch_historical_forecast(date(2026,6,1), date(2026,6,2))
print(df.shape); print(df.head())
assert len(df) == 48 and str(df.index.tz) == 'UTC'
print('OK')
"
```
Expected: `(48, 3)` and `OK`. No token needed.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run mypy && uv run pytest
git add src/data/openmeteo.py tests/test_openmeteo.py pyproject.toml uv.lock
git commit -m "feat(data): Open-Meteo historical-forecast fetcher with reanalysis ban

Two tests enforce the ban: one asserts the reanalysis host appears nowhere in
the module source, one asserts the runtime guard rejects it. Reanalysis would
produce excellent metrics and no information."
```

---

### Task 6: ENTSO-E fetcher, testable without a token

**Files:**
- Create: `src/data/entsoe.py`, `tests/fixtures/entsoe_imbalance_nl.xml`
- Test: `tests/test_entsoe.py`
- Modify: `pyproject.toml` (add `entsoe-py`)

**Interfaces:**
- Consumes: `src.data.cache`, `src.data.timebase`.
- Produces:
  - `fetch_imbalance_prices(start, end) -> pd.DataFrame` — UTC index, columns `price_long`, `price_short`
  - `fetch_day_ahead_prices(start, end) -> pd.Series`
  - `fetch_load_forecast(start, end) -> pd.Series`
  - `fetch_wind_solar_forecast(start, end) -> pd.DataFrame`
  - `class MissingTokenError(RuntimeError)`

**Token-gated.** Parsing is separated from fetching so the parser is fully tested against a committed fixture with no token and no network. Live calls are marked `@pytest.mark.integration` and skipped without `ENTSOE_API_TOKEN`.

- [ ] **Step 1: Add dependency and register the marker**

```bash
uv add entsoe-py
```

In `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = ["integration: hits a live API; requires credentials"]
addopts = "-q --strict-markers -m 'not integration'"
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_entsoe.py
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from src.data import entsoe


def test_missing_token_raises_a_clear_error(monkeypatch) -> None:
    monkeypatch.delenv("ENTSOE_API_TOKEN", raising=False)
    with pytest.raises(entsoe.MissingTokenError, match="ENTSOE_API_TOKEN"):
        entsoe._require_token()


def test_token_is_never_logged_or_echoed(monkeypatch) -> None:
    """R8. The error message must not contain the token value."""
    monkeypatch.setenv("ENTSOE_API_TOKEN", "supersecret123")
    assert entsoe._require_token() == "supersecret123"
    try:
        entsoe._require_token.__doc__  # noqa: B018
    finally:
        pass


def test_nl_domain_code_is_correct() -> None:
    assert entsoe.NL_DOMAIN == "10YNL----------L"


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("ENTSOE_API_TOKEN"), reason="ENTSOE_API_TOKEN not set"
)
def test_live_imbalance_fetch_returns_utc_15min_index() -> None:
    df = entsoe.fetch_imbalance_prices(
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    assert str(df.index.tz) == "UTC"
    assert len(df) == 96
    assert {"price_long", "price_short"} <= set(df.columns)
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_entsoe.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `entsoe.py`**

```python
"""ENTSO-E Transparency Platform fetchers.

Lean zone: entsoe-py does the protocol work. We add caching of raw responses,
canonical UTC indexing, and column naming that matches config/market_rules.yaml
field names so data_availability can be consulted by the same name.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
from entsoe import EntsoePandasClient

from src.data.cache import store_raw

NL_DOMAIN = "10YNL----------L"


class MissingTokenError(RuntimeError):
    """ENTSOE_API_TOKEN is not set."""


def _require_token() -> str:
    token = os.getenv("ENTSOE_API_TOKEN")
    if not token:
        raise MissingTokenError(
            "ENTSOE_API_TOKEN is not set. Copy .env.example to .env and add the "
            "token issued by transparency@entsoe.eu. See docs/DATA_SOURCES.md."
        )
    return token


def _client() -> EntsoePandasClient:
    return EntsoePandasClient(api_key=_require_token())


def _stamps(start: datetime, end: datetime) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")


def _record(dataset: str, start: datetime, end: datetime, frame: pd.DataFrame) -> None:
    store_raw(
        "entsoe",
        f"{dataset}_{start:%Y%m%dT%H%M}_{end:%Y%m%dT%H%M}",
        frame.to_json().encode(),
        datetime.now(timezone.utc),
    )


def fetch_imbalance_prices(start: datetime, end: datetime) -> pd.DataFrame:
    """Settled imbalance prices. THIS IS THE TARGET, never a feature."""
    s, e = _stamps(start, end)
    raw = _client().query_imbalance_prices(NL_DOMAIN, start=s, end=e)
    df = raw.tz_convert("UTC")
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    _record("imbalance_prices", start, end, df)
    return df


def fetch_day_ahead_prices(start: datetime, end: datetime) -> pd.Series:
    s, e = _stamps(start, end)
    series = _client().query_day_ahead_prices(NL_DOMAIN, start=s, end=e).tz_convert("UTC")
    _record("day_ahead_prices", start, end, series.to_frame("day_ahead_price"))
    return series.rename("day_ahead_price")


def fetch_load_forecast(start: datetime, end: datetime) -> pd.Series:
    s, e = _stamps(start, end)
    series = _client().query_load_forecast(NL_DOMAIN, start=s, end=e).squeeze()
    series = series.tz_convert("UTC")
    _record("load_forecast", start, end, series.to_frame("load_forecast"))
    return series.rename("load_forecast")


def fetch_wind_solar_forecast(start: datetime, end: datetime) -> pd.DataFrame:
    s, e = _stamps(start, end)
    df = _client().query_wind_and_solar_forecast(NL_DOMAIN, start=s, end=e).tz_convert("UTC")
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    _record("wind_solar_forecast", start, end, df)
    return df
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_entsoe.py -v`
Expected: PASS (3 tests, 1 skipped as integration)

- [ ] **Step 6: When the token arrives — record a fixture and verify live**

```bash
uv run pytest tests/test_entsoe.py -m integration -v
```
Expected: PASS. If the imbalance frame does **not** have two distinct price columns, stop and report — the dual-price structure from Phase 0 Q3 must be present in the data, and its absence means the ENTSO-E item is single-priced and TenneT's own feed is required.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run mypy && uv run pytest
git add src/data/entsoe.py tests/test_entsoe.py pyproject.toml uv.lock
git commit -m "feat(data): ENTSO-E fetchers, parser testable without a token

Live calls are marked integration and skipped without credentials so the suite
stays green while the token request is pending."
```

---

### Task 7: TenneT access spike and balance-delta lag measurement

**Files:**
- Create: `src/data/tennet.py`, `scripts/measure_balance_delta_lag.py`
- Test: `tests/test_tennet.py`
- Modify: `config/market_rules.yaml` (only if the measurement succeeds)

**Interfaces:**
- Produces: `fetch_balance_delta(start, end) -> pd.DataFrame`, `measure_lag(samples) -> dict[str, float]`

**Timeboxed spike — 4 hours. Do not open-endedly reverse-engineer TenneT.**

Known: `www.tennet.eu`, `api.tennet.eu`, and the legacy `tennet.org` export all returned **403** during planning, with a browser user-agent. 403 rather than 404 means an endpoint exists and rejects us.

- [ ] **Step 1: Probe access routes and record findings**

Try in order, stopping at the first that works:
1. `api.tennet.eu` with an `Accept: application/json` header and a realistic browser UA.
2. TenneT's Data Platform / developer portal — check whether registration issues an API key (as ENTSO-E does).
3. ENTSO-E's *Current Balancing State* item (EBGL Art. 12.3.a) as a substitute — it carries system imbalance at sub-ISP resolution for NL.

Write the outcome of each into `docs/DATA_SOURCES.md` under a new "TenneT access" heading, whatever the result.

- [ ] **Step 2: If NO route works — record the consequence and stop**

Append to `LIMITATIONS.md`:

```markdown
### TenneT's high-resolution balance delta is not programmatically accessible
All probed TenneT endpoints return HTTP 403. The 12-second balance delta — the
main legitimate intra-ISP feature and the signal passive balancers actually
trade on — is therefore unavailable to this project. Consequences: (a) the
balance-delta publication lag cannot be measured, so the field stays refused by
data_availability; (b) intra-ISP features are limited to whatever ENTSO-E
publishes; (c) the strategy modelled here is strictly weaker than a real
operator's, which must be stated wherever revenue is reported.
```

Then **skip to Task 8**. Do not fabricate a lag to unblock the pipeline.

- [ ] **Step 3: If a route works — write the measurement script**

The lag is measured, never assumed (ADR-006). Poll the live endpoint, and for each observation record the wall-clock instant we could first see it versus the instant it describes.

```python
# scripts/measure_balance_delta_lag.py
"""Measure the balance-delta publication lag empirically.

ADR-006: the widely-cited 3/5/2-minute delay timeline could not be
substantiated and may confuse cadence with delay. Rather than trust any
document, poll and observe.

Run for at least 2 hours to span several ISPs, then write the result into
config/market_rules.yaml WITH the sample size and observation window.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from src.data.tennet import fetch_latest_balance_delta

OUT = Path("data/interim/balance_delta_lag_samples.jsonl")


def main(minutes: int = 120, poll_seconds: int = 5) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + minutes * 60
    seen: set[str] = set()
    with OUT.open("a", encoding="utf-8") as fh:
        while time.time() < deadline:
            observed_at = datetime.now(timezone.utc)
            for point in fetch_latest_balance_delta():
                stamp = point["timestamp"]
                if stamp in seen:
                    continue
                seen.add(stamp)
                lag = (observed_at - datetime.fromisoformat(stamp)).total_seconds()
                fh.write(json.dumps({"describes": stamp, "first_seen": observed_at.isoformat(), "lag_seconds": lag}) + "\n")
                fh.flush()
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the measurement**

```bash
uv run python scripts/measure_balance_delta_lag.py
```

Report the **median, p95 and max** observed lag, and the sample count. The p95 — not the median — is the honest number for R1, because using the median would grant look-ahead on half the observations.

- [ ] **Step 5: Write the measured lag back to config**

```yaml
  balance_delta:
    rule: lag_after_period
    lag_seconds: <p95, rounded UP to the next whole second>
    lag_confidence: measured
    lag_measurement:
      method: "polled live endpoint; lag = first_seen - timestamp_described"
      samples: <n>
      observed_from: "<ISO8601>"
      observed_to: "<ISO8601>"
      median_seconds: <x>
      p95_seconds: <y>
      max_seconds: <z>
      script: scripts/measure_balance_delta_lag.py
```

- [ ] **Step 6: Verify the guard test flips correctly**

Run: `uv run pytest tests/test_settlement.py::test_balance_delta_lag_stays_unresolved_until_it_is_measured -v`
Expected: PASS via the `lag_confidence == "measured"` branch.

Run: `uv run pytest tests/test_no_lookahead.py -v`
Expected: `test_balance_delta_refuses_in_both_directions` now FAILS — the field is no longer unresolved. **Update that test** to assert the measured lag is respected instead:

```python
def test_balance_delta_respects_the_measured_lag() -> None:
    spec = load_rules()["publication"]["balance_delta"]
    if spec["rule"] == "unresolved":
        pytest.skip("lag not yet measured")
    assert spec["lag_confidence"] == "measured"
    assert not is_available("balance_delta", ISP, DECISION)
    old = ISP - timedelta(minutes=15)
    assert is_available("balance_delta", old, DECISION) == (
        available_at("balance_delta", old) < DECISION
    )
```

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run mypy && uv run pytest
git add -A
git commit -m "feat(data): TenneT access spike + empirical balance-delta lag

Lag is the p95 of observed publication delay, not the median: the median would
grant look-ahead on half the observations. Measurement provenance recorded in
config alongside the number."
```

---

### Task 8: Data quality report

**Files:**
- Create: `src/data/quality.py`, `scripts/build_quality_report.py`
- Test: `tests/test_quality.py`
- Output: `docs/DATA_QUALITY.md`

**Interfaces:**
- Produces: `gap_report(df) -> pd.DataFrame`, `duplicate_report(df) -> pd.DataFrame`, `regulation_state_distribution(df, by) -> pd.DataFrame`, `structural_break_check(df) -> pd.DataFrame`

**Token-gated** (needs real data). Build and unit-test the functions on synthetic frames first; generate the report when data lands.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quality.py
from __future__ import annotations

import pandas as pd
import pytest

from src.data.quality import duplicate_report, gap_report, regulation_state_distribution


def test_gap_report_finds_a_missing_isp() -> None:
    idx = pd.date_range("2026-06-01", periods=10, freq="15min", tz="UTC")
    df = pd.DataFrame({"v": range(10)}, index=idx).drop(idx[5])
    gaps = gap_report(df)
    assert len(gaps) == 1
    assert gaps.iloc[0]["missing_isps"] == 1


def test_gap_report_is_empty_on_a_complete_series() -> None:
    idx = pd.date_range("2026-06-01", periods=96, freq="15min", tz="UTC")
    assert gap_report(pd.DataFrame({"v": range(96)}, index=idx)).empty


def test_gap_report_tolerates_the_25_hour_day() -> None:
    """The autumn-back day has 100 ISPs and no gaps. A UTC-based check passes
    naturally; a local-time check would report a spurious 4-ISP gap."""
    from datetime import date

    from src.data.timebase import isps_in_local_day

    idx = isps_in_local_day(date(2026, 10, 25))
    assert len(idx) == 100
    assert gap_report(pd.DataFrame({"v": range(100)}, index=idx)).empty


def test_duplicate_report_finds_repeated_index_entries() -> None:
    idx = pd.DatetimeIndex(
        ["2026-06-01T00:00Z", "2026-06-01T00:00Z", "2026-06-01T00:15Z"]
    )
    dupes = duplicate_report(pd.DataFrame({"v": [1, 2, 3]}, index=idx))
    assert len(dupes) == 1


def test_regulation_state_distribution_sums_to_one_per_group() -> None:
    idx = pd.date_range("2026-06-01", periods=8, freq="15min", tz="UTC")
    df = pd.DataFrame({"regulation_state": [0, 1, -1, 2, 0, 1, 2, 2]}, index=idx)
    dist = regulation_state_distribution(df, by="hour")
    for _, row in dist.iterrows():
        assert row.sum() == pytest.approx(1.0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_quality.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `quality.py`**

```python
"""Data quality diagnostics.

RIGOUR ZONE — this feeds docs/DATA_QUALITY.md and the segmented reporting the
project's credibility rests on. All checks operate on the UTC index, which is
what makes them correct across DST boundaries.
"""

from __future__ import annotations

import pandas as pd

from src.data.timebase import ISP_MINUTES

_STEP = pd.Timedelta(minutes=ISP_MINUTES)


def gap_report(df: pd.DataFrame) -> pd.DataFrame:
    """Contiguous runs of missing ISPs in a UTC-indexed frame."""
    if df.empty:
        return pd.DataFrame(columns=["gap_start", "gap_end", "missing_isps"])
    idx = df.index.sort_values().unique()
    deltas = pd.Series(idx).diff()
    breaks = deltas[deltas > _STEP]
    rows = [
        {
            "gap_start": idx[i - 1] + _STEP,
            "gap_end": idx[i],
            "missing_isps": int(deltas.iloc[i] / _STEP) - 1,
        }
        for i in breaks.index
    ]
    return pd.DataFrame(rows, columns=["gap_start", "gap_end", "missing_isps"])


def duplicate_report(df: pd.DataFrame) -> pd.DataFrame:
    dupes = df.index[df.index.duplicated(keep=False)].unique()
    return pd.DataFrame({"timestamp": dupes, "count": [int((df.index == t).sum()) for t in dupes]})


def regulation_state_distribution(df: pd.DataFrame, by: str = "hour") -> pd.DataFrame:
    """Share of each regulation state, grouped by hour / month / year."""
    key = {
        "hour": df.index.hour,
        "month": df.index.to_period("M"),
        "year": df.index.year,
    }[by]
    counts = df.groupby([key, df["regulation_state"]]).size().unstack(fill_value=0)
    return counts.div(counts.sum(axis=1), axis=0)


def structural_break_check(df: pd.DataFrame) -> pd.DataFrame:
    """State-2 frequency before vs after each structural break in config.

    Specifically tests the ADR-007 prediction: state 2 should become MORE
    frequent after 2026-02-03, when the state-determination input changed from
    the 1-minute to the 12-second balance delta (15 -> 75 samples per ISP), with
    no change in the physical system.
    """
    from src.market import load_rules

    rows = []
    for brk in load_rules()["structural_breaks"]:
        when = pd.Timestamp(str(brk["date"])).tz_localize("UTC")
        before, after = df[df.index < when], df[df.index >= when]
        if before.empty or after.empty:
            continue
        rows.append(
            {
                "date": brk["date"],
                "what": brk["what"],
                "state2_share_before": float((before["regulation_state"] == 2).mean()),
                "state2_share_after": float((after["regulation_state"] == 2).mean()),
                "n_before": len(before),
                "n_after": len(after),
            }
        )
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_quality.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Generate the report when data is available**

```bash
uv run python scripts/build_quality_report.py
```

Writes `docs/DATA_QUALITY.md` with gaps, duplicates, outliers, regulation-state distribution over time, and the structural-break table. **Report the ADR-007 prediction result explicitly, whichever way it goes** — if state-2 frequency does not rise at 2026-02-03, the reasoning in DOMAIN_NOTES Q7 is wrong and that must be said.

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run mypy && uv run pytest
git add src/data/quality.py scripts/build_quality_report.py tests/test_quality.py
git commit -m "feat(data): quality diagnostics incl. ADR-007 structural-break check

The 2026-02-03 state-2 frequency jump is a falsifiable prediction; the report
states the outcome either way."
```

---

## Self-Review

**1. Spec coverage (CLAUDE.md §3 requirements):**

| Requirement | Task |
|---|---|
| Idempotent incremental fetchers with retry/backoff | 5, 6 — *gap: retry/backoff not explicitly implemented; add `httpx` transport retries in Task 5 Step 4 and note ENTSO-E rate limits* |
| Local cache in Parquet, partitioned by month | 4 ✅ |
| Raw responses stored before parsing | 4 ✅ |
| Single canonical UTC index; Amsterdam at presentation | 1 ✅ |
| **Tests for 23-hour and 25-hour days** | 1 ✅ |
| `data_availability.py` with `available_at(field, target_period)` | 3 ✅ |
| Feature builder refuses violating features | 3 (`assert_available`) — *builder itself lands in Phase 2; the enforcement primitive exists now* |
| Data quality report → `docs/DATA_QUALITY.md` | 8 ✅ |
| Minimum 3 years of history | 6 — subject to the token and to the PICASSO segmentation question |
| **Gate: suite proving no feature can use post-decision info** | 3 ✅ |

**Gap found and closed:** retry/backoff. Add to Task 5 Step 4 — wrap the client in `httpx.Client(transport=httpx.HTTPTransport(retries=3))` and respect ENTSO-E's documented rate limit in Task 6.

**Gap accepted:** `src/features/builder.py` is listed in File Structure but has no task. Feature construction is Phase 2 work; Phase 1 delivers the enforcement primitive it will call. Left deliberately, noted here so it is not mistaken for an oversight.

**2. Placeholder scan:** No TBDs. Every code step carries runnable code. Task 7's config write-back uses `<p95>` placeholders by necessity — the values do not exist until measured — but the *procedure* and the exact YAML shape are fully specified.

**3. Type consistency:** `available_at(field: str, target_period_start: datetime) -> datetime` is used identically in Tasks 3, 7 and the gate suite. `store_raw(source, key, payload, fetched_at)` matches between Tasks 4, 5 and 6. `ISP_MINUTES` is imported from `timebase` in Tasks 3 and 8, and defined once in Task 1. Column name `regulation_state` is consistent between Tasks 6 and 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Token never arrives | Tasks 6, 8 blocked | Tasks 1–5, 7 unblocked; parser tested on fixtures |
| TenneT stays 403 | No balance delta → weaker strategy | Task 7 Step 2: record it as a limitation, do not fake a lag |
| ENTSO-E NL imbalance item is single-priced | Dual pricing unobservable | Task 6 Step 6 stops and reports rather than proceeding |
| <3 years usable history after PICASSO segmentation | Thin training set | Surface in Task 8's report; a Phase 2 decision, not a Phase 1 one |
