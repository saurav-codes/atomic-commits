"""Session storage and resume support (implementation.md section 18).

Sessions live under .git/atc/sessions/<session_id>/ with a latest-plan pointer
at .git/atc/plan.json.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .git_client import GitClient
from .models import AppliedCommit, CommitPlan, WorktreeSnapshot


def new_session_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


_SESSION_ID_RE = re.compile(r"^(\d{8}-\d{6})-")


class SessionStore:
    def __init__(self, git: GitClient) -> None:
        self.git = git
        self.root = git.git_dir() / "atc"
        self.sessions_dir = self.root / "sessions"

    # -- paths -------------------------------------------------------------
    def session_path(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    @property
    def latest_plan_path(self) -> Path:
        return self.root / "plan.json"

    # -- locking -----------------------------------------------------------
    @contextmanager
    def _lock(self):
        """Hold an exclusive flock on .git/atc/lock for the duration of a write.

        Prevents two concurrent `atc --apply` processes from interleaving
        writes to session files and the latest-plan pointer (IMPROVEMENTS 1.8).
        fcntl is Unix-only; the project targets mac/Linux.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / "lock"
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    # -- create / write ----------------------------------------------------
    def create(self) -> str:
        with self._lock():
            session_id = new_session_id()
            self.session_path(session_id).mkdir(parents=True, exist_ok=True)
            return session_id

    def _write_json(self, path: Path, obj) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(obj, indent=2, default=str).encode("utf-8")
        # Atomic write: write to a sibling temp then os.replace into place, so
        # an interruption never leaves a truncated JSON file that would brick
        # `atc resume` (the recovery command). Created with mode 0o600 so the
        # file is owner-only from the start (no world-readable window before
        # chmod). IMPROVEMENTS 7.3.
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            # Clean up the temp if replace failed; never let it linger.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def write_snapshot(self, session_id: str, snapshot: WorktreeSnapshot) -> None:
        with self._lock():
            self._write_json(self.session_path(session_id) / "snapshot.json", snapshot.model_dump())

    def write_plan(self, session_id: str, plan: CommitPlan) -> None:
        data = plan.model_dump()
        with self._lock():
            self._write_json(self.session_path(session_id) / "plan.json", data)
            self._write_json(self.latest_plan_path, data)

    def write_apply_log(self, session_id: str, applied: list[AppliedCommit]) -> None:
        with self._lock():
            self._write_json(
                self.session_path(session_id) / "apply_log.json",
                [a.model_dump() for a in applied],
            )

    def write_backup(self, session_id: str, patch: bytes) -> None:
        with self._lock():
            path = self.session_path(session_id) / "backup.patch"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(patch)
            os.chmod(path, 0o600)

    # -- read --------------------------------------------------------------
    def load_latest_plan(self) -> CommitPlan | None:
        if not self.latest_plan_path.is_file():
            return None
        try:
            return CommitPlan.model_validate_json(self.latest_plan_path.read_bytes())
        except (ValidationError, ValueError):
            return None

    def load_plan_file(self, path: Path) -> CommitPlan:
        return CommitPlan.model_validate_json(Path(path).read_bytes())

    def load_snapshot(self, session_id: str) -> WorktreeSnapshot | None:
        path = self.session_path(session_id) / "snapshot.json"
        if not path.is_file():
            return None
        try:
            return WorktreeSnapshot.model_validate_json(path.read_bytes())
        except (ValidationError, ValueError):
            return None

    def list_sessions(self) -> list[str]:
        if not self.sessions_dir.is_dir():
            return []
        return sorted(
            (p.name for p in self.sessions_dir.iterdir() if p.is_dir() and _SESSION_ID_RE.match(p.name)),
            reverse=True,
        )

    def load_apply_log(self, session_id: str) -> list[AppliedCommit]:
        path = self.session_path(session_id) / "apply_log.json"
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_bytes())
        except ValueError:
            return []
        return [AppliedCommit.model_validate(a) for a in data]

    # -- metadata (CONTRACT for cli agent) ---------------------------------
    def list_sessions_metadata(self) -> list[dict[str, Any]]:
        """Per-session summary dicts: {id, timestamp, status, commit_count}.

        Reads each session's apply_log.json best-effort. Missing fields
        default to None. Status is derived from the apply log:
        "planned" (no/pending log), "committed" (all committed/skipped),
        "failed" (any failed), "pending" (any pending).
        """
        result: list[dict[str, Any]] = []
        for sid in self.list_sessions():
            timestamp: str | None = None
            m = _SESSION_ID_RE.match(sid)
            if m:
                timestamp = m.group(1)

            status: str | None = None
            commit_count: int | None = None
            log_path = self.session_path(sid) / "apply_log.json"
            if log_path.is_file():
                try:
                    data = json.loads(log_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    data = None
                if isinstance(data, list):
                    statuses = [
                        e.get("status") for e in data if isinstance(e, dict)
                    ]
                    commit_count = sum(1 for s in statuses if s == "committed")
                    if "failed" in statuses:
                        status = "failed"
                    elif "pending" in statuses:
                        status = "pending"
                    elif statuses and all(
                        s in ("committed", "skipped") for s in statuses
                    ):
                        status = "committed"
                    elif not statuses:
                        status = "planned"
            else:
                status = "planned"
                commit_count = None

            result.append(
                {
                    "id": sid,
                    "timestamp": timestamp,
                    "status": status,
                    "commit_count": commit_count,
                }
            )
        return result

    def latest_incomplete_session(self) -> str | None:
        for sid in self.list_sessions():
            log = self.load_apply_log(sid)
            if not log:
                # Planned but never applied.
                if (self.session_path(sid) / "plan.json").is_file():
                    return sid
                continue
            if any(a.status in ("pending", "failed") for a in log):
                return sid
        return None
