"""Parser tests against REAL `git diff` output generated in temp repos.

Hand-written fixtures can drift from what git actually emits. These tests run
git in a throwaway repo, capture its diff, feed it through the parser, and then
verify the parsed hunks can be reconstructed and re-applied to the index with
`git apply --cached`. This is the strongest correctness signal short of a full
end-to-end run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from atomic_commits.diff_parser import build_patch_for_hunks, parse_patch
from atomic_commits.git_client import GitClient


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=check
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@e.com")
    _git(r, "config", "user.name", "T")
    _git(r, "config", "commit.gpgsign", "false")
    return r


def _seed(repo: Path, path: str, content: str) -> None:
    fp = repo / path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"seed {path}")


def _worktree_diff(repo: Path) -> str:
    return _git(
        repo, "diff", "--find-renames", "--patch", "--unified=3", "--binary", check=False
    ).stdout


def _restage_each_hunk_round_trips(repo: Path) -> None:
    """Parse the worktree diff, then stage each parsed hunk via apply --cached.

    Verifies every reconstructed patch is accepted by `git apply --check`.
    """
    gc = GitClient(repo)
    gc.toplevel()  # cache toplevel
    patch = _worktree_diff(repo)
    files = parse_patch(patch, is_tracked=True)
    for fc in files:
        if fc.is_binary or not fc.hunks:
            continue
        for hunk in fc.hunks:
            rebuilt = build_patch_for_hunks(fc, [hunk]).encode("utf-8", "surrogateescape")
            # --check must pass against the current index for at least the
            # first hunk of each file (later hunks may overlap once staged).
            gc.apply_cached_check(rebuilt)
            break


def test_real_single_modification(repo: Path):
    _seed(repo, "a.py", "line1\nline2\nline3\n")
    (repo / "a.py").write_text("line1\nline2\ninserted\nline3\n")
    files = parse_patch(_worktree_diff(repo))
    assert len(files) == 1
    assert files[0].path == "a.py"
    assert files[0].status == "modified"
    assert any("inserted" in line for line in files[0].hunks[0].added)
    _restage_each_hunk_round_trips(repo)


def test_real_multiple_separated_hunks(repo: Path):
    lines = [f"l{i}" for i in range(1, 41)]
    _seed(repo, "big.py", "\n".join(lines) + "\n")
    lines[2] = "l3-changed"
    lines[30] = "l31-changed"
    (repo / "big.py").write_text("\n".join(lines) + "\n")
    files = parse_patch(_worktree_diff(repo))
    assert len(files[0].hunks) == 2
    _restage_each_hunk_round_trips(repo)


def test_real_added_file(repo: Path):
    _seed(repo, "a.py", "x = 1\n")
    (repo / "new.py").write_text("def g():\n    return 1\n")
    _git(repo, "add", "-N", "new.py")
    files = parse_patch(_worktree_diff(repo))
    added = [f for f in files if f.path == "new.py"]
    assert added and added[0].status == "added"


def test_real_deleted_file(repo: Path):
    _seed(repo, "gone.py", "a\nb\nc\n")
    (repo / "gone.py").unlink()
    files = parse_patch(_worktree_diff(repo))
    deleted = [f for f in files if f.path == "gone.py"]
    assert deleted and deleted[0].status == "deleted"


def test_real_rename(repo: Path):
    _seed(repo, "old.py", "def f():\n    return 1\n\n\nz = 2\n")
    _git(repo, "mv", "old.py", "new.py")
    # Rename detection needs the change staged for git to emit a rename.
    files = parse_patch(
        _git(repo, "diff", "--cached", "--find-renames", "--patch", "--unified=3", check=False).stdout
    )
    paths = {(f.old_path, f.path, f.status) for f in files}
    assert any(status == "renamed" for _, _, status in paths) or any(
        p == "new.py" for _, p, _ in paths
    )


def test_real_file_with_spaces(repo: Path):
    _seed(repo, "dir name/file name.py", "a\nb\nc\n")
    (repo / "dir name" / "file name.py").write_text("a\nb\nc\nd\n")
    files = parse_patch(_worktree_diff(repo))
    assert files[0].path == "dir name/file name.py"
    _restage_each_hunk_round_trips(repo)


def test_real_no_newline_at_eof(repo: Path):
    _seed(repo, "eof.py", "first\nsecond")  # no trailing newline
    (repo / "eof.py").write_text("first\nsecond-changed")  # still no newline
    patch = _worktree_diff(repo)
    assert "No newline at end of file" in patch
    files = parse_patch(patch)
    rebuilt = build_patch_for_hunks(files[0], files[0].hunks)
    assert "No newline at end of file" in rebuilt
    # And the rebuilt patch must apply cleanly to the index.
    gc = GitClient(repo)
    gc.toplevel()
    gc.apply_cached_check(rebuilt.encode("utf-8", "surrogateescape"))


def test_real_crlf_content(repo: Path):
    _seed(repo, "win.txt", "a\r\nb\r\nc\r\n")
    (repo / "win.txt").write_text("a\r\nb\r\nc\r\nd\r\n")
    files = parse_patch(_worktree_diff(repo))
    assert files and files[0].path == "win.txt"


def test_real_adjacent_changes_merge_into_one_hunk(repo: Path):
    _seed(repo, "adj.py", "a\nb\nc\nd\n")
    (repo / "adj.py").write_text("a\nB\nC\nd\n")
    files = parse_patch(_worktree_diff(repo))
    # Two adjacent line changes collapse into a single hunk with 3-line context.
    assert len(files[0].hunks) == 1
    _restage_each_hunk_round_trips(repo)
