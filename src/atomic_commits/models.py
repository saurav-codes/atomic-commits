"""Pydantic data models for atc.

See implementation.md sections 10, 13, and 24.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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


class ChangeUnit(BaseModel):
    """One exact changed region plus local, non-AI context."""

    unit_id: str
    hunk_id: str
    file_path: str
    status: FileStatus
    language: str = "text"
    symbol: str = ""
    change_kind: str = "code"
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class ChangeLink(BaseModel):
    """A locally proven relationship between two changed regions."""

    source: str
    target: str
    kind: str
    reason: str


class ChangeGraph(BaseModel):
    units: list[ChangeUnit] = Field(default_factory=list)
    links: list[ChangeLink] = Field(default_factory=list)


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
    depends_on: list[str] = Field(default_factory=list)
    unsplittable_reason: str = ""


class ExcludedChange(BaseModel):
    path: str
    reason: str
    hunk_ids: list[str] = Field(default_factory=list)


class CommitPlan(BaseModel):
    version: Literal["1"] = "1"
    mode: Mode = "compact"

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, value: Any) -> Any:
        # Models sometimes return the schema version as an integer (e.g. 1);
        # normalize to the string literal the field expects.
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return str(value)
        return value
    repo_fingerprint: str
    base_head: str
    groups: list[CommitGroup] = Field(default_factory=list)
    excluded: list[ExcludedChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChunkReview(BaseModel):
    chunk_id: str
    hunk_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    detected_concerns: list[str] = Field(default_factory=list)
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
