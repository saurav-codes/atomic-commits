import asyncio

from atomic_commits import planner
from atomic_commits.committer import Committer
from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan

from .helpers import commit_count, git, make_cfg


def test_repo_subdir_resolves_paths(git_repo, mock_provider):
    # Create a file in a subdirectory and point --repo at that subdirectory.
    sub = git_repo / "pkg"
    sub.mkdir()
    (sub / "m.py").write_text("x = 1\n")
    git(git_repo, "add", "pkg/m.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (sub / "m.py").write_text("x = 2\n")

    cfg = make_cfg(sub, mode="verbose")
    gc = GitClient(sub)
    before = commit_count(git_repo)
    snapshot = scan(gc, cfg)
    # Scanner must see the change relative to the repo root.
    assert any(f.path == "pkg/m.py" for f in snapshot.files)

    plan = asyncio.run(planner.plan(mock_provider, gc, snapshot, cfg))
    results = Committer(gc, cfg).apply(plan)
    assert all(r.status == "committed" for r in results)
    assert commit_count(git_repo) == before + len(plan.groups)
