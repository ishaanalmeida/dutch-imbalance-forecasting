"""Load `.env` into the process environment.

Lean zone. `python-dotenv` would do this too, but it is ~15 lines of stdlib for
the subset we need and a dependency has to earn its place (CLAUDE.md §12).

Real environment variables always win over the file, so CI — which sets
`ENTSOE_API_TOKEN=""` explicitly — cannot be overridden by a stray local `.env`,
and a developer can override the file for one command without editing it.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_env(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Read KEY=VALUE lines into `os.environ`. Returns what was applied.

    Ignores blank lines and `#` comments. Strips one layer of matching quotes,
    because `KEY="value"` is a common shape and the quotes are not part of the
    value. Missing file is not an error: credentials are optional for the parts
    of this project that do not need them.
    """
    path = path or ENV_PATH
    if not path.exists():
        return {}

    applied: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value:
            continue
        # A real environment variable is a deliberate act; a file is a default.
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
        applied[key] = value
    return applied
