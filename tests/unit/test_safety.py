from atomic_commits.safety import (
    Allowlist,
    evaluate_path,
    load_allowlist,
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
    # ponytail: removed content param since evaluator doesn't inspect it.
    res = evaluate_path(
        "config.py",
        is_binary=False,
        allow_binary=False,
    )
    assert res.safe is True


def test_binary_refused_by_default():
    res = evaluate_path("a.png", is_binary=True, allow_binary=False)
    assert res.safe is False


def test_safe_image_allowed_with_flag():
    res = evaluate_path("a.png", is_binary=True, allow_binary=True)
    assert res.safe is True


def test_unknown_binary_refused_even_with_flag():
    res = evaluate_path("a.zip", is_binary=True, allow_binary=True)
    assert res.safe is False


def test_atcallow_overrides_env_exclusion():
    al = Allowlist([".env"])
    excluded, reason = path_excluded(".env", allowlist=al)
    assert excluded is False
    assert reason is None


def test_atcallow_overrides_key_exclusion():
    al = Allowlist(["test.key"])
    excluded, _ = path_excluded("test.key", allowlist=al)
    assert excluded is False


def test_atcallow_glob_matches_subpath():
    al = Allowlist(["fixtures/*.key"])
    excluded, _ = path_excluded("fixtures/secrets.key", allowlist=al)
    assert excluded is False


def test_atcallow_basename_matches_any_dir():
    # A bare filename glob matches the basename anywhere in the tree.
    al = Allowlist(["test.key"])
    excluded, _ = path_excluded("config/test.key", allowlist=al)
    assert excluded is False


def test_atcallow_does_not_permit_unmatched_denylist():
    al = Allowlist(["test.key"])
    excluded, _ = path_excluded(".env", allowlist=al)
    assert excluded is True


def test_atcallow_overrides_dir_component_exclusion():
    al = Allowlist(["node_modules/lib/index.js"])
    excluded, _ = path_excluded("node_modules/lib/index.js", allowlist=al)
    assert excluded is False


def test_no_atcallow_keeps_default_denylist():
    # No allowlist argument => default denylist behavior is unchanged.
    assert path_excluded(".env")[0] is True
    assert path_excluded("test.key")[0] is True


def test_load_allowlist_absent(tmp_path):
    al = load_allowlist(tmp_path)
    assert bool(al) is False
    assert al.patterns == []
    # Empty allowlist does not override the denylist.
    assert path_excluded(".env", allowlist=al)[0] is True


def test_load_allowlist_reads_file(tmp_path):
    (tmp_path / ".atcallow").write_text(
        "# allow test fixtures\nfixtures/*.key\n.env\n\n"
    )
    al = load_allowlist(tmp_path)
    assert al.patterns == ["fixtures/*.key", ".env"]
    assert path_excluded("fixtures/secrets.key", allowlist=al)[0] is False
    assert path_excluded(".env", allowlist=al)[0] is False
    # A non-allowed denylist entry is still excluded.
    assert path_excluded("other.key", allowlist=al)[0] is True


def test_evaluate_path_respects_allowlist():
    al = Allowlist(["test.key"])
    res = evaluate_path(
        "test.key",
        is_binary=False,
        allow_binary=False,
        allowlist=al,
    )
    assert res.safe is True
    assert res.excluded_path is False
