import asyncio

import pytest

from atomic_commits import planner
from atomic_commits.committer import Committer
from atomic_commits.errors import FingerprintMismatchError
from atomic_commits.flows import apply_saved, resume
from atomic_commits.git_client import GitClient
from atomic_commits.models import AppliedCommit
from atomic_commits.scanner import scan
from atomic_commits.session import SessionStore

from .helpers import commit_count, git, make_cfg


def test_fingerprint_mismatch_refuses_stale_plan(git_repo, mock_provider):
    (git_repo / "a.py").write_text("x = 1\n")
    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("x = 2\n")

    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    plan = asyncio.run(planner.plan(mock_provider, gc, snapshot, cfg))
    store = SessionStore(gc)
    sid = store.create()
    store.write_plan(sid, plan)

    # Change the worktree so the fingerprint no longer matches.
    (git_repo / "a.py").write_text("x = 3\n")

    with pytest.raises(FingerprintMismatchError):
        apply_saved(gc, cfg, None)


def test_resume_completes_remaining_groups(git_repo, mock_provider):
    # Two independent files -> two groups in verbose mode.
    (git_repo / "a.py").write_text("x = 1\n")
    (git_repo / "b.py").write_text("y = 1\n")
    git(git_repo, "add", "a.py", "b.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("x = 2\n")
    (git_repo / "b.py").write_text("y = 2\n")

    cfg = make_cfg(git_repo, mode="verbose")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    plan = asyncio.run(planner.plan(mock_provider, gc, snapshot, cfg))
    assert len(plan.groups) == 2

    store = SessionStore(gc)
    sid = store.create()
    store.write_plan(sid, plan)

    # Simulate that only the first group was applied: commit it, then record
    # a partial apply log with a pending second group.
    before = commit_count(git_repo)
    first = Committer(gc, cfg).apply(
        type(plan)(
            version=plan.version,
            mode=plan.mode,
            repo_fingerprint=plan.repo_fingerprint,
            base_head=plan.base_head,
            groups=plan.groups[:1],
            excluded=plan.excluded,
            warnings=plan.warnings,
        )
    )
    assert first[0].status == "committed"
    store.write_apply_log(
        sid,
        first + [AppliedCommit(group_id=plan.groups[1].group_id, message=plan.groups[1].message, status="pending")],
    )

    # Resume should apply the remaining group.
    resume(gc, cfg)
    assert commit_count(git_repo) == before + 2
    status = git(git_repo, "status", "--porcelain").stdout
    assert status.strip() == ""
