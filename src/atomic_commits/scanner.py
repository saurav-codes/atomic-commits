"""Worktree scanning and stable snapshot construction.

Uses `git status --porcelain=v1 -z` as the source of truth, parses worktree
(and optionally cached) diffs, synthesizes diffs for safe untracked files, and
applies safety filtering, producing a WorktreeSnapshot with a stable
fingerprint.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import diff_parser, safety
from .config import RunConfig
from .fingerprints import hunk_fingerprint, worktree_fingerprint
from .git_client import GitClient
from .models import FileChange, Hunk, SafetyResult, StatusEntry, WorktreeSnapshot


def parse_status_z(raw: bytes) -> list[StatusEntry]:
    """Parse NUL-delimited porcelain v1 status output."""
    entries: list[StatusEntry] = []
    tokens = raw.split(b"\x00")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok:
            i += 1
            continue
        text = tok.decode("utf-8", "surrogateescape")
        xy = text[:2]
        path = text[3:]
        orig_path = None
        # Renames/copies carry the ORIGINAL path in the next token; the path
        # above is already the new (current) path.
        if xy and (xy[0] in ("R", "C") or xy[1] in ("R", "C")):
            i += 1
            if i < len(tokens):
                orig_path = tokens[i].decode("utf-8", "surrogateescape")
        entries.append(StatusEntry(xy=xy, path=path, orig_path=orig_path))
        i += 1
    return entries


def _rename_hunk(path: str, old_path: str | None) -> Hunk:
    """A file-level hunk for a pure rename (no content diff).

    Mirrors diff_parser._file_level_hunk so the planner/committer treat the
    rename as a committable change instead of silently dropping it (a rename
    with zero content hunks would otherwise be skipped by chunk_hunks).
    """
    metadata = [f"rename from {old_path or ''}", f"rename to {path}"]
    header = "@@ file-level renamed @@"
    fp = hunk_fingerprint(
        file_path=path,
        added=["renamed", *metadata],
        removed=[],
        header=header,
        context_before=[],
    )
    return Hunk(
        hunk_id=f"{path}::hunk::1",
        file_path=path,
        old_start=0,
        old_count=0,
        new_start=0,
        new_count=0,
        header=header,
        added=["renamed", *metadata],
        patch="",
        fingerprint=fp,
    )


def _read_text_or_none(repo: Path, rel_path: str) -> tuple[str | None, bool, bytes]:
    """Return (decoded_text|None, is_binary, raw_bytes) for a worktree file."""
    fpath = repo / rel_path
    try:
        data = fpath.read_bytes()
    except OSError:
        return None, False, b""
    if safety.looks_binary(data):
        return None, True, data
    try:
        return data.decode("utf-8"), False, data
    except UnicodeDecodeError:
        return None, True, data


def _within_paths(path: str, paths: list[str]) -> bool:
    if not paths:
        return True
    norm = Path(path.replace("\\", "/"))
    for p in paths:
        base = Path(p.rstrip("/"))
        if norm == base or norm.is_relative_to(base):
            return True
    return False


def scan(git: GitClient, cfg: RunConfig) -> WorktreeSnapshot:
    repo = git.toplevel()
    branch = git.current_branch()
    head = git.head_sha()
    status_entries = parse_status_z(git.status_porcelain_z())

    # Load .atcallow once; an empty allowlist overrides nothing.
    allowlist = safety.load_allowlist(repo)

    files: list[FileChange] = []
    # With staged input, compare HEAD to the final working files. Appending the
    # staged and unstaged diffs duplicates files changed in both places.
    worktree_patch = git.diff_head() if cfg.include_staged else git.diff_worktree()
    parsed = diff_parser.parse_patch(worktree_patch, is_tracked=True)

    for fc in parsed:
        if not _within_paths(fc.path, cfg.paths):
            continue
        text, is_bin, _ = _read_text_or_none(repo, fc.path)
        fc.is_binary = fc.is_binary or is_bin
        fc.safety = safety.evaluate_path(
            fc.path,
            is_binary=fc.is_binary,
            allow_binary=cfg.allow_binary,
            allowlist=allowlist,
        )
        files.append(fc)

    parsed_paths = {f.path for f in files}
    for entry in status_entries:
        if entry.path in parsed_paths or not _within_paths(entry.path, cfg.paths):
            continue
        if "R" not in entry.xy:
            continue
        rename_hunk = _rename_hunk(entry.path, entry.orig_path)
        fc = FileChange(
            path=entry.path,
            old_path=entry.orig_path,
            status="renamed",
            is_tracked=True,
            safety=safety.evaluate_path(
                entry.path,
                is_binary=False,
                allow_binary=cfg.allow_binary,
                allowlist=allowlist,
            ),
            hunks=[rename_hunk],
        )
        files.append(fc)

    # Safe untracked files: synthesize added-file diffs.
    untracked_hashes: list[str] = []
    # First pass: classify untracked files; collect safe ones that need diffs.
    pending: list[tuple[str, bool, SafetyResult, bytes]] = []
    for rel in git.untracked_files():
        if not _within_paths(rel, cfg.paths):
            continue
        excluded, _ = safety.path_excluded(rel, allowlist=allowlist)
        if excluded:
            continue
        _, is_bin, data = _read_text_or_none(repo, rel)
        sresult = safety.evaluate_path(
            rel,
            is_binary=is_bin,
            allow_binary=cfg.allow_binary,
            allowlist=allowlist,
        )
        if not sresult.safe:
            files.append(
                FileChange(path=rel, status="added", is_tracked=False, is_binary=is_bin, safety=sresult)
            )
            continue
        pending.append((rel, is_bin, sresult, data))

    # Batch-synthesize diffs for all safe untracked files in a single subprocess.
    batch_patches = git.diff_no_index_batch([p[0] for p in pending])
    for rel, is_bin, sresult, data in pending:
        synth = batch_patches.get(rel, "")
        synth_files = diff_parser.parse_patch(synth, is_tracked=False)
        for sf in synth_files:
            sf.path = rel
            sf.status = "added"
            sf.is_tracked = False
            sf.is_binary = is_bin
            sf.safety = sresult
            # Hunks were parsed from a temp-dir diff whose paths include the
            # temp prefix; fix file_path/hunk_id and recompute fingerprints so
            # the stager can rematch hunks later.
            for h in sf.hunks:
                ordinal = h.hunk_id.rsplit("::hunk::", 1)[-1]
                h.file_path = rel
                h.hunk_id = f"{rel}::hunk::{ordinal}"
                h.fingerprint = hunk_fingerprint(
                    file_path=rel,
                    added=h.added,
                    removed=h.removed,
                    header=h.header,
                    context_before=h.context_before,
                )
            files.append(sf)
        untracked_hashes.append(hashlib.sha256(data).hexdigest())

    all_hunk_fps = [h.fingerprint for fc in files for h in fc.hunks]
    file_entries: list[tuple[str, str]] = [(fc.path, fc.status) for fc in files]
    fp = worktree_fingerprint(
        head_sha=head,
        branch=branch,
        file_entries=file_entries,
        hunk_fingerprints=all_hunk_fps,
        untracked_hashes=untracked_hashes,
    )

    return WorktreeSnapshot(
        repo_root=repo,
        branch=branch,
        head_sha=head,
        status_entries=status_entries,
        files=files,
        fingerprint=fp,
    )

