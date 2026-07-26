
import pytest

from atomic_commits import planner
from atomic_commits.committer import Committer
from atomic_commits.errors import FingerprintMismatchError, PreflightError
from atomic_commits.flows import _apply_with_progress, apply_saved, resume
from atomic_commits.git_client import GitClient
from atomic_commits.models import AppliedCommit, CommitGroup, CommitPlan
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
    plan = planner.plan(mock_provider, gc, snapshot, cfg)
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
    plan = planner.plan(mock_provider, gc, snapshot, cfg)
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


def test_apply_saved_persists_plan_and_snapshot(git_repo, mock_provider):
    """apply_saved must write plan.json/snapshot.json so a later resume works.

    Regression: apply_saved created a session and applied but never persisted
    the plan/snapshot, so a partially-failed session could not be resumed
    (FileNotFoundError on plan.json).
    """
    (git_repo / "a.py").write_text("x = 1\n")
    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("x = 2\n")

    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    # Build and persist a plan via dry_run first so apply_saved has a latest plan.
    from atomic_commits import planner
    from atomic_commits.scanner import scan
    snapshot = scan(gc, cfg)
    plan = planner.plan(mock_provider, gc, snapshot, cfg)
    store = SessionStore(gc)
    sid = store.create()
    store.write_plan(sid, plan)

    # apply_saved loads the latest plan and applies it; it must persist the
    # plan/snapshot into ITS OWN session before applying.
    apply_saved(gc, cfg, None)

    # Find the newest session and assert it has plan.json and snapshot.json.
    # Exclude the setup session (`sid`) we created above; the planner's chunk-
    # review cache dir can also surface in list_sessions(), so filter to dirs
    # that actually hold a plan.json.
    sessions = [
        s for s in store.list_sessions()
        if s != sid and (store.session_path(s) / "plan.json").is_file()
    ]
    assert sessions, "apply_saved should have created a session"
    newest = sessions[0]  # list_sessions returns sorted desc
    session_dir = store.session_path(newest)
    assert (session_dir / "plan.json").is_file()
    assert (session_dir / "snapshot.json").is_file()
    # And the worktree should be clean after a successful apply.
    assert git(git_repo, "status", "--porcelain").stdout == ""


def test_resume_repairs_and_applies_interrupted_multi_hunk_rename(git_repo):
    lines = [f"line {index}" for index in range(40)]
    (git_repo / "old.py").write_text("\n".join(lines) + "\n")
    git(git_repo, "add", "old.py")
    git(git_repo, "commit", "-q", "-m", "seed old.py")
    git(git_repo, "mv", "old.py", "new.py")
    lines[2] = "changed near start"
    lines[30] = "changed near end"
    (git_repo / "new.py").write_text("\n".join(lines) + "\n")

    planning_cfg = make_cfg(git_repo, mode="compact", include_staged=True, no_verify=True)
    gc = GitClient(git_repo)
    snapshot = scan(gc, planning_cfg)
    renamed = next(file_change for file_change in snapshot.files if file_change.status == "renamed")
    assert len(renamed.hunks) == 2
    broken = CommitPlan(
        repo_fingerprint=snapshot.fingerprint,
        base_head=snapshot.head_sha,
        mode="compact",
        groups=[
            CommitGroup(
                group_id=f"g{index}",
                message=f"refactor(core): update renamed file part {index}",
                hunk_ids=[hunk.hunk_id],
            )
            for index, hunk in enumerate(renamed.hunks, start=1)
        ],
    )
    store = SessionStore(gc)
    session_id = store.create()
    store.write_plan(session_id, broken)
    store.write_snapshot(session_id, snapshot)
    # Simulate a staging failure that reset a previously staged rename into
    # an unstaged deletion plus untracked addition. Advancing HEAD reproduces
    # the same classification change after an earlier planned commit landed.
    git(git_repo, "reset", "-q", "HEAD", "--", "old.py", "new.py")
    (git_repo / "marker.py").write_text("done = True\n")
    git(git_repo, "add", "marker.py")
    git(git_repo, "commit", "-q", "-m", "earlier planned commit")

    resume(gc, make_cfg(git_repo, mode="compact", no_verify=True))

    assert git(git_repo, "status", "--porcelain").stdout == ""
    applied = store.load_apply_log(session_id)
    assert len(applied) == 1
    assert applied[0].status == "committed"


def test_apply_progress_survives_an_unexpected_later_error(git_repo, monkeypatch):
    cfg = make_cfg(git_repo, mode="compact")
    gc = GitClient(git_repo)
    store = SessionStore(gc)
    session_id = store.create()
    plan = CommitPlan(
        repo_fingerprint="fp",
        base_head=gc.head_sha(),
        mode="compact",
        groups=[CommitGroup(
            group_id="g1",
            message="refactor(core): record completed commit",
            hunk_ids=["a.py::hunk::1"],
        )],
    )

    def fail_after_commit(self, commit_plan, on_event, **kwargs):
        on_event(1, 1, commit_plan.groups[0], "abc123")
        raise PreflightError("later staging failed")

    monkeypatch.setattr(Committer, "apply", fail_after_commit)
    with pytest.raises(PreflightError, match="later staging failed"):
        _apply_with_progress(
            gc, cfg, store, session_id, plan, show=False,
        )

    applied = store.load_apply_log(session_id)
    assert [(item.group_id, item.sha, item.status) for item in applied] == [
        ("g1", "abc123", "committed")
    ]
