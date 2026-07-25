
from atomic_commits import planner
from atomic_commits.committer import Committer
from atomic_commits.git_client import GitClient
from atomic_commits.models import CommitGroup, CommitPlan
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
    plan = planner.plan(mock_provider, gc, snapshot, cfg)
    results = Committer(gc, cfg).apply(plan)

    assert all(r.status == "committed" for r in results)
    assert commit_count(git_repo) == before + len(plan.groups)


def test_apply_nearby_split_regions_as_separate_commits(git_repo):
    original = """first = 1
keep_one = 1
keep_two = 2
second = 2
tail = 3
"""
    changed = original.replace("first = 1", "first = 10").replace(
        "second = 2", "second = 20"
    )
    (git_repo / "app.py").write_text(original)
    git(git_repo, "add", "app.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "app.py").write_text(changed)

    cfg = make_cfg(git_repo, mode="verbose", no_verify=True)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    hunks = snapshot.files[0].hunks
    assert len(hunks) == 2
    plan = CommitPlan(
        mode="verbose",
        repo_fingerprint=snapshot.fingerprint,
        base_head=snapshot.head_sha,
        groups=[
            CommitGroup(
                group_id=f"g{index}",
                message=f"refactor(app): revise value {index}",
                hunk_ids=[hunk.hunk_id],
            )
            for index, hunk in enumerate(hunks, start=1)
        ],
    )

    results = Committer(gc, cfg).apply(plan, planned_snapshot=snapshot)

    assert [result.status for result in results] == ["committed", "committed"]
    assert (git_repo / "app.py").read_text() == changed
