import asyncio

from atomic_commits.committer import Committer
from atomic_commits.git_client import GitClient
from atomic_commits import planner
from atomic_commits.scanner import scan

from .helpers import commit_count, git, make_cfg


def test_apply_verbose_commits_each_hunk(git_repo, mock_provider):
    (git_repo / "a.py").write_text("x = 1\n")
    (git_repo / "b.py").write_text("y = 1\n")
    git(git_repo, "add", "a.py", "b.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("x = 2\n")
    (git_repo / "b.py").write_text("y = 2\n")

    cfg = make_cfg(git_repo, mode="verbose")
    gc = GitClient(git_repo)
    before = commit_count(git_repo)

    snapshot = scan(gc, cfg)
    plan = asyncio.run(planner.plan(mock_provider, gc, snapshot, cfg))
    results = Committer(gc, cfg).apply(plan)

    assert all(r.status == "committed" for r in results)
    assert commit_count(git_repo) == before + len(plan.groups)
