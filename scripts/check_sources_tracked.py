"""Fail if any source file exists on disk but is not tracked by git.

This repo shipped an unanchored `data/` ignore rule in Phase 0. Because
`data/` matches a directory of that name at ANY depth, it silently shadowed
`src/data/` — a real package — and `git add` skipped files there without
error for four commits. Nothing failed; the files simply were not in the
commits. A clean clone would have been missing part of the source tree.

`git add` staying silent on ignored paths is the whole problem, so this check
exists to make that silence loud. Run by pre-commit and by CI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SOURCE_DIRS = ("src", "scripts", "tests")
REPO_ROOT = Path(__file__).resolve().parents[1]


def untracked_sources() -> list[Path]:
    """Python files under the source dirs that git is not tracking."""
    tracked = set(
        subprocess.run(
            ["git", "ls-files", *SOURCE_DIRS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )

    missing: list[Path] = []
    for directory in SOURCE_DIRS:
        for path in (REPO_ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative not in tracked:
                missing.append(path.relative_to(REPO_ROOT))
    return sorted(missing)


def main() -> int:
    missing = untracked_sources()
    if not missing:
        return 0

    print("Source files exist on disk but are NOT tracked by git:", file=sys.stderr)
    for path in missing:
        print(f"  {path.as_posix()}", file=sys.stderr)
    print(
        "\nThis usually means a .gitignore pattern is shadowing a source directory.\n"
        "Check with: git check-ignore -v <path>",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
