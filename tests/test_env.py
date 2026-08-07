"""The .env loader. R8: credentials come from the file, never from code."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.env import load_env


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_key_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_TOKEN", raising=False)
    load_env(_write(tmp_path, "SOME_TOKEN=abc123\n"))
    assert os.environ["SOME_TOKEN"] == "abc123"


def test_ignores_comments_blanks_and_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REAL", raising=False)
    applied = load_env(_write(tmp_path, "# comment\n\nnot_a_pair\nREAL=yes\n"))
    assert applied == {"REAL": "yes"}


def test_strips_matching_quotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("Q", raising=False)
    load_env(_write(tmp_path, 'Q="quoted value"\n'))
    assert os.environ["Q"] == "quoted value"


def test_empty_value_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` ships with empty placeholders; loading them as empty
    strings would look like 'credential present' to a naive check."""
    monkeypatch.delenv("EMPTY", raising=False)
    assert load_env(_write(tmp_path, "EMPTY=\n")) == {}
    assert "EMPTY" not in os.environ


def test_real_environment_wins_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI sets ENTSOE_API_TOKEN="" deliberately. A stray local .env must not
    override a real environment variable."""
    monkeypatch.setenv("WINS", "from-environment")
    load_env(_write(tmp_path, "WINS=from-file\n"))
    assert os.environ["WINS"] == "from-environment"


def test_override_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("W2", "from-environment")
    load_env(_write(tmp_path, "W2=from-file\n"), override=True)
    assert os.environ["W2"] == "from-file"


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_env(tmp_path / "nope.env") == {}
