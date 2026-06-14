import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from atomic_commits import planner
from atomic_commits.errors import PlanValidationError
from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan

from tests.integration.helpers import make_cfg


class InvalidPlanProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete_json(
        self, *, system: str, user: str, schema_name: str, max_tokens: int, temperature: float
    ) -> dict[str, Any]:
        self.calls.append(schema_name)
        payload = json.loads(user)
        if schema_name == "ChunkReview":
            return {
                "chunk_id": payload["chunk_id"],
                "summary": "mock review",
                "detected_concerns": [],
                "suggested_groups": [],
                "risky_hunks": [],
                "message_terms": {},
            }
        return {
            "version": "1",
            "mode": payload["mode"],
            "repo_fingerprint": payload["repo_fingerprint"],
            "base_head": payload["base_head"],
            "groups": [
                {
                    "group_id": "g1",
                    "message": "feat(core): apply one change",
                    "rationale": "mock grouping",
                    "hunk_ids": ["missing::hunk::1"],
                    "file_paths": [payload["context"]["hunk_inventory"][0]["file_path"]],
                    "risk": "low",
                }
            ],
            "excluded": [],
            "warnings": [],
        }


def test_planner_does_not_retry_invalid_plan(git_repo):
    (git_repo / "a.py").write_text("x = 1\n")
    from tests.integration.helpers import git

    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("x = 2\n")

    cfg = make_cfg(Path(git_repo), mode="compact")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    provider = InvalidPlanProvider()

    with pytest.raises(PlanValidationError, match="plan validation failed"):
        asyncio.run(planner.plan(provider, gc, snapshot, cfg))

    assert provider.calls == ["ChunkReview", "CommitPlan"]
