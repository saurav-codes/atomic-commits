import pytest

from atomic_commits.errors import PreflightError
from atomic_commits.flows import preflight
from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan

from .helpers import git, make_cfg


def test_staged_changes_refused_by_default(git_repo):
    (git_repo / "a.py").write_text("x = 1\n")
    git(git_repo, "add", "a.py")
    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    with pytest.raises(PreflightError):
        preflight(gc, cfg)


def test_include_staged_allows(git_repo):
    (git_repo / "a.py").write_text("x = 1\n")
    git(git_repo, "add", "a.py")
    cfg = make_cfg(git_repo, include_staged=True)
    gc = GitClient(git_repo)
    preflight(gc, cfg)  # should not raise


def test_include_staged_merges_index_and_worktree_view(git_repo):
    (git_repo / "a.py").write_text("x = 1\ny = 1\n")
    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("x = 2\ny = 1\n")
    git(git_repo, "add", "a.py")
    (git_repo / "a.py").write_text("x = 2\ny = 2\n")
    cfg = make_cfg(git_repo, include_staged=True)

    snapshot = scan(GitClient(git_repo), cfg)

    assert len(snapshot.files) == 1
    assert {line for hunk in snapshot.files[0].hunks for line in hunk.added} == {
        "x = 2",
        "y = 2",
    }
