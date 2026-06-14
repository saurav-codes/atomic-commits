"""Session storage and resume support (implementation.md section 18).

Sessions live under .git/atc/sessions/<session_id>/ with a latest-plan pointer
at .git/atc/plan.json.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from .git_client import GitClient
from .models import AppliedCommit, CommitPlan, WorktreeSnapshot


def new_session_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


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

    # -- create / write ----------------------------------------------------
    def create(self) -> str:
        session_id = new_session_id()
        self.session_path(session_id).mkdir(parents=True, exist_ok=True)
        return session_id

    def _write_json(self, path: Path, obj) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    def write_snapshot(self, session_id: str, snapshot: WorktreeSnapshot) -> None:
        self._write_json(self.session_path(session_id) / "snapshot.json", snapshot.model_dump())

    def write_chunk_reviews(self, session_id: str, reviews: list) -> None:
        self._write_json(
            self.session_path(session_id) / "chunk_reviews.json",
            [r.model_dump() if hasattr(r, "model_dump") else r for r in reviews],
        )

    def write_plan(self, session_id: str, plan: CommitPlan) -> None:
        data = plan.model_dump()
        self._write_json(self.session_path(session_id) / "plan.json", data)
        self._write_json(self.latest_plan_path, data)

    def write_apply_log(self, session_id: str, applied: list[AppliedCommit]) -> None:
        self._write_json(
            self.session_path(session_id) / "apply_log.json",
            [a.model_dump() for a in applied],
        )

    def write_backup(self, session_id: str, patch: bytes) -> None:
        path = self.session_path(session_id) / "backup.patch"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(patch)

    def write_excluded(self, session_id: str, plan: CommitPlan) -> None:
        self._write_json(
            self.session_path(session_id) / "excluded.json",
            [e.model_dump() for e in plan.excluded],
        )

    # -- read --------------------------------------------------------------
    def load_latest_plan(self) -> CommitPlan | None:
        if not self.latest_plan_path.is_file():
            return None
        data = json.loads(self.latest_plan_path.read_text(encoding="utf-8"))
        return CommitPlan.model_validate(data)

    def load_plan_file(self, path: Path) -> CommitPlan:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return CommitPlan.model_validate(data)

    def load_snapshot(self, session_id: str) -> WorktreeSnapshot | None:
        path = self.session_path(session_id) / "snapshot.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return WorktreeSnapshot.model_validate(data)

    def list_sessions(self) -> list[str]:
        if not self.sessions_dir.is_dir():
            return []
        return sorted((p.name for p in self.sessions_dir.iterdir() if p.is_dir()), reverse=True)

    def load_apply_log(self, session_id: str) -> list[AppliedCommit]:
        path = self.session_path(session_id) / "apply_log.json"
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [AppliedCommit.model_validate(a) for a in data]

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
