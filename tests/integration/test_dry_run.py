import asyncio

from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan
from atomic_commits import planner
from atomic_commits.session import SessionStore

from .helpers import commit_count, git, make_cfg


def test_dry_run_makes_no_commits(git_repo, mock_provider):
    (git_repo / "app.py").write_text("def f():\n    return 1\n")
    git(git_repo, "add", "app.py")
    git(git_repo, "commit", "-q", "-m", "add app")
    # Now make a change.
    (git_repo / "app.py").write_text("def f():\n    return 2\n")

    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    before = commit_count(git_repo)

    snapshot = scan(gc, cfg)
    plan = asyncio.run(planner.plan(mock_provider, gc, snapshot, cfg))
    store = SessionStore(gc)
    sid = store.create()
    store.write_plan(sid, plan)

    assert commit_count(git_repo) == before
    assert len(plan.groups) >= 1
    assert store.load_latest_plan() is not None
