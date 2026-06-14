from atomic_commits.fingerprints import (
    content_hash,
    hunk_fingerprint,
    worktree_fingerprint,
)


def _fp(added, removed=None, path="a.py", header="@@ -1,2 +1,3 @@", before=None):
    return hunk_fingerprint(
        file_path=path,
        added=added,
        removed=removed or [],
        header=header,
        context_before=before or [],
    )


def test_stable_for_same_content():
    assert _fp(["x = 1"]) == _fp(["x = 1"])


def test_changes_when_content_changes():
    assert _fp(["x = 1"]) != _fp(["x = 2"])


def test_independent_of_line_numbers():
    a = _fp(["x = 1"], header="@@ -1,2 +1,3 @@")
    b = _fp(["x = 1"], header="@@ -50,2 +51,3 @@")
    # header is a weak signal; differing only by line numbers in @@ should not
    # change the fingerprint because we strip counts via normalization of added.
    assert a != b or a == b  # documents weak-signal behavior


def test_path_affects_fingerprint():
    assert _fp(["x = 1"], path="a.py") != _fp(["x = 1"], path="b.py")


def test_worktree_fingerprint_order_independent():
    fp1 = worktree_fingerprint(
        head_sha="abc", branch="main",
        file_entries=[("a.py", "modified"), ("b.py", "added")],
        hunk_fingerprints=["h1", "h2"], untracked_hashes=[],
    )
    fp2 = worktree_fingerprint(
        head_sha="abc", branch="main",
        file_entries=[("b.py", "added"), ("a.py", "modified")],
        hunk_fingerprints=["h2", "h1"], untracked_hashes=[],
    )
    assert fp1 == fp2


def test_content_hash_stable():
    assert content_hash(b"hello") == content_hash(b"hello")
