"""Lint test: unscoped access must only appear in allowed locations — TODO 0.3.4.

``allow_unscoped()`` and ``.unscoped()`` are greppable escape hatches for the
tenant scoping guard.  They are legitimate in:
  - tests/         (test setup and assertions)
  - management/     (management commands like seed, backup)
  - migrations/     (data migrations that operate across tenants)

They must NEVER appear in application code (views, services, models, managers).
A developer adding one there is introducing a cross-tenant leak.

This test scans all Python files under apps/ and fails if it finds either
pattern outside the allowed list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"

# Patterns that represent deliberate unscoped access
PATTERNS = [
    re.compile(r"allow_unscoped\b"),
    re.compile(r"\.unscoped\("),
]

# Directories within apps/ where unscoped access is legitimate
ALLOWED_SUFFIXES = (
    "migrations/",
    "management/",
)

# Files that are the definition itself or are explicitly approved exceptions.
# Each entry is relative to ROOT, e.g. "apps/core/scoping.py".
ALLOWED_FILES: set[str] = {
    "apps/core/scoping.py",  # defines allow_unscoped — must not match itself
    "apps/identity/actors.py",  # builds actor scope from user's own role assignments
}


def _is_allowed_file(rel_path: str) -> bool:
    """True if the file is explicitly whitelisted."""
    normalized = rel_path.replace("\\", "/")
    return normalized in ALLOWED_FILES


def _code_only(text: str) -> str:
    """Strip comments and string literals from Python source.

    We only care about whether ``allow_unscoped`` or ``.unscoped(`` appears
    in executable code, not in docstrings, comments, or string arguments
    (e.g. error messages that mention the function name).
    """
    # Remove multi-line strings (docstrings, triple-quoted) first
    result = re.sub(r'"""[\s\S]*?"""', '""', text)
    result = re.sub(r"'''[\s\S]*?'''", "''", result)
    # Remove single-line comments
    result = re.sub(r"#[^\n]*", "", result)
    # Remove single/double quoted strings (non-greedy, no newlines)
    result = re.sub(r'"[^"\n]*"', '""', result)
    result = re.sub(r"'[^'\n]*'", "''", result)
    return result


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for every real code match in a file.

    Matches inside string literals and comments are ignored — those are
    documentation, not unscoped access.
    """
    hits = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits

    code_text = _code_only(text)
    for line_no, (orig_line, code_line) in enumerate(
        zip(text.splitlines(), code_text.splitlines(), strict=False), start=1
    ):
        for pattern in PATTERNS:
            if pattern.search(code_line):
                hits.append((line_no, orig_line.strip()))
                break
    return hits


def _is_allowed(path: Path) -> bool:
    """True if the file is in an allowed directory or is whitelisted."""
    relative = path.relative_to(ROOT)
    rel_str = str(relative).replace("\\", "/")
    if _is_allowed_file(rel_str):
        return True
    for suffix in ALLOWED_SUFFIXES:
        if any(part == suffix.rstrip("/") for part in relative.parts):
            return True
    return False


@pytest.mark.parametrize(
    "py_file",
    sorted(APPS_DIR.rglob("*.py")),
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_no_unscoped_in_application_code(py_file: Path):
    """Application code under apps/ must not bypass tenant scoping."""
    if _is_allowed(py_file):
        rel = str(py_file.relative_to(ROOT)).replace("\\", "/")
        if _is_allowed_file(rel):
            pytest.skip(f"whitelisted file ({rel})")
        pytest.skip("allowed location (migration/management)")

    hits = _scan_file(py_file)
    if not hits:
        return  # clean

    detail = "\n".join(f"  L{no}: {line}" for no, line in hits)
    rel = py_file.relative_to(ROOT)
    pytest.fail(
        f"Found unscoped access in application code ({rel}):\n{detail}\n\n"
        f"allow_unscoped() and .unscoped() are only permitted in tests, "
        f"management commands, and migrations."
    )
