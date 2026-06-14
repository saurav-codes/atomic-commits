"""Map/reduce planning (implementation.md sections 13-15, 24).

Builds a context pack, chunks diffs by a token budget (keeping hunks intact),
runs the AI map phase per chunk and the reduce phase to produce a CommitPlan,
then validates the final plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import prompts
from .config import RunConfig
from .errors import InvalidAIResponseError, PlanValidationError
from .git_client import GitClient
from .models import ChunkReview, CommitPlan, ContextPack, WorktreeSnapshot
from .providers.base import AIProvider
from .scanner import safe_files
from .validators.plan_schema import validate_plan

INSTRUCTION_FILES = ["AGENTS.md", ".cursorrules", "CLAUDE.md", "README.md"]
MAX_INSTRUCTION_CHARS = 4000


def _estimate_tokens(text: str) -> int:
    # Cheap heuristic: ~4 chars per token.
    return max(1, len(text) // 4)


def _load_instructions(repo: Path) -> str:
    collected: list[str] = []
    budget = MAX_INSTRUCTION_CHARS
    for name in INSTRUCTION_FILES:
        fp = repo / name
        if fp.is_file():
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            snippet = text[:budget]
            collected.append(f"# {name}\n{snippet}")
            budget -= len(snippet)
            if budget <= 0:
                break
    return "\n\n".join(collected)


def build_context_pack(git: GitClient, snapshot: WorktreeSnapshot, cfg: RunConfig) -> ContextPack:
    files = safe_files(snapshot)
    file_list = [
        {"path": f.path, "status": f.status, "hunks": len(f.hunks)} for f in files
    ]
    hunk_inventory = [
        {"hunk_id": h.hunk_id, "file_path": h.file_path, "header": h.header}
        for f in files
        for h in f.hunks
    ]
    exclusions = [
        f"{f.path}: {', '.join(f.safety.reasons)}"
        for f in snapshot.files
        if not f.safety.safe
    ]
    return ContextPack(
        repo_name=snapshot.repo_root.name,
        branch=snapshot.branch,
        recent_subjects=git.recent_subjects(8),
        mode=cfg.mode,
        safety_exclusions=exclusions,
        file_list=file_list,
        diffstat=git.diffstat(),
        instructions=_load_instructions(snapshot.repo_root),
        hunk_inventory=hunk_inventory,
    )


def chunk_hunks(snapshot: WorktreeSnapshot, max_chunk_tokens: int) -> list[dict[str, Any]]:
    """Group hunks into chunks under a token budget, keeping each hunk intact."""
    chunks: list[dict[str, Any]] = []
    current_diff: list[str] = []
    current_ids: list[str] = []
    current_tokens = 0
    idx = 0

    def flush() -> None:
        nonlocal current_diff, current_ids, current_tokens, idx
        if current_ids:
            chunks.append(
                {"chunk_id": f"chunk-{idx}", "diff": "".join(current_diff), "hunk_ids": current_ids}
            )
            idx += 1
            current_diff = []
            current_ids = []
            current_tokens = 0

    for f in safe_files(snapshot):
        for h in f.hunks:
            block = f"--- {h.file_path}\n{h.patch}\n"
            t = _estimate_tokens(block)
            if current_tokens + t > max_chunk_tokens and current_ids:
                flush()
            current_diff.append(block)
            current_ids.append(h.hunk_id)
            current_tokens += t
    flush()
    return chunks


async def run_map(
    provider: AIProvider, context: ContextPack, chunks: list[dict[str, Any]], cfg: RunConfig
) -> list[ChunkReview]:
    reviews: list[ChunkReview] = []
    ctx = context.model_dump()
    for chunk in chunks:
        raw = await provider.complete_json(
            system=prompts.MAP_SYSTEM,
            user=prompts.map_user(ctx, chunk["chunk_id"], chunk["diff"], chunk["hunk_ids"]),
            schema_name="ChunkReview",
            max_tokens=cfg.max_chunk_tokens,
            temperature=cfg.temperature,
        )
        raw.setdefault("chunk_id", chunk["chunk_id"])
        try:
            reviews.append(ChunkReview.model_validate(raw))
        except ValidationError as exc:
            raise InvalidAIResponseError(f"invalid map review for {chunk['chunk_id']}: {exc}") from exc
    return reviews


async def run_reduce(
    provider: AIProvider,
    context: ContextPack,
    reviews: list[ChunkReview],
    snapshot: WorktreeSnapshot,
    cfg: RunConfig,
) -> CommitPlan:
    hunk_inventory = context.hunk_inventory
    ctx = context.model_dump()
    user = prompts.reduce_user(
        ctx,
        [r.model_dump() for r in reviews],
        hunk_inventory,
        cfg.mode,
        snapshot.fingerprint,
        snapshot.head_sha,
    )
    raw = await provider.complete_json(
        system=prompts.REDUCE_SYSTEM,
        user=user,
        schema_name="CommitPlan",
        max_tokens=cfg.max_reducer_tokens,
        temperature=cfg.temperature,
    )
    raw.setdefault("repo_fingerprint", snapshot.fingerprint)
    raw.setdefault("base_head", snapshot.head_sha)
    raw.setdefault("mode", cfg.mode)
    try:
        return CommitPlan.model_validate(raw)
    except ValidationError as exc:
        raise InvalidAIResponseError(f"invalid commit plan: {exc}") from exc


async def plan(
    provider: AIProvider, git: GitClient, snapshot: WorktreeSnapshot, cfg: RunConfig
) -> CommitPlan:
    """Full map/reduce planning with one validation pass."""
    context = build_context_pack(git, snapshot, cfg)
    chunks = chunk_hunks(snapshot, cfg.max_chunk_tokens)
    reviews = await run_map(provider, context, chunks, cfg)

    files = safe_files(snapshot)
    safe_ids = [h.hunk_id for f in files for h in f.hunks]
    hunk_to_path = {h.hunk_id: h.file_path for f in files for h in f.hunks}

    commit_plan = await run_reduce(provider, context, reviews, snapshot, cfg)

    errors = validate_plan(
        commit_plan, safe_hunk_ids=safe_ids, hunk_to_path=hunk_to_path, mode=cfg.mode
    )
    if errors:
        raise PlanValidationError("plan validation failed:\n  " + "\n  ".join(errors))
    return commit_plan
