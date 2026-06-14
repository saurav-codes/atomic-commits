import subprocess

from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan

from .helpers import git, make_cfg


def test_rename_detected(git_repo):
    (git_repo / "old.py").write_text("def f():\n    return 1\n\n\nx = 1\n")
    git(git_repo, "add", "old.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    git(git_repo, "mv", "old.py", "renamed.py")

    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    statuses = {(f.path, f.status) for f in snapshot.files}
    # Either a rename is detected, or it appears as add/delete pair; both safe.
    assert any(s == "renamed" for _, s in statuses) or (
        any(p == "renamed.py" for p, _ in statuses)
    )


def test_mode_change_scanned(git_repo):
    f = git_repo / "script.sh"
    f.write_text("#!/bin/sh\necho hi\n")
    git(git_repo, "add", "script.sh")
    git(git_repo, "commit", "-q", "-m", "seed")
    f.chmod(0o755)
    git(git_repo, "update-index", "--chmod=+x", "script.sh")

    cfg = make_cfg(git_repo, include_staged=True)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    # Mode change should be present and safe.
    assert any(f.path == "script.sh" for f in snapshot.files)
