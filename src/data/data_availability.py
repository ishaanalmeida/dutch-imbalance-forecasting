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


class UnknownVintageError(KeyError):
    """A named vintage was requested that the field does not declare.

    Raised rather than falling back to the field's base rule: silently
    returning the first-publication time when a caller asked for a later
    revision would misreport when that revision was actually retrievable.
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


def require_aware(ts: datetime, label: str) -> datetime:
    """Shared tz-aware guard for a labelled datetime.

    Public so src/data/vintage.py and src/evaluation/walkforward.py can share
    it instead of each reimplementing the identical check -- three copies of
    one guard is exactly the drift risk CLAUDE.md's rigour-zone rules exist to
    prevent (docs/DECISIONS.md ADR-025).
    """
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


def _declared_vintages(field: str, spec: dict[str, object]) -> list[dict[str, object]]:
    """The field's declared vintage schedule, validated. Empty list if none.

    Validates shape rather than trusting it: a malformed `vintages:` block in a
    rigour-zone config must fail loudly here, not produce a confusing
    AttributeError somewhere downstream.
    """
    raw = spec.get("vintages")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise UnknownVintageError(
            f"{field!r} has a 'vintages' key that is not a list "
            f"({type(raw).__name__}). This is a config error."
        )
    entries: list[dict[str, object]] = []
    for entry in raw:
        if not isinstance(entry, dict) or "name" not in entry:
            raise UnknownVintageError(
                f"{field!r} has a malformed vintage entry {entry!r}; each entry "
                f"must be a mapping with a 'name'. This is a config error."
            )
        entries.append(dict(entry))
    return entries


def _vintage_spec(field: str, spec: dict[str, object], vintage: str) -> dict[str, object]:
    """The sub-spec for a named vintage, or raise.

    Never falls back to the field's base rule: a caller that asked for the
    intraday revision and silently got the day-ahead publication time would
    believe a later revision was available earlier than it was.
    """
    declared = _declared_vintages(field, spec)
    if not declared:
        raise UnknownVintageError(
            f"{field!r} declares no named vintages, so {vintage!r} cannot be "
            f"resolved. Call available_at() without a vintage argument to get "
            f"its single first-publication time."
        )
    for entry in declared:
        if entry["name"] == vintage:
            return entry
    names = [entry["name"] for entry in declared]
    raise UnknownVintageError(
        f"{field!r} has no vintage named {vintage!r}. Declared vintages: {names}."
    )


def available_at(field: str, target_period_start: datetime, vintage: str | None = None) -> datetime:
    """The instant `field` for the ISP starting at `target_period_start` first
    became retrievable, in UTC.

    `vintage` selects a named revision for fields that declare a vintage
    schedule (see `config/market_rules.yaml`). Omitting it returns the
    **earliest** vintage — the conservative default, so code that does not
    reason about revisions can never accidentally read a later one.

    Raises UnresolvedLagError if the lag is not established, UnknownFieldError
    if the field is not declared, UnknownVintageError if a named vintage is
    requested that does not exist, and ValueError if `target_period_start` is
    naive or not aligned to an ISP boundary.
    """
    target_period_start = require_aware(target_period_start, "target_period_start")
    target_period_start = _require_isp_aligned(target_period_start.astimezone(UTC))
    spec = _spec(field)

    # The unresolved check runs against the PARENT spec before any vintage is
    # resolved, so a vintage argument can never route around it.
    if spec.get("rule") == "unresolved":
        raise UnresolvedLagError(
            f"The publication lag for {field!r} is not established "
            f"(lag_confidence={spec.get('lag_confidence', spec.get('confidence'))!r}). "
            f"It must be measured empirically and written back to "
            f"config/market_rules.yaml with evidence before this field may be "
            f"used. See docs/DECISIONS.md ADR-006."
        )

    if vintage is not None:
        spec = _vintage_spec(field, spec, vintage)

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

    # Wall-clock publication rules. These express "published at HH:MM local on
    # the day before / the same day / the day after delivery", which is how
    # scheduled market processes actually work: one run covers a whole delivery
    # day at once, rather than each period becoming available a fixed offset
    # after itself.
    _DAY_OFFSET = {
        "published_day_before_at": -1,
        "published_same_day_at": 0,
        "published_day_after_at": +1,
    }
    if rule in _DAY_OFFSET:
        tz = ZoneInfo(str(spec["timezone"]))
        hh, mm = (int(part) for part in str(spec["local_time"]).split(":"))
        # The LOCAL delivery date, not the UTC one. For an ISP late in the UTC
        # day these differ, and using the UTC date would shift publication by a
        # whole day -- in the permissive direction.
        local_delivery = target_period_start.astimezone(tz)
        publish_date = local_delivery.date() + timedelta(days=_DAY_OFFSET[str(rule)])
        publish_local = datetime.combine(publish_date, time(hh, mm), tzinfo=tz)
        return publish_local.astimezone(UTC)

    raise UnknownFieldError(
        f"{field!r} has unhandled publication rule {rule!r}. Known rules: "
        f"'lag_after_period', 'published_day_before_at', "
        f"'published_same_day_at', 'published_day_after_at', 'unresolved'."
    )


def is_available(
    field: str,
    target_period_start: datetime,
    decision_time: datetime,
    vintage: str | None = None,
) -> bool:
    """True iff the datum was published STRICTLY before `decision_time`.

    R1 is strict, not inclusive: a datum published exactly at the decision
    instant is not usable, because "published at" and "retrievable strictly
    before" are not the same guarantee.
    """
    decision_time = require_aware(decision_time, "decision_time")
    return available_at(field, target_period_start, vintage) < decision_time.astimezone(UTC)


def available_vintages(
    field: str, target_period_start: datetime, decision_time: datetime
) -> list[str]:
    """Names of the vintages of `field` visible at `decision_time`, earliest first.

    This is what the feature builder needs in order to compute forecast-revision
    features (CLAUDE.md §4). Whether two vintages exist is a **per-period** fact,
    not a global one: the 07:00 intraday wind/solar update is not available for
    target periods early on the delivery day, so a builder that assumes both
    vintages always exist would read the future for every early-morning ISP.

    Returns `[]` for a field with no declared vintage schedule — absence of a
    schedule is not the same as a single unnamed vintage, and callers should
    use `available_at`/`is_available` for those.
    """
    declared = _declared_vintages(field, _spec(field))
    return [
        str(entry["name"])
        for entry in declared
        if is_available(field, target_period_start, decision_time, vintage=str(entry["name"]))
    ]


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
