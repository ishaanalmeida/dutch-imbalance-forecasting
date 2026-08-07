"""Dutch imbalance settlement, driven entirely by ``config/market_rules.yaml``.

RIGOUR ZONE (CLAUDE.md §12) — ponytail simplification does not apply here.

The settlement rule table is *data*, not code: it lives in the YAML so that the
rules exist in exactly one place (CLAUDE.md §2). This module resolves that table
rather than restating it, so a rule change is a config change and cannot drift
out of sync with `docs/DOMAIN_NOTES.md`.

The resolver deliberately supports only the four tokens the rule table uses and
raises on anything else. It is not `eval`: an unexpected expression must fail
loudly rather than silently evaluate to something plausible.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "market_rules.yaml"

_PRICE_TOKENS = ("p_up", "p_down", "p_mid")
_FUNC_RE = re.compile(r"^(min|max)\(\s*([a-z_]+)\s*,\s*([a-z_]+)\s*\)$")


@lru_cache(maxsize=1)
def load_rules(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """Load and cache the market rules. Cached because it is read per-ISP."""
    with open(path, encoding="utf-8") as fh:
        rules: dict[str, Any] = yaml.safe_load(fh)
    return rules


def _resolve(expr: str, prices: dict[str, float]) -> float:
    """Resolve one rule-table expression against the ISP's component prices."""
    expr = expr.strip()
    if expr in _PRICE_TOKENS:
        return prices[expr]

    match = _FUNC_RE.match(expr)
    if match is None:
        raise ValueError(
            f"Unsupported expression in market_rules.yaml pricing table: {expr!r}. "
            f"Supported: {_PRICE_TOKENS}, min(a, b), max(a, b)."
        )
    func, left, right = match.groups()
    for token in (left, right):
        if token not in _PRICE_TOKENS:
            raise ValueError(f"Unknown price token {token!r} in expression {expr!r}.")
    return (min if func == "min" else max)(prices[left], prices[right])


def imbalance_prices(
    regulation_state: int,
    p_up: float | None,
    p_down: float | None,
    p_mid: float | None,
) -> tuple[float, float]:
    """Settlement prices for one ISP, as ``(price_long, price_short)`` in EUR/MWh.

    ``price_long`` settles a BRP surplus (injected more / withdrew less than
    schedule); ``price_short`` settles a BRP shortage. In regulation states
    0, +1 and -1 the two are equal; only state 2 is dual-priced.

    Component prices that the state's rule does not reference may be ``None``
    (e.g. ``p_up`` is irrelevant in state -1). A component the rule *does*
    reference must be present, or this raises.
    """
    rules = load_rules()["pricing"]["rules"]
    if regulation_state not in rules:
        raise KeyError(
            f"Unknown regulation state {regulation_state!r}; known states: {sorted(rules)}."
        )

    supplied = {"p_up": p_up, "p_down": p_down, "p_mid": p_mid}
    rule = rules[regulation_state]

    resolved = []
    for side in ("price_long", "price_short"):
        expr = rule[side]
        needed = [t for t in _PRICE_TOKENS if t in expr]
        missing = [t for t in needed if supplied[t] is None]
        if missing:
            raise ValueError(
                f"Regulation state {regulation_state} rule {side}={expr!r} "
                f"requires {missing}, which were not supplied."
            )
        resolved.append(_resolve(expr, {k: v for k, v in supplied.items() if v is not None}))

    return resolved[0], resolved[1]


def cash_to_brp(
    price_long: float,
    price_short: float,
    e_surplus_mwh: float,
    e_shortage_mwh: float,
) -> float:
    """Net cash flow *to* the BRP for one ISP, in EUR. Negative means the BRP pays.

    ``e_surplus_mwh`` and ``e_shortage_mwh`` are positive magnitudes; a BRP has
    at most one of them non-zero in any ISP. This single expression reproduces
    every row of the direction-of-payment table in the TenneT Imbalance Pricing
    System document, including the negative-price rows where the direction of
    payment flips.
    """
    if e_surplus_mwh < 0 or e_shortage_mwh < 0:
        raise ValueError(
            "e_surplus_mwh and e_shortage_mwh are magnitudes and must be >= 0; "
            f"got {e_surplus_mwh} and {e_shortage_mwh}."
        )
    return price_long * e_surplus_mwh - price_short * e_shortage_mwh
