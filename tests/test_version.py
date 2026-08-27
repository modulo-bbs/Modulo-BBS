"""Tests for core/version.py and /ver."""
from __future__ import annotations

from pathlib import Path

from core.version import VERSION, display, git_revision


def test_version_is_dotted():
    parts = VERSION.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts[:2])


def test_git_revision_from_fake_repo(tmp_path: Path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "master").write_text(
        "abcdef1234567890\n", encoding="utf-8"
    )
    assert git_revision(tmp_path) == "abcdef1"
    assert display(tmp_path) == f"{VERSION} (abcdef1)"


def test_display_without_git(tmp_path: Path):
    assert git_revision(tmp_path) == ""
    assert display(tmp_path) == VERSION
