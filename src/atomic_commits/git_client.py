"""Thin wrapper around the `git` CLI via subprocess.

Git patch/staging semantics are exact and battle-tested, so atc shells out to
`git` rather than depending on a heavy library.

All commands run with the repo root as cwd. Binary-safe output is returned as
bytes; text helpers decode as UTF-8 with surrogateescape so arbitrary bytes
round-trip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import diff_parser, safety
from .errors import CommitError, PatchApplyError, PreflightError

# Directory name used inside the temp dir by diff_no_index_batch. Paths in the
# batch diff output are prefixed with this; it is stripped to recover the
# repo-relative path.
_BATCH_CONTENT_DIR = "_atc_content"


def _warn_denylisted_in_backup(patch: bytes) -> None:
    """Warn on stderr if the backup patch contains denylisted paths.

    The full backup is always preserved for reversibility — this is a
    best-effort warning so the user can review before ``git apply``.
    """
    try:
        text = patch.decode("utf-8", "surrogateescape")
        denylisted: set[str] = set()
        for fc in diff_parser.parse_patch(text, is_tracked=True):
            for p in (fc.path, fc.old_path):
                if p is None:
                    continue
                excluded, _ = safety.path_excluded(p)
                if excluded:
                    denylisted.add(p)
        if denylisted:
            print(
                "warning: backup patch contains denylisted paths "
                "(full backup preserved for reversibility; review before "
                "`git apply`): " + ", ".join(sorted(denylisted)),
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001 - best-effort warning, never fail the backup
        pass


class GitClient:
    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)
        self._toplevel: Path | None = None

    def _cwd(self) -> str:
        """Directory to run git from.

        Once we know the repository toplevel, run everything from there so that
        diff paths (which git reports relative to the repo root) line up with
        the paths passed to `git add` / `git apply --cached`. This makes
        `atc --repo <subdir>` behave correctly.
        """
        if self._toplevel is not None:
            return str(self._toplevel)
        return str(self.repo)

    # -- low level ---------------------------------------------------------
    def _run(
        self,
        args: list[str],
        *,
        input_bytes: bytes | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        if shutil.which("git") is None:
            raise PreflightError(
                "git executable not found on PATH",
                hint="Install Git and ensure `git` is available.",
            )
        run_env = None
        if env:
            run_env = dict(os.environ)
            run_env.update(env)
        proc = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=self._cwd(),
            input=input_bytes,
            capture_output=True,
            env=run_env,
        )
        if check and proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            raise PreflightError(f"git {' '.join(args)} failed: {stderr}")
        return proc

    def run_text(self, args: list[str], *, check: bool = True) -> str:
        proc = self._run(args, check=check)
        return proc.stdout.decode("utf-8", "surrogateescape")

    def run_bytes(self, args: list[str], *, check: bool = True) -> bytes:
        return self._run(args, check=check).stdout

    # -- preflight / info --------------------------------------------------
    def is_git_repo(self) -> bool:
        proc = self._run(["rev-parse", "--show-toplevel"], check=False)
        return proc.returncode == 0

    def toplevel(self) -> Path:
        out = self.run_text(["rev-parse", "--show-toplevel"]).strip()
        top = Path(out)
        # Cache so all subsequent commands run from the repo root.
        self._toplevel = top
        return top

    def current_branch(self) -> str:
        return self.run_text(["rev-parse", "--abbrev-ref", "HEAD"]).strip()

    def head_sha(self, *, allow_missing: bool = True) -> str:
        proc = self._run(["rev-parse", "HEAD"], check=False)
        if proc.returncode != 0:
            if allow_missing:
                return ""
            raise PreflightError("repository has no commits yet")
        return proc.stdout.decode().strip()

    def has_commits(self) -> bool:
        return self._run(["rev-parse", "--verify", "HEAD"], check=False).returncode == 0

    def git_dir(self) -> Path:
        out = self.run_text(["rev-parse", "--absolute-git-dir"]).strip()
        return Path(out)

    def in_progress_state_files(self) -> list[str]:
        """Return names of in-progress operation markers that should block atc."""
        gd = self.git_dir()
        markers = [
            "MERGE_HEAD",
            "rebase-merge",
            "rebase-apply",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
        ]
        return [m for m in markers if (gd / m).exists()]

    def recent_subjects(self, count: int = 10) -> list[str]:
        if not self.has_commits():
            return []
        out = self.run_text(["log", f"-{count}", "--pretty=%s"], check=False)
        return [line for line in out.splitlines() if line.strip()]

    # -- status / scan -----------------------------------------------------
    def status_porcelain_z(self) -> bytes:
        return self.run_bytes(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
        )

    def has_staged_changes(self) -> bool:
        proc = self._run(["diff", "--cached", "--quiet"], check=False)
        return proc.returncode != 0

    def diffstat(self) -> str:
        return self.run_text(["diff", "--stat"], check=False)

    def diff_worktree(self) -> str:
        return self.run_text(
            ["diff", "--find-renames", "--patch", "--unified=3", "--binary"],
            check=False,
        )

    def diff_head(self) -> str:
        """Return the final staged plus unstaged change relative to HEAD."""
        return self.run_text(
            ["diff", "HEAD", "--find-renames", "--patch", "--unified=3", "--binary"],
            check=False,
        )

    def diff_cached(self) -> str:
        return self.run_text(
            ["diff", "--cached", "--find-renames", "--patch", "--unified=3", "--binary"],
            check=False,
        )

    def cached_changed_paths(self) -> list[str]:
        out = self.run_bytes(["diff", "--cached", "--name-only", "-z"], check=False)
        return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\x00") if p]

    def untracked_files(self) -> list[str]:
        out = self.run_bytes(["ls-files", "--others", "--exclude-standard", "-z"])
        return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\x00") if p]

    def diff_no_index(self, path: str) -> str:
        """Synthesize an added-file diff for an untracked file."""
        # git diff --no-index returns exit code 1 when files differ; that is normal.
        proc = self._run(
            ["diff", "--no-index", "--unified=3", "--binary", "/dev/null", path],
            check=False,
        )
        return proc.stdout.decode("utf-8", "surrogateescape")

    def diff_no_index_batch(self, paths: list[str]) -> dict[str, str]:
        """Synthesize added-file diffs for multiple untracked files in one subprocess.

        Creates a temp directory mirroring the untracked files and runs a single
        ``git diff --no-index`` between an empty dir and the content dir. Returns
        a dict mapping each repo-relative path to its individual patch section.
        Files that produce no diff output (e.g. empty files) are absent from the
        dict; callers should treat a missing key as an empty diff.
        """
        if not paths:
            return {}
        root = self._toplevel or self.repo
        result: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            empty = Path(tmpdir) / "_empty"
            content = Path(tmpdir) / _BATCH_CONTENT_DIR
            empty.mkdir()
            content.mkdir()
            for rel in paths:
                src = root / rel
                dst = content / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(src, dst)
                except OSError:
                    try:
                        shutil.copy2(src, dst)
                    except OSError:
                        # File vanished between listing and diffing; skip it.
                        continue
            proc = self._run(
                [
                    "diff", "--no-index", "--unified=3", "--binary",
                    "--src-prefix=a/", "--dst-prefix=b/",
                    str(empty), str(content),
                ],
                check=False,
            )
            output = proc.stdout.decode("utf-8", "surrogateescape")
        # Split into per-file sections and map back to repo-relative paths.
        # The diff output paths include the temp dir structure (e.g.
        # "var/folders/.../_atc_content/new_module.py" after a/ stripping);
        # strip everything up to and including the content dir name.
        marker = _BATCH_CONTENT_DIR + "/"
        for section in diff_parser._split_file_sections(output):
            fcs = diff_parser.parse_patch(section, is_tracked=False)
            if not fcs:
                continue
            rel = fcs[0].path
            idx = rel.find(marker)
            if idx >= 0:
                rel = rel[idx + len(marker) :]
            result[rel] = section
        return result

    # -- staging / apply ---------------------------------------------------
    def add_intent(self, path: str) -> None:
        self._run(["add", "-N", "--", path])

    def add_path(self, path: str) -> None:
        self._run(["add", "--", path])

    def add_all_paths(self, paths: list[str]) -> None:
        self._run(["add", "-A", "--", *paths])

    def rm_cached(self, path: str) -> None:
        self._run(["rm", "--cached", "--", path])

    def apply_cached_check(self, patch: bytes) -> None:
        proc = self._run(
            ["apply", "--cached", "--recount", "--check", "-"],
            input_bytes=patch,
            check=False,
        )
        if proc.returncode != 0:
            raise PatchApplyError(
                "patch does not apply cleanly to the index",
                hint=proc.stderr.decode("utf-8", "replace").strip() or None,
            )

    def apply_cached(self, patch: bytes) -> None:
        proc = self._run(
            ["apply", "--cached", "--recount", "-"],
            input_bytes=patch,
            check=False,
        )
        if proc.returncode != 0:
            raise PatchApplyError(
                "failed to stage patch",
                hint=proc.stderr.decode("utf-8", "replace").strip() or None,
            )

    def restore_staged(self, paths: list[str]) -> None:
        if not paths:
            return
        self._run(["restore", "--staged", "--", *paths], check=False)

    def commit(self, message: str, *, no_verify: bool = False, committer_date: str | None = None) -> str:
        args = ["commit", "-m", message]
        if no_verify:
            args.append("--no-verify")
        env = {"GIT_COMMITTER_DATE": committer_date} if committer_date else None
        proc = self._run(args, check=False, env=env)
        if proc.returncode != 0:
            raise CommitError(
                "git commit failed",
                hint=proc.stderr.decode("utf-8", "replace").strip() or None,
            )
        return self.head_sha()

    def backup_patch(self) -> bytes:
        patch = self.run_bytes(["diff", "--binary"], check=False)
        _warn_denylisted_in_backup(patch)
        return patch
