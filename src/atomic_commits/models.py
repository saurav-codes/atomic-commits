"""Pydantic data models for atc.

See implementation.md sections 10, 13, and 24.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Mode = Literal["compact", "verbose"]
FileStatus = Literal["modified", "added", "deleted", "renamed", "mode", "binary"]
Risk = Literal["low", "medium", "high"]


class SafetyResult(BaseModel):
    """Outcome of safety evaluation for a file or hunk."""

    safe: bool = True
    reasons: list[str] = Field(default_factory=list)
    binary: bool = False
    excluded_path: bool = False


class Hunk(BaseModel):
    hunk_id: str
    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    context_before: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)
    patch: str = ""
    fingerprint: str = ""


class FileChange(BaseModel):
    path: str
    old_path: str | None = None
    status: FileStatus
    is_tracked: bool = True
    is_binary: bool = False
    hunks: list[Hunk] = Field(default_factory=list)
    safety: SafetyResult = Field(default_factory=SafetyResult)


class StatusEntry(BaseModel):
    xy: str
    path: str
    orig_path: str | None = None


class WorktreeSnapshot(BaseModel):
    repo_root: Path
    branch: str
    head_sha: str
    status_entries: list[StatusEntry] = Field(default_factory=list)
    files: list[FileChange] = Field(default_factory=list)
    fingerprint: str = ""


class CommitGroup(BaseModel):
    group_id: str
    message: str
    rationale: str = ""
    hunk_ids: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)
    risk: Risk = "low"


class ExcludedChange(BaseModel):
    path: str
    reason: str
    hunk_ids: list[str] = Field(default_factory=list)


class CommitPlan(BaseModel):
    version: Literal["1"] = "1"
    mode: Mode = "compact"
    repo_fingerprint: str
    base_head: str
    groups: list[CommitGroup] = Field(default_factory=list)
    excluded: list[ExcludedChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SuggestedGroup(BaseModel):
    subject: str
    hunk_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class ChunkReview(BaseModel):
    chunk_id: str
    summary: str = ""
    detected_concerns: list[str] = Field(default_factory=list)
    suggested_groups: list[SuggestedGroup] = Field(default_factory=list)
    risky_hunks: list[str] = Field(default_factory=list)
    message_terms: dict[str, list[str]] = Field(default_factory=dict)


class AppliedCommit(BaseModel):
    group_id: str
    message: str
    sha: str | None = None
    status: Literal["pending", "committed", "failed", "skipped"] = "pending"
    detail: str = ""


class ContextPack(BaseModel):
    repo_name: str = ""
    branch: str = ""
    recent_subjects: list[str] = Field(default_factory=list)
    mode: Mode = "compact"
    safety_exclusions: list[str] = Field(default_factory=list)
    file_list: list[dict[str, Any]] = Field(default_factory=list)
    diffstat: str = ""
    instructions: str = ""
    hunk_inventory: list[dict[str, Any]] = Field(default_factory=list)
