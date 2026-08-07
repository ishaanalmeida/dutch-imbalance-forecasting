"""The tracked-sources guard must actually detect an untracked source file.

A guard that always returns "clean" is worse than no guard, because it is
believed.

Note on scope: these tests deliberately do NOT assert that the whole repo is
currently clean. `git ls-files` reads the index, so any file a developer has
created but not yet staged would fail such an assertion — including the very
files being added in the commit that introduces this guard. That would make the
suite red during normal work and train people to ignore it. The whole-repo
assertion belongs in CI, where the tree is committed, and it lives there in
`.github/workflows/ci.yml`. Here we test the function's logic instead.
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_sources_tracked import untracked_sources

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_detects_a_genuinely_untracked_source_file() -> None:
    probe = REPO_ROOT / "src" / "_probe_untracked.py"
    probe.write_text("# temporary probe file\n", encoding="utf-8")
    try:
        assert probe.relative_to(REPO_ROOT) in untracked_sources()
    finally:
        probe.unlink()

    assert probe.relative_to(REPO_ROOT) not in untracked_sources()


def test_does_not_flag_a_tracked_source_file() -> None:
    """`src/market.py` is committed, so it must never appear in the result."""
    assert Path("src/market.py") not in untracked_sources()


def test_ignores_pycache() -> None:
    """Compiled artefacts are not source and must not be reported."""
    cache_dir = REPO_ROOT / "src" / "__pycache__"
    cache_dir.mkdir(exist_ok=True)
    probe = cache_dir / "_probe_cached.py"
    probe.write_text("# should be ignored\n", encoding="utf-8")
    try:
        assert all("__pycache__" not in p.parts for p in untracked_sources())
    finally:
        probe.unlink()
