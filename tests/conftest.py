"""Shared pytest fixtures: temporary git repos and a mock AI provider."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest


def _git(repo: Path, *args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True, **kwargs
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# Project\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial commit")
    return repo


class MockProvider:
    """Deterministic provider: one commit group per safe hunk."""

    def __init__(self, mode: str = "verbose") -> None:
        self.mode = mode
        self.calls: list[str] = []

    async def complete_json(
        self, *, system: str, user: str, schema_name: str, max_tokens: int, temperature: float
    ) -> dict[str, Any]:
        self.calls.append(schema_name)
        import json

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
        # CommitPlan: one group per hunk in the inventory.
        inventory = payload["context"]["hunk_inventory"]
        groups = []
        for i, h in enumerate(inventory, start=1):
            groups.append(
                {
                    "group_id": f"g{i}",
                    "message": f"feat(core): apply change {i} to {Path(h['file_path']).stem}",
                    "rationale": "mock grouping",
                    "hunk_ids": [h["hunk_id"]],
                    "file_paths": [h["file_path"]],
                    "risk": "low",
                }
            )
        return {
            "version": "1",
            "mode": payload.get("mode", self.mode),
            "repo_fingerprint": payload["repo_fingerprint"],
            "base_head": payload["base_head"],
            "groups": groups,
            "excluded": [],
            "warnings": [],
        }


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def run_git():
    return _git
