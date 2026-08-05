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

from src.data.timebase import ISP_MINUTES, isp_start_of
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


def _require_isp_aligned(ts: datetime) -> datetime:
    """Refuse a target_period_start that is not itself an ISP boundary.

    A misaligned timestamp (e.g. an ISP end, or an arbitrary intra-ISP instant)
    would silently be treated as a different ISP's start by the arithmetic
    below, producing a wrong-but-plausible availability time. Failing loudly
    here is cheap; the alternative is a look-ahead bug that only shows up as an
    unexplained edge in a backtest.
    """
    aligned = isp_start_of(ts)
    if aligned != ts:
        raise ValueError(
            f"target_period_start {ts.isoformat()!r} is not aligned to an "
            f"ISP boundary (expected {aligned.isoformat()!r}). Pass the start "
            f"of the ISP, not an arbitrary instant within it."
        )
    return ts


def available_at(field: str, target_period_start: datetime) -> datetime:
    """The instant `field` for the ISP starting at `target_period_start` first
    became retrievable, in UTC.

    Raises UnresolvedLagError if the lag is not established, UnknownFieldError
    if the field is not declared, and ValueError if `target_period_start` is
    naive or not aligned to an ISP boundary.
    """
    target_period_start = _require_aware(target_period_start, "target_period_start")
    target_period_start = _require_isp_aligned(target_period_start.astimezone(UTC))
    spec = _spec(field)
    rule = spec.get("rule")

    # Dispatch on `rule` FIRST, before any `lag_seconds` is read. A field whose
    # lag is unresolved must refuse here even if a stray numeric lag_seconds
    # were ever (incorrectly) present in the config — see ADR-009.
    if rule == "unresolved":
        raise UnresolvedLagError(
            f"The publication lag for {field!r} is not established "
            f"(lag_confidence={spec.get('lag_confidence', spec.get('confidence'))!r}). "
            f"It must be measured empirically and written back to "
            f"config/market_rules.yaml with evidence before this field may be "
            f"used. See docs/DECISIONS.md ADR-006."
        )

    if rule == "lag_after_period":
        lag = spec.get("lag_seconds")
        if lag is None:
            raise UnresolvedLagError(
                f"{field!r} declares rule: lag_after_period but lag_seconds is "
                f"null. This is a config error: either set a measured "
                f"lag_seconds, or change the rule to 'unresolved'."
            )
        if isinstance(lag, bool) or not isinstance(lag, int):
            # isinstance(True, int) is True in Python, and PyYAML 1.1 parses
            # yes/on/true as booleans -- excluding bool explicitly stops
            # `lag_seconds: yes` from silently becoming a 1-second lag.
            raise UnresolvedLagError(
                f"{field!r} lag_seconds must be an int number of seconds, got "
                f"{type(lag).__name__}: {lag!r}. This is a config error in "
                f"config/market_rules.yaml."
            )
        if lag < 0:
            # This repo shipped exactly this bug once already as the
            # `lag_seconds: -1` sentinel (see this module's test file's
            # docstring). A negative lag_after_period value would mean the
            # datum is available before the period it describes even ENDS,
            # which this rule can never legitimately express.
            raise UnresolvedLagError(
                f"{field!r} lag_seconds is negative ({lag!r}). "
                f"lag_after_period is measured from the period's END, so a "
                f"negative value would make the datum available before its "
                f"own period ends -- always a config error. Use "
                f"'published_day_before_at' for genuinely pre-delivery "
                f"fields, or fix the config."
            )
        period_end = target_period_start + timedelta(minutes=ISP_MINUTES)
        return period_end + timedelta(seconds=lag)

    if rule == "published_day_before_at":
        tz = ZoneInfo(str(spec["timezone"]))
        hh, mm = (int(part) for part in str(spec["local_time"]).split(":"))
        local_delivery = target_period_start.astimezone(tz)
        publish_local = datetime.combine(
            local_delivery.date() - timedelta(days=1), time(hh, mm), tzinfo=tz
        )
        return publish_local.astimezone(UTC)

    raise UnknownFieldError(
        f"{field!r} has unhandled publication rule {rule!r}. Known rules: "
        f"'lag_after_period', 'published_day_before_at', 'unresolved'."
    )


def is_available(field: str, target_period_start: datetime, decision_time: datetime) -> bool:
    """True iff the datum was published STRICTLY before `decision_time`.

    R1 is strict, not inclusive: a datum published exactly at the decision
    instant is not usable, because "published at" and "retrievable strictly
    before" are not the same guarantee.
    """
    decision_time = _require_aware(decision_time, "decision_time")
    return available_at(field, target_period_start) < decision_time.astimezone(UTC)


def assert_available(field: str, target_period_start: datetime, decision_time: datetime) -> None:
    """Raise LookAheadError unless the datum was published before the decision.

    This is the call the feature builder and backtest engine are expected to
    make on every field they touch — the check is designed to be cheap enough
    to call unconditionally rather than something engineers reach for only
    when they remember to.
    """
    if not is_available(field, target_period_start, decision_time):
        published = available_at(field, target_period_start)
        # Normalise all three instants to UTC: `published` is UTC by
        # construction, but target_period_start/decision_time are whatever
        # tz the caller passed. Printing a mix of offsets in one message is
        # the kind of thing that looks fine until someone reads it at 2am.
        target_utc = target_period_start.astimezone(UTC)
        decision_utc = decision_time.astimezone(UTC)
        raise LookAheadError(
            f"R1 violation: {field!r} for ISP {target_utc.isoformat()} "
            f"is available at {published.isoformat()}, which is not strictly "
            f"before the decision time {decision_utc.isoformat()}."
        )
