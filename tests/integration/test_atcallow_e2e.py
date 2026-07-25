from __future__ import annotations

from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan

from .helpers import make_cfg


def test_atcallow_overrides_env_denylist(git_repo):
    # .env is denied by default; without .atcallow, scan() excludes it entirely
    # (path_excluded skips it before it ever reaches snapshot.files).
    (git_repo / ".env").write_text("FOO=bar\n")
    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    assert ".env" not in {f.path for f in snapshot.files}

    # A .atcallow file at the repo root is loaded by scan() via load_allowlist
    # and overrides the denylist: the previously-excluded .env is now included
    # and reported safe.
    (git_repo / ".atcallow").write_text(".env\n")
    snapshot = scan(gc, cfg)
    env = [f for f in snapshot.files if f.path == ".env"]
    assert env, ".env should be included after .atcallow override"
    assert env[0].safety.safe is True
