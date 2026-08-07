"""Command-line interface. `uv run python -m src.cli <command>`.

Lean zone: argparse from the stdlib, plain text output, no framework and no
colour library. The point is that every command prints something a human can
read and paste into a message.

Commands map to the things worth inspecting by hand:

    status        what is built, what is blocked, and why
    availability  the R1 gate made visible for one ISP
    settle        the settlement rules on a worked example
    log-vintage   record the current forecast (the track record)
    track-record  what the vintage log has accumulated so far
    reparse       rebuild the Parquet cache from stored raw responses
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime

from src.data import cache, vintage
from src.data.data_availability import (
    UnknownVintageError,
    UnresolvedLagError,
    available_at,
    available_vintages,
    is_available,
)
from src.data.timebase import isp_start_of
from src.market import cash_to_brp, imbalance_prices, load_rules

_RULE = "-" * 72


def _heading(text: str) -> None:
    print(f"\n{text}\n{_RULE}")


# --- status -----------------------------------------------------------------


def cmd_status(_: argparse.Namespace) -> int:
    rules = load_rules()

    _heading("MARKET RULES")
    print(f"  market            {rules['meta']['market']} ({rules['meta']['tso']})")
    print(f"  rules version     {rules['meta']['rules_version']}")
    print(f"  ISP length        {rules['isp']['length_minutes']} min")
    print(
        f"  regulation states {sorted(k for k in rules['regulation_states'] if isinstance(k, int))}"
    )
    print(
        f"  incentive comp.   {'active' if rules['incentive_component']['active'] else 'abolished'}"
    )

    _heading("PUBLICATION FIELDS")
    for field, spec in rules["publication"].items():
        rule = spec["rule"]
        detail = ""
        if rule == "lag_after_period":
            detail = f"lag={spec.get('lag_seconds')}s"
        elif rule in ("published_day_before_at", "published_same_day_at"):
            detail = f"{spec.get('local_time')} {spec.get('timezone')}"
        vintages = [v["name"] for v in spec.get("vintages") or []]
        flags = []
        if spec.get("revised"):
            flags.append("revised")
        if vintages:
            flags.append(f"vintages={vintages}")
        marker = "  !!" if rule == "unresolved" else "    "
        print(f"{marker} {field:<38} {rule:<24} {detail} {' '.join(flags)}")
    print("\n  '!!' = lag unresolved: data_availability REFUSES to serve this field.")

    _heading("CREDENTIALS")
    token = os.getenv("ENTSOE_API_TOKEN")
    print(f"  ENTSOE_API_TOKEN  {'set' if token else 'NOT SET  -> ENTSO-E fetchers unusable'}")
    print("  Open-Meteo        no credentials required")
    print("  TenneT            registration at developer.tennet.eu required")
    print("                    (see docs/DATA_SOURCES.md)")

    _heading("LOCAL DATA")
    log = vintage.read_vintages("weather_forecast")
    if log.empty:
        print("  vintage log       EMPTY -- run `log-vintage` to start the track record")
    else:
        print(f"  vintage log       {len(log)} rows, {log['observed_at'].nunique()} vintage(s)")
        print(f"                    first observed {log['observed_at'].min()}")
        print(f"                    last  observed {log['observed_at'].max()}")
    root = cache.DATA_ROOT / "processed"
    datasets = sorted(p.name for p in root.glob("*") if p.is_dir()) if root.exists() else []
    print(f"  parquet cache     {datasets or 'empty'}")

    _heading("BLOCKED ON")
    print("  1. ENTSO-E token  -> all four ENTSO-E fetchers unverified")
    print("  2. TenneT signup  -> balance-delta lag unmeasured, so the field stays refused")
    return 0


# --- availability -----------------------------------------------------------


def cmd_availability(args: argparse.Namespace) -> int:
    isp = (
        datetime.fromisoformat(args.isp).astimezone(UTC)
        if args.isp
        else isp_start_of(datetime.now(UTC))
    )
    isp = isp_start_of(isp)
    decision = isp  # ADR-005: the decision for ISP t is taken at start(t)

    _heading(f"AVAILABILITY FOR ISP {isp.isoformat()}  (decision at ISP start)")
    print(f"  {'field':<38} {'published at':<28} usable?")
    print(f"  {'-' * 38} {'-' * 28} -------")

    for field in load_rules()["publication"]:
        try:
            published = available_at(field, isp)
            usable = is_available(field, isp, decision)
            mark = "YES" if usable else "no"
            print(f"  {field:<38} {published.isoformat():<28} {mark}")
        except UnresolvedLagError:
            print(f"  {field:<38} {'REFUSED (lag unresolved)':<28} --")

        for name in [v["name"] for v in load_rules()["publication"][field].get("vintages") or []]:
            try:
                vp = available_at(field, isp, vintage=name)
                vu = "YES" if is_available(field, isp, decision, vintage=name) else "no"
                print(f"    +-- vintage {name:<26} {vp.isoformat():<28} {vu}")
            except (UnresolvedLagError, UnknownVintageError):
                pass

    names = available_vintages("wind_solar_forecast_day_ahead", isp, decision)
    print(f"\n  wind/solar vintages visible: {names or 'none'}")
    print(
        "  forecast-error proxy computable: "
        f"{'yes' if len(names) >= 2 else 'NO -- needs two vintages'}"
    )
    return 0


# --- settle -----------------------------------------------------------------


def cmd_settle(args: argparse.Namespace) -> int:
    p_up, p_down, p_mid = args.p_up, args.p_down, args.p_mid
    energy = args.mwh

    _heading("SETTLEMENT (TenneT Imbalance Pricing System v6.1, Table 2)")
    print(f"  component prices: p_up={p_up}  p_down={p_down}  p_mid={p_mid}  EUR/MWh")
    print(f"  battery moves {energy} MWh relative to schedule\n")
    print(
        f"  {'state':<8} {'price_long':>11} {'price_short':>12}   {'discharge':>10} {'charge':>10}"
    )
    print(f"  {'-' * 8} {'-' * 11} {'-' * 12}   {'-' * 10} {'-' * 10}")

    for state in (0, 1, -1, 2):
        long_p, short_p = imbalance_prices(state, p_up, p_down, p_mid)
        discharge = cash_to_brp(long_p, short_p, energy, 0.0)  # BRP surplus
        charge = cash_to_brp(long_p, short_p, 0.0, energy)  # BRP shortage
        print(
            f"  {state:<8} {long_p:>11.2f} {short_p:>12.2f}   {discharge:>+10.2f} {charge:>+10.2f}"
        )

    print("\n  Cash is TO the battery: negative means it pays.")
    print("  State 2 is the only dual-priced state, and with these prices it is")
    print("  loss-making in BOTH directions -- which is why a point forecast that")
    print("  misses a state-2 period books revenue where reality books a penalty.")
    return 0


# --- vintage log ------------------------------------------------------------


def cmd_log_vintage(args: argparse.Namespace) -> int:
    from src.jobs.log_weather_vintage import run

    observed_at = datetime.now(UTC)
    rows = run(forecast_days=args.forecast_days, observed_at=observed_at)
    print(f"recorded {rows} target periods, observed_at={observed_at.isoformat()}")
    return 0 if rows else 1


def cmd_track_record(_: argparse.Namespace) -> int:
    log = vintage.read_vintages("weather_forecast")
    _heading("VINTAGE LOG -- weather_forecast")
    if log.empty:
        print("  empty. Run `log-vintage` (or let the 6-hourly CI cron run) to start it.")
        print("  This is the one artefact that cannot be back-filled later.")
        return 1

    print(f"  rows              {len(log)}")
    print(f"  distinct vintages {log['observed_at'].nunique()}")
    print(f"  target range      {log['target_time'].min()}  ..  {log['target_time'].max()}")
    print("\n  observations:")
    for observed, group in log.groupby("observed_at"):
        print(f"    {observed}  ->  {len(group)} target periods")

    if log["observed_at"].nunique() >= 2:
        wide = log.pivot(index="target_time", columns="observed_at", values="wind_speed_100m")
        revision = (wide.iloc[:, -1] - wide.iloc[:, 0]).dropna()
        if not revision.empty:
            _heading("FORECAST REVISION (latest vintage minus first)")
            print(f"  overlapping target periods {len(revision)}")
            print(f"  mean revision   {revision.mean():+.3f} km/h wind at 100m")
            print(f"  max  |revision| {revision.abs().max():.3f}")
            print("\n  This is the forecast-error proxy CLAUDE.md section 4 asks for. It exists")
            print("  only because both vintages were kept.")
    else:
        print("\n  Only one vintage so far -- revision features need at least two.")
    return 0


# --- reparse ----------------------------------------------------------------


def cmd_reparse(args: argparse.Namespace) -> int:
    """Rebuild the Parquet cache from stored raw responses, without re-fetching.

    This is why raw responses are persisted before parsing: a parser change must
    never cost an API call or burn quota.
    """
    import json

    from src.data.openmeteo import _parse as parse_openmeteo

    _heading("REPARSE -- rebuilding parsed cache from stored raw responses")
    raw_dir = cache.DATA_ROOT / "raw" / "openmeteo"
    if not raw_dir.exists():
        print("  no stored raw openmeteo responses. Nothing to reparse.")
        return 1

    rebuilt = 0
    skipped = 0
    for sidecar in sorted(raw_dir.glob("*.json")):
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        key = meta["key"]
        if key.startswith("live_"):
            # Live forecasts are VINTAGES, not settled observations. Feeding
            # them into the parsed cache would collapse successive vintages of
            # the same target hour into one row and destroy the revision
            # history. They are replayed via the vintage store instead.
            skipped += 1
            continue
        payload = cache.load_raw("openmeteo", key)
        if payload is None:
            print(f"  MISSING payload for {key}")
            continue
        frame = parse_openmeteo(json.loads(payload))
        if frame.empty:
            print(f"  empty        {key}")
            continue
        if not args.dry_run:
            cache.write_frame("weather_forecast", frame)
        print(f"  {'would parse' if args.dry_run else 'reparsed'}   {key}  ({len(frame)} rows)")
        rebuilt += 1

    print(f"\n  {rebuilt} response(s) reparsed{' (dry run)' if args.dry_run else ''}.")
    if skipped:
        print(f"  {skipped} live-forecast response(s) skipped: those are vintages,")
        print("  replayed through the vintage store, not the settled cache.")
    return 0 if rebuilt else 1


# --- wiring -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="what is built, what is blocked").set_defaults(fn=cmd_status)

    p_avail = sub.add_parser("availability", help="the R1 gate, for one ISP")
    p_avail.add_argument("--isp", help="ISP start, ISO 8601 (default: current ISP)")
    p_avail.set_defaults(fn=cmd_availability)

    p_settle = sub.add_parser("settle", help="settlement on a worked example")
    p_settle.add_argument("--p-up", type=float, default=120.0)
    p_settle.add_argument("--p-down", type=float, default=-15.0)
    p_settle.add_argument("--p-mid", type=float, default=30.0)
    p_settle.add_argument("--mwh", type=float, default=10.0)
    p_settle.set_defaults(fn=cmd_settle)

    p_log = sub.add_parser("log-vintage", help="record the current forecast")
    p_log.add_argument("--forecast-days", type=int, default=7)
    p_log.set_defaults(fn=cmd_log_vintage)

    sub.add_parser("track-record", help="what the vintage log has accumulated").set_defaults(
        fn=cmd_track_record
    )

    p_reparse = sub.add_parser("reparse", help="rebuild cache from raw, no refetch")
    p_reparse.add_argument("--dry-run", action="store_true")
    p_reparse.set_defaults(fn=cmd_reparse)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.fn(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
