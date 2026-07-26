"""Unified-diff parser.

Parses `git diff` patch output into FileChange/Hunk models. Supports modified,
added, deleted, renamed, mode-only, and binary files, quoted paths, files with
spaces, adjacent hunks, and no-newline-at-EOF markers.

We never use zero-context diffs; the caller must produce >=3 context lines.
Each hunk retains its exact patch text so it can be re-staged later.
"""

from __future__ import annotations

import re
import shlex

from .fingerprints import hunk_fingerprint
from .models import FileChange, Hunk

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<rest>.*)$"
)

# Matches a single git C-style escape inside a quoted path: either an octal
# byte (1-3 octal digits, git emits 3) or one of the named control-char escapes.
_GIT_QUOTED_ESCAPE_RE = re.compile(r"\\([0-7]{1,3}|[\\\"abtnvfr])")

# Maps git's named C-escapes to their single-byte values.
_GIT_NAMED_ESCAPES = {
    "\\": 0x5C,
    '"': 0x22,
    "a": 0x07,
    "b": 0x08,
    "t": 0x09,
    "n": 0x0A,
    "v": 0x0B,
    "f": 0x0C,
    "r": 0x0D,
}


def _unquote_path(path: str) -> str:
    """Undo git's C-style quoting for paths containing special characters.

    Git quotes paths using C-style escapes when they contain spaces or other
    special bytes. Non-ASCII bytes are emitted as octal ``\\NNN`` sequences and
    the underlying byte stream is UTF-8 (``core.quotePath`` only controls
    *whether* non-ASCII bytes are quoted, not the encoding). We reassemble the
    escaped bytes and decode them as UTF-8 so non-ASCII paths round-trip
    correctly; unquoted paths are already UTF-8 text from git's stdout.
    """
    path = path.strip()
    if not (path.startswith('"') and path.endswith('"')):
        # Unquoted paths arrive as UTF-8 text from GitClient's stdout decode;
        # nothing to reinterpret.
        return path
    inner = path[1:-1]
    out = bytearray()
    pos = 0
    for m in _GIT_QUOTED_ESCAPE_RE.finditer(inner):
        out.extend(inner[pos : m.start()].encode("utf-8", "surrogateescape"))
        esc = m.group(1)
        if esc[0].isdigit():  # octal \NNN -> single raw byte
            out.append(int(esc, 8) & 0xFF)
        else:
            out.append(_GIT_NAMED_ESCAPES[esc])
        pos = m.end()
    out.extend(inner[pos:].encode("utf-8", "surrogateescape"))
    return out.decode("utf-8", "surrogateescape")


def _strip_prefix(path: str) -> str:
    """Strip the a/ or b/ prefix git adds to diff paths."""
    if path in ("/dev/null",):
        return path
    if len(path) >= 2 and path[1] == "/" and path[0] in "ab":
        return path[2:]
    return path


def _clean_diff_path(path: str) -> str:
    return _unquote_path(_strip_prefix(_unquote_path(path)))


def _split_file_sections(patch: str) -> list[str]:
    """Split a multi-file patch into per-file chunks starting at 'diff --git'."""
    lines = patch.splitlines(keepends=True)
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and current:
            sections.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("".join(current))
    return [s for s in sections if s.strip()]


def _parse_header_paths(section_lines: list[str]) -> tuple[str | None, str | None]:
    """Extract old/new paths from a file section's metadata lines."""
    old_path: str | None = None
    new_path: str | None = None
    for line in section_lines:
        if line.startswith("--- "):
            raw = line[4:].rstrip("\n")
            old_path = None if raw == "/dev/null" else _clean_diff_path(raw)
        elif line.startswith("+++ "):
            raw = line[4:].rstrip("\n")
            new_path = None if raw == "/dev/null" else _clean_diff_path(raw)
        elif line.startswith("rename from "):
            old_path = _unquote_path(line[len("rename from ") :].rstrip("\n"))
        elif line.startswith("rename to "):
            new_path = _unquote_path(line[len("rename to ") :].rstrip("\n"))
    return old_path, new_path


def _git_header_paths(first_line: str) -> tuple[str | None, str | None]:
    """Best-effort path extraction from the 'diff --git a/x b/y' line."""
    try:
        parts = shlex.split(first_line.strip())
    except ValueError:
        return None, None
    if len(parts) < 4 or parts[0] != "diff" or parts[1] != "--git":
        return None, None
    a = _clean_diff_path(parts[2])
    b = _clean_diff_path(parts[3])
    return a, b


def _classify(section: str, old_path: str | None, new_path: str | None) -> str:
    if "\nBinary files" in section or "GIT binary patch" in section:
        return "binary"
    if "\nnew file mode" in section:
        return "added"
    if "\ndeleted file mode" in section:
        return "deleted"
    if "\nrename from " in section:
        return "renamed"
    if ("\nold mode " in section or "\nnew mode " in section) and "@@" not in section:
        return "mode"
    if old_path is None and new_path is not None:
        return "added"
    if old_path is not None and new_path is None:
        return "deleted"
    return "modified"


def _make_hunk(
    *, file_path: str, hunk_id: str, old_start: int, new_start: int,
    header_suffix: str, body: list[str],
) -> Hunk:
    old_count = sum(1 for line in body if not line.startswith(("+", "\\")))
    new_count = sum(1 for line in body if not line.startswith(("-", "\\")))
    header_line = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{header_suffix}\n"
    context_before: list[str] = []
    removed: list[str] = []
    added: list[str] = []
    context_after: list[str] = []
    seen_change = False
    for raw in body:
        if raw.startswith("\\"):
            continue
        tag, text = raw[:1], raw[1:].rstrip("\n")
        if tag == "+":
            added.append(text)
            seen_change = True
        elif tag == "-":
            removed.append(text)
            seen_change = True
        elif seen_change:
            context_after.append(text)
        else:
            context_before.append(text)
    patch_text = header_line + "".join(body)
    return Hunk(
        hunk_id=hunk_id,
        file_path=file_path,
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        header=header_line.rstrip("\n"),
        context_before=context_before,
        removed=removed,
        added=added,
        context_after=context_after,
        patch=patch_text,
        fingerprint=hunk_fingerprint(
            file_path=file_path,
            added=added,
            removed=removed,
            header=header_line,
            context_before=context_before,
        ),
    )


def _split_body(
    body: list[str], old_start: int, new_start: int,
) -> list[tuple[int, int, list[str]]]:
    """Split one Git hunk into independently stageable change blocks."""
    changed = [i for i, line in enumerate(body) if line.startswith(("+", "-"))]
    if not changed:
        return []
    runs: list[tuple[int, int]] = []
    start = end = changed[0]
    for index in changed[1:]:
        if index == end + 1 or all(line.startswith("\\") for line in body[end + 1 : index]):
            end = index
        else:
            runs.append((start, end))
            start = end = index
    runs.append((start, end))
    if len(runs) == 1:
        return [(old_start, new_start, body)]

    parts: list[tuple[int, int, list[str]]] = []
    for run_index, (run_start, run_end) in enumerate(runs):
        left_limit = 3
        if run_index:
            previous_end = runs[run_index - 1][1]
            gap = sum(
                1 for line in body[previous_end + 1 : run_start]
                if not line.startswith("\\")
            )
            left_limit = min(3, gap // 2)
        left = run_start
        context = 0
        while left > 0 and context < left_limit:
            previous = body[left - 1]
            if previous.startswith(("+", "-")):
                break
            left -= 1
            if not previous.startswith("\\"):
                context += 1
        right_limit = 3
        if run_index + 1 < len(runs):
            next_start = runs[run_index + 1][0]
            gap = sum(
                1 for line in body[run_end + 1 : next_start]
                if not line.startswith("\\")
            )
            right_limit = min(3, gap - gap // 2)
        right = run_end + 1
        context = 0
        while right < len(body) and context < right_limit:
            current = body[right]
            if current.startswith(("+", "-")):
                break
            right += 1
            if not current.startswith("\\"):
                context += 1
        # Keep the no-newline marker attached to the preceding selected line.
        if right < len(body) and body[right].startswith("\\"):
            right += 1

        part_old = old_start
        part_new = new_start
        for line in body[:left]:
            if line.startswith("-"):
                part_old += 1
            elif line.startswith("+"):
                part_new += 1
            elif not line.startswith("\\"):
                part_old += 1
                part_new += 1
        parts.append((part_old, part_new, body[left:right]))
    return parts


def _parse_hunks(section: str, file_path: str) -> list[Hunk]:
    lines = section.splitlines(keepends=True)
    hunks: list[Hunk] = []
    i = 0
    ordinal = 0
    n = len(lines)
    while i < n:
        header_match = _HUNK_HEADER_RE.match(lines[i].rstrip("\n"))
        if not header_match:
            i += 1
            continue
        ordinal += 1
        old_start = int(header_match.group("old_start"))
        new_start = int(header_match.group("new_start"))
        header_suffix = header_match.group("rest")

        body: list[str] = []
        j = i + 1
        while j < n and not _HUNK_HEADER_RE.match(lines[j].rstrip("\n")) and not lines[j].startswith("diff --git "):
            body.append(lines[j])
            j += 1

        parts = _split_body(body, old_start, new_start)
        for atom, (part_old, part_new, part_body) in enumerate(parts, start=1):
            suffix = "" if len(parts) == 1 else f".{atom}"
            hunks.append(
                _make_hunk(
                    file_path=file_path,
                    hunk_id=f"{file_path}::hunk::{ordinal}{suffix}",
                    old_start=part_old,
                    new_start=part_new,
                    header_suffix=header_suffix,
                    body=part_body,
                )
            )
        i = j
    return hunks


def _file_level_hunk(section: str, file_path: str, status: str) -> Hunk:
    metadata = [
        line.rstrip("\n")
        for line in section.splitlines()
        if line.startswith(("old mode ", "new mode ", "rename from ", "rename to "))
    ]
    header = f"@@ file-level {status} @@"
    fp = hunk_fingerprint(
        file_path=file_path,
        added=[status, *metadata],
        removed=[],
        header=header,
        context_before=[],
    )
    return Hunk(
        hunk_id=f"{file_path}::hunk::1",
        file_path=file_path,
        old_start=0,
        old_count=0,
        new_start=0,
        new_count=0,
        header=header,
        added=[status, *metadata],
        patch="",
        fingerprint=fp,
    )


def parse_patch(patch: str, *, is_tracked: bool = True) -> list[FileChange]:
    """Parse a full git patch string into FileChange objects."""
    files: list[FileChange] = []
    for section in _split_file_sections(patch):
        section_lines = section.splitlines(keepends=True)
        old_path, new_path = _parse_header_paths(section_lines)
        if old_path is None and new_path is None:
            ga, gb = _git_header_paths(section_lines[0])
            old_path, new_path = ga, gb

        status = _classify(section, old_path, new_path)
        path = new_path or old_path or ""
        is_binary = status == "binary"
        hunks = [] if is_binary else _parse_hunks(section, path)
        if not hunks and status in {"mode", "renamed"}:
            hunks = [_file_level_hunk(section, path, status)]

        files.append(
            FileChange(
                path=path,
                old_path=old_path if status == "renamed" else (old_path if status != "added" else None),
                status=status,  # type: ignore[arg-type]
                is_tracked=is_tracked,
                is_binary=is_binary,
                hunks=hunks,
            )
        )
    return files


def build_patch_for_hunks(file_change: FileChange, hunks: list[Hunk]) -> str:
    """Reconstruct a minimal, applyable patch for a subset of one file's hunks.

    Produces standard git diff headers plus the selected hunk bodies. Uses >=3
    context lines as carried in each hunk's stored patch text.
    """
    old = file_change.old_path or file_change.path
    new = file_change.path
    if file_change.status == "added":
        header = (
            f"diff --git a/{new} b/{new}\n"
            f"--- /dev/null\n"
            f"+++ b/{new}\n"
        )
    elif file_change.status == "deleted":
        header = (
            f"diff --git a/{old} b/{old}\n"
            f"--- a/{old}\n"
            f"+++ /dev/null\n"
        )
    else:
        header = (
            f"diff --git a/{old} b/{new}\n"
            f"--- a/{old}\n"
            f"+++ b/{new}\n"
        )
    # Preserve each hunk's exact patch body, including any
    # "\ No newline at end of file" markers. Only add a trailing newline when
    # the body does not already end with the no-newline marker, so we never
    # inject a spurious blank line into a no-newline patch.
    parts: list[str] = []
    for h in hunks:
        text = h.patch
        if text.endswith("\n") or text.endswith("\\ No newline at end of file"):
            parts.append(text)
        else:
            parts.append(text + "\n")
    return header + "".join(parts)
