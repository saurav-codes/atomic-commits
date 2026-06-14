from atomic_commits.validators.commit_messages import validate_commit_message


def test_accepts_specific_message():
    assert validate_commit_message("backend(auth): add login rate-limit response") == []


def test_rejects_generic_update():
    reasons = validate_commit_message("backend: update core files")
    assert reasons


def test_rejects_filename_only():
    reasons = validate_commit_message("foo.py")
    assert reasons


def test_rejects_too_long():
    long = "backend(auth): " + "x" * 80
    reasons = validate_commit_message(long)
    assert any("longer than" in r for r in reasons)


def test_rejects_wip():
    assert validate_commit_message("chore: wip") != []


def test_requires_object_after_verb():
    assert validate_commit_message("backend(auth): add") != []


def test_generic_verb_with_specific_object_allowed():
    # 'change' is generic, but paired with a specific object it is acceptable.
    assert validate_commit_message("backend(auth): change token expiry window") == []


def test_entirely_generic_rejected():
    assert validate_commit_message("chore: misc cleanup stuff") != []
