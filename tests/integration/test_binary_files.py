from atomic_commits.config import RunConfig
from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan

from .helpers import git, make_cfg


def test_binary_refused_by_default(git_repo):
    (git_repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02binary\x00data")
    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    binf = [f for f in snapshot.files if f.path == "logo.png"]
    assert binf and binf[0].safety.safe is False


def test_safe_image_allowed_with_flag(git_repo):
    (git_repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02binary\x00data")
    cfg = make_cfg(git_repo, allow_binary=True)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    binf = [f for f in snapshot.files if f.path == "logo.png"]
    assert binf and binf[0].safety.safe is True
