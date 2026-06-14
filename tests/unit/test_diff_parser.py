from atomic_commits.diff_parser import parse_patch, build_patch_for_hunks


MODIFIED = """diff --git a/foo.py b/foo.py
index 111..222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 line1
 line2
+inserted
 line3
"""

ADDED = """diff --git a/new.py b/new.py
new file mode 100644
index 000..333
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+hello
+world
"""

DELETED = """diff --git a/gone.py b/gone.py
deleted file mode 100644
index 444..000
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-old1
-old2
"""

RENAME = """diff --git a/old.py b/renamed.py
similarity index 100%
rename from old.py
rename to renamed.py
"""

MODE = """diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
"""

BINARY = """diff --git a/img.png b/img.png
new file mode 100644
index 000..555
Binary files /dev/null and b/img.png differ
"""

ADJACENT = """diff --git a/multi.py b/multi.py
index 111..222 100644
--- a/multi.py
+++ b/multi.py
@@ -1,2 +1,3 @@
 a
+b
 c
@@ -10,2 +11,3 @@
 x
+y
 z
"""

SPACES = 'diff --git a/"dir name/file name.py" b/"dir name/file name.py"\nindex 1..2 100644\n--- a/"dir name/file name.py"\n+++ b/"dir name/file name.py"\n@@ -1 +1,2 @@\n a\n+b\n'

NO_NEWLINE = """diff --git a/eof.py b/eof.py
index 1..2 100644
--- a/eof.py
+++ b/eof.py
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""


def test_modified_single_hunk():
    files = parse_patch(MODIFIED)
    assert len(files) == 1
    f = files[0]
    assert f.path == "foo.py"
    assert f.status == "modified"
    assert len(f.hunks) == 1
    assert f.hunks[0].added == ["inserted"]


def test_added_file():
    files = parse_patch(ADDED)
    assert files[0].status == "added"
    assert files[0].path == "new.py"
    assert files[0].hunks[0].added == ["hello", "world"]


def test_deleted_file():
    files = parse_patch(DELETED)
    assert files[0].status == "deleted"
    assert files[0].hunks[0].removed == ["old1", "old2"]


def test_rename():
    files = parse_patch(RENAME)
    assert files[0].status == "renamed"
    assert files[0].old_path == "old.py"
    assert files[0].path == "renamed.py"


def test_mode_only():
    files = parse_patch(MODE)
    assert files[0].status == "mode"


def test_binary():
    files = parse_patch(BINARY)
    assert files[0].is_binary is True
    assert files[0].status == "binary"
    assert files[0].hunks == []


def test_adjacent_hunks():
    files = parse_patch(ADJACENT)
    assert len(files[0].hunks) == 2
    assert files[0].hunks[0].hunk_id.endswith("::hunk::1")
    assert files[0].hunks[1].hunk_id.endswith("::hunk::2")


def test_quoted_path_with_spaces():
    files = parse_patch(SPACES)
    assert files[0].path == "dir name/file name.py"


def test_no_newline_marker_ignored():
    files = parse_patch(NO_NEWLINE)
    hunk = files[0].hunks[0]
    assert hunk.removed == ["old"]
    assert hunk.added == ["new"]


def test_build_patch_round_trip():
    files = parse_patch(MODIFIED)
    f = files[0]
    patch = build_patch_for_hunks(f, f.hunks)
    assert patch.startswith("diff --git a/foo.py b/foo.py")
    assert "+inserted" in patch


def test_build_patch_preserves_no_newline_marker():
    files = parse_patch(NO_NEWLINE)
    f = files[0]
    patch = build_patch_for_hunks(f, f.hunks)
    # The reconstructed patch must keep the no-newline marker and must not
    # introduce a spurious trailing blank line after it.
    assert "\\ No newline at end of file" in patch
    assert not patch.endswith("\n\n")


def test_added_file_round_trip_headers():
    files = parse_patch(ADDED)
    f = files[0]
    patch = build_patch_for_hunks(f, f.hunks)
    assert "--- /dev/null" in patch
    assert "+++ b/new.py" in patch
