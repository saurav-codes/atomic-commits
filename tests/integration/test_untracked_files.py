import asyncio

from atomic_commits.committer import Committer
from atomic_commits.git_client import GitClient
from atomic_commits import planner
from atomic_commits.scanner import scan

from .helpers import git, make_cfg


def test_safe_untracked_file_is_committed(git_repo, mock_provider):
    (git_repo / "new_module.py").write_text("def g():\n    return 42\n")

    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    assert any(f.path == "new_module.py" and f.safety.safe for f in snapshot.files)

    plan = asyncio.run(planner.plan(mock_provider, gc, snapshot, cfg))
    results = Committer(gc, cfg).apply(plan)
    assert all(r.status == "committed" for r in results)

    tracked = git(git_repo, "ls-files").stdout
    assert "new_module.py" in tracked
