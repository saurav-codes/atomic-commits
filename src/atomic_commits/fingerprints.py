"""Stable fingerprints for hunks and the whole worktree (section 19).

Fingerprints must rematch after earlier hunks are committed, so they must not
rely on absolute line numbers. We hash path + normalized added/removed content
+ a nearby heading signal.
"""

from __future__ import annotations

import hashlib
import re

HEADING_RE = re.compile(
    r"\b(?:def|class|func|function|interface|struct|impl|module|fn)\b\s+([A-Za-z0-9_]+)"
)

# Matches the @@ -a,b +c,d @@ part of a hunk header so we can drop the volatile
# line numbers and keep only the trailing section heading text.
_HUNK_RANGE_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def _normalize_header(header: str) -> str:
    """Drop @@ line-number ranges, keeping only the section heading text.

    Line numbers shift as earlier hunks are committed, so including them in a
    fingerprint would prevent rematching the same hunk later (section 19).
    """
    stripped = header.strip()
    return _HUNK_RANGE_RE.sub("", stripped).strip()


def _normalize_lines(lines: list[str]) -> str:
    return "\n".join(line.rstrip() for line in lines)


def nearby_heading(context_before: list[str], header: str) -> str:
    for line in reversed(context_before):
        m = HEADING_RE.search(line)
        if m:
            return m.group(1)
    m = HEADING_RE.search(header)
    return m.group(1) if m else ""


def hunk_fingerprint(
    *,
    file_path: str,
    added: list[str],
    removed: list[str],
    header: str,
    context_before: list[str],
) -> str:
    heading = nearby_heading(context_before, header)
    payload = "\n".join(
        [
            f"path:{file_path}",
            f"heading:{heading}",
            "added:",
            _normalize_lines(added),
            "removed:",
            _normalize_lines(removed),
            f"header:{_normalize_header(header)}",
        ]
    )
    return _sha256(payload)


def worktree_fingerprint(
    *,
    head_sha: str,
    branch: str,
    file_entries: list[tuple[str, str]],
    hunk_fingerprints: list[str],
    untracked_hashes: list[str],
) -> str:
    """file_entries: list of (path, status). All inputs are sorted for stability."""
    parts = [f"head:{head_sha}", f"branch:{branch}"]
    for path, status in sorted(file_entries):
        parts.append(f"file:{status}:{path}")
    for fp in sorted(hunk_fingerprints):
        parts.append(f"hunk:{fp}")
    for h in sorted(untracked_hashes):
        parts.append(f"untracked:{h}")
    return _sha256("\n".join(parts))


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
