"""Board version — one number, one place.

Bump :data:`VERSION` when a build is worth naming. :func:`display` adds a
short git revision when ``.git`` is present so ``/ver`` can tell a stale
process even if the number was not bumped.
"""

from __future__ import annotations

from pathlib import Path

NAME = "Modulo BBS"
VERSION = "0.2.0"

_ROOT = Path(__file__).resolve().parent.parent


def git_revision(root: Path | None = None) -> str:
    """Short git object name, or ``\"\"`` if this tree is not a clone."""
    git_dir = (root or _ROOT) / ".git"
    if not git_dir.is_dir():
        return ""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        ref_file = git_dir / ref
        try:
            return (ref_file.read_text(encoding="utf-8").strip())[:7]
        except OSError:
            return _packed_ref(git_dir, ref)
    return head[:7]


def _packed_ref(git_dir: Path, ref: str) -> str:
    packed = git_dir / "packed-refs"
    try:
        text = packed.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == ref:
            return parts[0][:7]
    return ""


def display(root: Path | None = None) -> str:
    """Human line for ``/ver`` and banners: ``0.2.0`` or ``0.2.0 (abc1234)``."""
    rev = git_revision(root)
    if rev:
        return f"{VERSION} ({rev})"
    return VERSION
