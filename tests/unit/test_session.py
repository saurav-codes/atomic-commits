"""Regression tests for SessionStore JSON robustness.

A truncated session file must not brick `atc resume` (the recovery command)
with a raw json.JSONDecodeError. Readers treat corrupt JSON like a missing
file, and _write_json writes atomically so an interruption never leaves a
truncated file in the first place.
"""

from __future__ import annotations

import json
from pathlib import Path

from atomic_commits.git_client import GitClient
from atomic_commits.models import CommitPlan
from atomic_commits.session import SessionStore


def _store(repo: Path) -> SessionStore:
    return SessionStore(GitClient(repo))


def test_load_snapshot_treats_corrupt_json_as_missing(git_repo):
    """A truncated snapshot.json must not brick `atc resume` with JSONDecodeError."""
    store = _store(git_repo)
    sid = store.create()
    snap_path = store.session_path(sid) / "snapshot.json"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text("{truncated", encoding="utf-8")
    assert store.load_snapshot(sid) is None


def test_load_apply_log_treats_corrupt_json_as_empty(git_repo):
    store = _store(git_repo)
    sid = store.create()
    log_path = store.session_path(sid) / "apply_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("not json", encoding="utf-8")
    assert store.load_apply_log(sid) == []


def test_load_latest_plan_treats_corrupt_json_as_missing(git_repo):
    store = _store(git_repo)
    store.root.mkdir(parents=True, exist_ok=True)
    store.latest_plan_path.write_text("{broken", encoding="utf-8")
    assert store.load_latest_plan() is None


def test_write_json_leaves_no_tmp_and_is_atomic(git_repo):
    store = _store(git_repo)
    sid = store.create()
    target = store.session_path(sid) / "plan.json"
    store.write_plan(
        sid,
        CommitPlan(
            repo_fingerprint="x",
            base_head="y",
        ),
    )
    assert target.is_file()
    # JSON is valid and parseable.
    json.loads(target.read_text(encoding="utf-8"))
    # No leftover temp file.
    assert not target.with_suffix(target.suffix + ".tmp").is_file()
