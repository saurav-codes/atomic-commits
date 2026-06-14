from atomic_commits.safety import (
    evaluate_path,
    path_excluded,
)


def test_env_excluded():
    excluded, reason = path_excluded(".env")
    assert excluded is True
    assert reason


def test_env_sample_allowed():
    excluded, _ = path_excluded(".env.sample")
    assert excluded is False


def test_node_modules_excluded():
    excluded, _ = path_excluded("node_modules/lib/index.js")
    assert excluded is True


def test_cache_dirs_excluded():
    for p in (".venv/x.py", "__pycache__/m.pyc", ".mypy_cache/a", "dist/app.js"):
        assert path_excluded(p)[0] is True


def test_safe_source_file_not_excluded():
    assert path_excluded("src/app/main.py")[0] is False


def test_evaluate_content_does_not_scan_sensitive_values():
    res = evaluate_path(
        "config.py",
        is_binary=False,
        content='SECRET = "AKIAABCDEFGHIJKLMNOP"',
        allow_binary=False,
    )
    assert res.safe is True


def test_binary_refused_by_default():
    res = evaluate_path("a.png", is_binary=True, content=None, allow_binary=False)
    assert res.safe is False


def test_safe_image_allowed_with_flag():
    res = evaluate_path("a.png", is_binary=True, content=None, allow_binary=True)
    assert res.safe is True


def test_unknown_binary_refused_even_with_flag():
    res = evaluate_path("a.zip", is_binary=True, content=None, allow_binary=True)
    assert res.safe is False
