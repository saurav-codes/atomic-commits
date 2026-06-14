"""Safety filtering for atc (implementation.md section 9).

Safety must run before AI sees any content and before staging. This module
classifies paths and content as safe/unsafe.
"""

from __future__ import annotations

import re

from .models import SafetyResult

# 9.1 Always-exclude path components.
EXCLUDED_DIR_COMPONENTS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", "dist", "build", "target",
    "coverage", ".coverage", ".next", ".nuxt", ".turbo", ".cache",
    "logs", "tmp", "temp", ".DS_Store",
}

# Exact / glob-like filename denylist.
EXCLUDED_FILENAME_PATTERNS = [
    re.compile(r"^\.env$"),
    re.compile(r"^\.env\..+"),
    re.compile(r".*\.pem$"),
    re.compile(r".*\.key$"),
    re.compile(r".*\.p12$"),
    re.compile(r".*\.pfx$"),
    re.compile(r"^id_rsa$"),
    re.compile(r"^id_ed25519$"),
    re.compile(r"^known_hosts$"),
    re.compile(r".*\.log$"),
    re.compile(r".*\.sqlite$"),
    re.compile(r".*\.db$"),
]

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

def path_excluded(path: str) -> tuple[bool, str | None]:
    """Return (excluded, reason)."""
    parts = path.replace("\\", "/").split("/")
    for comp in parts:
        if comp in EXCLUDED_DIR_COMPONENTS:
            return True, f"path component '{comp}' is denylisted"
    filename = parts[-1] if parts else path
    if SAMPLE_ENV_RE.search(filename):
        return False, None
    for pat in EXCLUDED_FILENAME_PATTERNS:
        if pat.match(filename):
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
    content: str | None,
    allow_binary: bool,
) -> SafetyResult:
    """Evaluate a single file path + optional decoded content."""
    result = SafetyResult()
    excluded, reason = path_excluded(path)
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

    _ = content
    return result
