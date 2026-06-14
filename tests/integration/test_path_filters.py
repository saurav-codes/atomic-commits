from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan

from .helpers import make_cfg


def test_sensitive_looking_content_is_not_scanned(git_repo):
    (git_repo / "settings.py").write_text(
        'AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n'
    )
    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    assert any(f.path == "settings.py" for f in snapshot.files)


def test_env_file_excluded(git_repo, mock_provider):
    (git_repo / ".env").write_text("FOO=bar\n")
    (git_repo / "ok.py").write_text("x = 1\n")
    cfg = make_cfg(git_repo)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    paths = {f.path for f in snapshot.files}
    assert ".env" not in paths
    assert "ok.py" in paths
