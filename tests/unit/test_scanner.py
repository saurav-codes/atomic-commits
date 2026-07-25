from __future__ import annotations

from atomic_commits.scanner import _rename_hunk, parse_status_z


def test_parse_status_z_rename_keeps_new_path_and_old_path():
    """porcelain v1 -z emits 'XY new\\0old\\0' for renames; path must be the new
    path and orig_path the old one (regression: they were swapped)."""
    # "R  " (renamed in worktree) + space + "new.py", then NUL, then "old.py", NUL.
    raw = b"R  new.py\x00old.py\x00"
    entries = parse_status_z(raw)
    assert len(entries) == 1
    e = entries[0]
    assert e.xy == "R "
    assert e.path == "new.py"
    assert e.orig_path == "old.py"


def test_parse_status_z_plain_modification_no_orig():
    """A normal modification has no second token and orig_path is None."""
    raw = b" M app.py\x00"
    entries = parse_status_z(raw)
    assert len(entries) == 1
    assert entries[0].path == "app.py"
    assert entries[0].orig_path is None


def test_rename_hunk_has_file_level_hunk_shape():
    h = _rename_hunk("new.py", "old.py")
    assert h.hunk_id == "new.py::hunk::1"
    assert h.file_path == "new.py"
    assert h.header == "@@ file-level renamed @@"
    assert "rename from old.py" in h.added
    assert "rename to new.py" in h.added
    assert h.fingerprint  # non-empty fingerprint
