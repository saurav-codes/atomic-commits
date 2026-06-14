"""Shared helpers for integration tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

from atomic_commits import planner
from atomic_commits.config import RunConfig
from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan


def git(repo: Path, *args: str):
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True
    )


def commit_count(repo: Path) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=str(repo), capture_output=True, text=True
    )
    return int(out.stdout.strip() or "0")


def staged_paths(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=str(repo), capture_output=True, text=True
    )
    return [p for p in out.stdout.splitlines() if p]


def make_cfg(repo: Path, mode: str = "verbose", **kw) -> RunConfig:
    cfg = RunConfig(repo=repo, mode=mode)
    cfg.model = "mock-model"
    cfg.api_key = "mock-key"
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def plan_with(provider, git_client: GitClient, cfg: RunConfig):
    import asyncio

    snapshot = scan(git_client, cfg)
    return snapshot, asyncio.run(planner.plan(provider, git_client, snapshot, cfg))


__all__ = [
    "git",
    "commit_count",
    "staged_paths",
    "make_cfg",
    "plan_with",
]
