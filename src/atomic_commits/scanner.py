"""Worktree scanning (implementation.md section 8).

Uses `git status --porcelain=v1 -z` as the source of truth, parses worktree
(and optionally cached) diffs, synthesizes diffs for safe untracked files, and
applies safety filtering, producing a WorktreeSnapshot with a stable
fingerprint.
"""

from __future__ import annotations

from pathlib import Path

from . import diff_parser, safety
from .config import RunConfig
from .fingerprints import content_hash, worktree_fingerprint
from .git_client import GitClient
from .models import FileChange, StatusEntry, WorktreeSnapshot


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
        # Renames/copies carry a second path in the next token.
        if xy and (xy[0] in ("R", "C") or xy[1] in ("R", "C")):
            i += 1
            if i < len(tokens):
                orig_path = path
                path = tokens[i].decode("utf-8", "surrogateescape")
        entries.append(StatusEntry(xy=xy, path=path, orig_path=orig_path))
        i += 1
    return entries


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
    norm = path.replace("\\", "/")
    for p in paths:
        p = p.rstrip("/")
        if norm == p or norm.startswith(p + "/"):
            return True
    return False


def scan(git: GitClient, cfg: RunConfig) -> WorktreeSnapshot:
    repo = git.toplevel()
    branch = git.current_branch()
    head = git.head_sha()
    status_entries = parse_status_z(git.status_porcelain_z())

    files: list[FileChange] = []
    # Tracked changes (and staged if requested).
    worktree_patch = git.diff_worktree()
    parsed = diff_parser.parse_patch(worktree_patch, is_tracked=True)
    if cfg.include_staged:
        parsed += diff_parser.parse_patch(git.diff_cached(), is_tracked=True)

    for fc in parsed:
        if not _within_paths(fc.path, cfg.paths):
            continue
        text, is_bin, _ = _read_text_or_none(repo, fc.path)
        fc.is_binary = fc.is_binary or is_bin
        fc.safety = safety.evaluate_path(
            fc.path,
            is_binary=fc.is_binary,
            content=text,
            allow_binary=cfg.allow_binary,
        )
        files.append(fc)

    parsed_paths = {f.path for f in files}
    for entry in status_entries:
        if entry.path in parsed_paths or not _within_paths(entry.path, cfg.paths):
            continue
        if "R" not in entry.xy:
            continue
        fc = FileChange(
            path=entry.path,
            old_path=entry.orig_path,
            status="renamed",
            is_tracked=True,
            safety=safety.evaluate_path(
                entry.path,
                is_binary=False,
                content=None,
                allow_binary=cfg.allow_binary,
            ),
        )
        files.append(fc)

    # Safe untracked files: synthesize added-file diffs.
    untracked_hashes: list[str] = []
    for rel in git.untracked_files():
        if not _within_paths(rel, cfg.paths):
            continue
        excluded, _ = safety.path_excluded(rel)
        if excluded:
            continue
        text, is_bin, data = _read_text_or_none(repo, rel)
        sresult = safety.evaluate_path(
            rel,
            is_binary=is_bin,
            content=text,
            allow_binary=cfg.allow_binary,
        )
        if not sresult.safe:
            files.append(
                FileChange(path=rel, status="added", is_tracked=False, is_binary=is_bin, safety=sresult)
            )
            continue
        synth = git.diff_no_index(rel)
        synth_files = diff_parser.parse_patch(synth, is_tracked=False)
        for sf in synth_files:
            sf.path = rel
            sf.status = "added"
            sf.is_tracked = False
            sf.is_binary = is_bin
            sf.safety = sresult
            files.append(sf)
        untracked_hashes.append(content_hash(data))

    all_hunk_fps = [h.fingerprint for fc in files for h in fc.hunks]
    file_entries = [(fc.path, fc.status) for fc in files]
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


def safe_files(snapshot: WorktreeSnapshot) -> list[FileChange]:
    return [f for f in snapshot.files if f.safety.safe]


def safe_hunk_ids(snapshot: WorktreeSnapshot) -> list[str]:
    return [h.hunk_id for f in safe_files(snapshot) for h in f.hunks]
