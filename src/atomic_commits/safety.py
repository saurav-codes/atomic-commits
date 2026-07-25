"""Safety filtering for atc (implementation.md section 9).

Safety must run before AI sees any content and before staging. This module
classifies paths and content as safe/unsafe.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .models import SafetyResult

# 9.1 Always-exclude path components.
EXCLUDED_DIR_COMPONENTS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", "dist", "build", "target",
    "coverage", ".coverage", ".next", ".nuxt", ".turbo", ".cache",
    "logs", "tmp", "temp", ".DS_Store",
}

# Filename denylist, combined into a single precompiled alternation regex
# for speed on large file lists. Matched with re.match (anchored at start),
# so the leading ^ anchors from the original per-pattern forms are redundant
# and omitted; trailing $ anchors are kept to pin exact/exact-suffix matches.
EXCLUDED_FILENAME_RE = re.compile(
    "|".join(
        [
            r"\.env$",
            r"\.env\..+",
            r".*\.pem$",
            r".*\.key$",
            r".*\.p12$",
            r".*\.pfx$",
            r"id_rsa$",
            r"id_ed25519$",
            r"known_hosts$",
            r".*\.log$",
            r".*\.sqlite$",
            r".*\.db$",
        ]
    )
)

# Sample files that are allowed despite matching .env.* if they look safe.
SAMPLE_ENV_RE = re.compile(r"\.env\.(example|sample|template|dist)$")

SAFE_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".pdf",
}
BINARY_EXTS = {
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a",
    ".sqlite", ".db", ".pdf", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".mov", ".class", ".pyc",
}


class Allowlist:
    """Path-glob allowlist (the ``.atcallow`` file) that overrides exclusions.

    A path that matches a denylist entry but also matches an allowlist glob is
    permitted. Matching is case-sensitive (``fnmatch.fnmatchcase``) and is
    tried against both the full repo-relative path and the basename, so a bare
    ``test.key`` allows ``test.key`` anywhere in the tree. An empty allowlist
    (no ``.atcallow`` present) overrides nothing, preserving the default
    denylist behavior.
    """

    def __init__(self, patterns: list[str] | None = None) -> None:
        self._patterns: list[str] = list(patterns) if patterns else []

    @property
    def patterns(self) -> list[str]:
        """Read-only copy of the glob patterns."""
        return list(self._patterns)

    def __bool__(self) -> bool:
        return bool(self._patterns)

    def matches(self, path: str) -> bool:
        norm = path.replace("\\", "/")
        name = norm.rsplit("/", 1)[-1]
        for pat in self._patterns:
            if fnmatch.fnmatchcase(norm, pat) or fnmatch.fnmatchcase(name, pat):
                return True
        return False


def load_allowlist(repo_root: Path) -> Allowlist:
    """Load ``.atcallow`` from the repo root.

    Returns an empty :class:`Allowlist` when the file is absent or unreadable.
    Blank lines and ``#`` comments are ignored; every other line is a path
    glob. Exposed so scanner/committer can build the allowlist once and pass it
    to :func:`path_excluded` / :func:`evaluate_path` read-only.
    """
    path = repo_root / ".atcallow"
    if not path.is_file():
        return Allowlist()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return Allowlist()
    patterns: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return Allowlist(patterns)


def path_excluded(
    path: str,
    *,
    allowlist: Allowlist | None = None,
) -> tuple[bool, str | None]:
    """Return (excluded, reason).

    When ``allowlist`` is provided, a path that matches both a denylist entry
    and an allowlist glob is permitted (returns ``(False, None)``).
    """
    parts = path.replace("\\", "/").split("/")
    for comp in parts:
        if comp in EXCLUDED_DIR_COMPONENTS:
            if allowlist is not None and allowlist.matches(path):
                return False, None
            return True, f"path component '{comp}' is denylisted"
    filename = parts[-1] if parts else path
    if SAMPLE_ENV_RE.search(filename):
        return False, None
    if EXCLUDED_FILENAME_RE.match(filename):
        if allowlist is not None and allowlist.matches(path):
            return False, None
        return True, f"filename '{filename}' is denylisted"
    return False, None


def ext_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def looks_binary(sample: bytes) -> bool:
    if b"\x00" in sample[:8192]:
        return True
    return False


def evaluate_path(
    path: str,
    *,
    is_binary: bool,
    allow_binary: bool,
    allowlist: Allowlist | None = None,
) -> SafetyResult:
    """Evaluate a single file path for safety."""
    result = SafetyResult()
    excluded, reason = path_excluded(path, allowlist=allowlist)
    if excluded:
        result.safe = False
        result.excluded_path = True
        result.reasons.append(reason or "denylisted path")
        return result

    ext = ext_of(path)
    if is_binary:
        result.binary = True
        if not allow_binary:
            result.safe = False
            result.reasons.append("binary file refused (use --allow-binary)")
            return result
        if ext not in SAFE_BINARY_EXTS:
            result.safe = False
            result.reasons.append(f"binary extension '{ext}' not in safe asset allowlist")
            return result

    return result
