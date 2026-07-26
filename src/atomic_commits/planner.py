"""Adaptive direct and evidence-assisted planning.

Builds a context pack, chunks diffs by a token budget (keeping hunks intact),
runs the AI map phase per chunk and the reduce phase to produce a CommitPlan,
then validates the final plan.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import output, prompts
from .change_analysis import build_change_graph, connected_groups
from .config import DEFAULT_DEADLINE, DEFAULT_DIRECT_MAX_TOKENS, RunConfig
from .errors import InvalidAIResponseError, PlanValidationError, ProviderError
from .git_client import GitClient
from .models import ChangeGraph, ChunkReview, CommitGroup, CommitPlan, ContextPack, WorktreeSnapshot
from .providers.base import AIProvider
from .validators.commit_messages import validate_commit_message
from .validators.plan_schema import validate_plan

INSTRUCTION_FILES = ["AGENTS.md", ".cursorrules", "CLAUDE.md", "README.md"]
MAX_INSTRUCTION_CHARS = 4000

_EVIDENCE_INPUT_TOKENS = 4000
_LEAD_INPUT_TOKENS = 16000
_BOUNDED_PLAN_WARNING = "ATC planned this large change in bounded semantic batches."


def _estimate_tokens(text: str) -> int:
    # Cheap heuristic: ~4 chars per token.
    return max(1, len(text) // 4)


def _direct_max_tokens(cfg: RunConfig) -> int:
    """``cfg.direct_max_tokens`` after applying the default for unset values."""
    return cfg.direct_max_tokens if cfg.direct_max_tokens is not None else DEFAULT_DIRECT_MAX_TOKENS


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
    files = [f for f in snapshot.files if f.safety.safe]
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
        instructions="" if cfg.no_instructions else _load_instructions(snapshot.repo_root),
        hunk_inventory=hunk_inventory,
    )


def chunk_hunks(
    snapshot: WorktreeSnapshot, max_chunk_tokens: int, graph: ChangeGraph | None = None,
) -> list[dict[str, Any]]:
    """Pack related changed regions into bounded evidence requests."""
    chunks: list[dict[str, Any]] = []
    by_id = {
        h.hunk_id: h for file_change in snapshot.files if file_change.safety.safe
        for h in file_change.hunks
    }
    if graph is None:
        ordered_ids = list(by_id)
    else:
        ordered_ids = [unit_id for group in connected_groups(graph) for unit_id in group]

    diff_parts: list[str] = []
    hunk_ids: list[str] = []
    file_paths: list[str] = []
    tokens = 0

    def flush(warnings: list[str] | None = None) -> None:
        nonlocal tokens
        if not hunk_ids:
            return
        selected = set(hunk_ids)
        facts = {}
        if graph is not None:
            facts = {
                "units": [
                    unit.model_dump() for unit in graph.units if unit.unit_id in selected
                ],
                "links": [
                    link.model_dump() for link in graph.links
                    if link.source in selected and link.target in selected
                ],
            }
        chunks.append({
            "chunk_id": f"chunk-{len(chunks)}",
            "diff": "".join(diff_parts),
            "hunk_ids": list(hunk_ids),
            "file_paths": list(dict.fromkeys(file_paths)),
            "tokens": tokens,
            "warnings": warnings or [],
            "change_facts": facts,
        })
        diff_parts.clear()
        hunk_ids.clear()
        file_paths.clear()
        tokens = 0

    for hunk_id in ordered_ids:
        hunk = by_id[hunk_id]
        block = f"--- {hunk.file_path}\n{hunk.patch}\n"
        block_tokens = _estimate_tokens(block)
        if hunk_ids and tokens + block_tokens > max_chunk_tokens:
            flush()
        diff_parts.append(block)
        hunk_ids.append(hunk.hunk_id)
        file_paths.append(hunk.file_path)
        tokens += block_tokens
        if block_tokens > max_chunk_tokens:
            flush([
                f"{hunk.hunk_id} is about {block_tokens} tokens, above the "
                f"{max_chunk_tokens}-token evidence limit"
            ])
    flush()
    return chunks


# ---------------------------------------------------------------------------
# Chunk-review cache
# ---------------------------------------------------------------------------
# Cache key: (chunk_id, sha256 of the chunk's hunk fingerprints). Stored as
# JSON in the session dir so only changed chunks re-run after a plan-validation
# failure or resume. Cache logic stays in planner.py; the session dir path is
# passed in read-only from the caller (..session.SessionStore).


def _chunk_fingerprint(snapshot: WorktreeSnapshot, chunk: dict[str, Any]) -> str:
    """Stable hash of the hunks a chunk covers, ignoring chunk_id ordering."""
    fp_by_id = {h.hunk_id: h.fingerprint for f in snapshot.files for h in f.hunks}
    parts = [f"{hid}:{fp_by_id.get(hid, '')}" for hid in chunk["hunk_ids"]]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _cache_path(session_dir: Path | None, chunk_id: str) -> Path | None:
    if session_dir is None:
        return None
    # Sanitize chunk_id (e.g. "chunk-3") into a safe filename.
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in chunk_id)
    return session_dir / "cache" / f"{safe}.json"


def _load_cached_review(session_dir: Path | None, chunk: dict[str, Any],
                        snapshot: WorktreeSnapshot) -> ChunkReview | None:
    path = _cache_path(session_dir, chunk["chunk_id"])
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("fingerprint") != _chunk_fingerprint(snapshot, chunk):
        return None
    try:
        review = ChunkReview.model_validate(data["review"])
    except (ValidationError, KeyError):
        return None
    # Ensure chunk_id matches the current chunk (cache is keyed on it, but be safe).
    review.chunk_id = chunk["chunk_id"]
    review.hunk_ids = list(chunk["hunk_ids"])
    return review


def _store_cached_review(session_dir: Path | None, chunk: dict[str, Any],
                         snapshot: WorktreeSnapshot, review: ChunkReview) -> None:
    path = _cache_path(session_dir, chunk["chunk_id"])
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "chunk_id": chunk["chunk_id"],
                    "fingerprint": _chunk_fingerprint(snapshot, chunk),
                    "review": review.model_dump(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        # Cache is best-effort; never fail a plan because the cache could not write.
        pass


def run_map(
    provider: AIProvider, context: ContextPack, chunks: list[dict[str, Any]], cfg: RunConfig,
    show_progress: bool = True,
    *,
    max_parallel: int | None = None,
    snapshot: WorktreeSnapshot | None = None,
    session_dir: Path | None = None,
) -> list[ChunkReview]:
    """Run the map phase across chunks in parallel.

    Each chunk is an independent LLM call, so we dispatch them concurrently
    via a thread pool (urllib releases the GIL on I/O, so threads give real
    parallelism here). This turns a 15-hunk/1-chunk/90s serial wait into
    several small parallel calls whose total wall time ≈ the slowest one.

    ``max_parallel`` caps the thread pool size. When None it defaults
    to ``min(total, 8)`` preserving the prior behaviour. A ``snapshot`` +
    ``session_dir`` pair enables the chunk-review cache: only chunks
    whose hunk fingerprints changed re-call the provider.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Each evidence request already names its hunks. Repeating the repository's
    # complete hunk inventory in every request wastes most of the context.
    ctx = context.model_dump(exclude={"hunk_inventory"})
    total = len(chunks)

    def _analyze(chunk: dict[str, Any]) -> ChunkReview:
        cached = _load_cached_review(session_dir, chunk, snapshot) if snapshot is not None else None
        if cached is not None:
            return cached
        raw = provider.complete_json(
            system=prompts.MAP_SYSTEM,
            user=prompts.map_user(
                ctx, chunk["chunk_id"], chunk["diff"], chunk["hunk_ids"],
                chunk.get("change_facts"),
            ),
            schema_name="ChunkReview",
            max_tokens=min(
                cfg.max_chunk_tokens,
                max(1024, 512 + len(chunk["hunk_ids"]) * 96),
            ),
            temperature=cfg.temperature,
            timeout=cfg.provider_timeout,
            attempts=cfg.retry_attempts,
        )
        raw.setdefault("chunk_id", chunk["chunk_id"])
        raw.setdefault("hunk_ids", chunk["hunk_ids"])
        try:
            review = ChunkReview.model_validate(raw)
        except ValidationError as exc:
            raise InvalidAIResponseError(
                f"invalid map review for {chunk['chunk_id']}: {exc}"
            ) from exc
        review.hunk_ids = list(chunk["hunk_ids"])
        if snapshot is not None:
            _store_cached_review(session_dir, chunk, snapshot, review)
        return review

    if total <= 1:
        # Single chunk: no point spinning up a pool.
        label = output.format_files(
            chunks[0]["file_paths"], verbose=(cfg.mode == "verbose")
        ) if chunks else "no files"
        with output.step(f"Analyzing chunk 1/1 ({label})...", enabled=show_progress):
            return [_analyze(chunks[0])] if chunks else []

    if max_parallel is None:
        max_parallel = min(total, 8)
    else:
        max_parallel = max(1, min(max_parallel, total))
    reviews: list[ChunkReview | None] = [None] * total
    with output.step(f"Analyzing {total} chunks in parallel (max {max_parallel} workers)...",
                     enabled=show_progress):
        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            future_to_idx = {pool.submit(_analyze, chunk): i for i, chunk in enumerate(chunks)}
            done = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                reviews[idx] = future.result()
                done += 1
                if show_progress:
                    output.err_console.print(
                        f"[dim]  chunk {idx + 1}/{total} done ({done}/{total} complete)[/dim]"
                    )
    return [r for r in reviews if r is not None]


# ---------------------------------------------------------------------------
# Bounded lead reduction
# ---------------------------------------------------------------------------
# When the chunk-review set is too large to fit in a single reduce prompt we
# reduce in batches, then reduce the reductions, always producing a CommitPlan.
# Single-pass when it fits (backward-compatible).


def _reduce_once(
    provider: AIProvider,
    ctx: dict[str, Any],
    reviews: list[dict[str, Any]],
    hunk_inventory: list[dict[str, Any]],
    cfg: RunConfig,
    mode: str,
    repo_fingerprint: str,
    base_head: str,
    show_progress: bool,
    label: str = "Synthesizing commit plan...",
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """One reducer LLM call. Returns the raw parsed JSON dict."""
    user = prompts.reduce_user(ctx, reviews, hunk_inventory, mode, repo_fingerprint, base_head)
    with output.step(label, enabled=show_progress):
        return provider.complete_json(
            system=prompts.REDUCE_SYSTEM,
            user=user,
            schema_name="CommitPlan",
            max_tokens=max_tokens or cfg.max_reducer_tokens,
            temperature=cfg.temperature,
            timeout=cfg.provider_timeout,
            attempts=cfg.retry_attempts,
        )


def _estimate_reduce_tokens(
    ctx: dict[str, Any],
    reviews: list[dict[str, Any]],
    hunk_inventory: list[dict[str, Any]],
    mode: str,
    repo_fingerprint: str,
    base_head: str,
) -> int:
    user = prompts.reduce_user(ctx, reviews, hunk_inventory, mode, repo_fingerprint, base_head)
    # Add overhead for the system prompt + JSON framing.
    return _estimate_tokens(prompts.REDUCE_SYSTEM) + _estimate_tokens(user) + 512


def run_reduce(
    provider: AIProvider,
    context: ContextPack,
    reviews: list[ChunkReview],
    snapshot: WorktreeSnapshot,
    graph: ChangeGraph,
    cfg: RunConfig,
    show_progress: bool = True,
    session_dir: Path | None = None,
) -> CommitPlan:
    hunk_inventory = context.hunk_inventory
    ctx = context.model_dump()
    review_dicts = [r.model_dump() for r in reviews]
    mode = cfg.mode
    repo_fingerprint = snapshot.fingerprint
    base_head = snapshot.head_sha

    def parse(raw: dict[str, Any]) -> CommitPlan:
        raw.setdefault("repo_fingerprint", repo_fingerprint)
        raw.setdefault("base_head", base_head)
        raw.setdefault("mode", mode)
        try:
            return CommitPlan.model_validate(raw)
        except ValidationError as exc:
            raise InvalidAIResponseError(f"invalid commit plan: {exc}") from exc

    estimated = _estimate_reduce_tokens(
        ctx, review_dicts, hunk_inventory, mode, repo_fingerprint, base_head,
    )
    if estimated <= _LEAD_INPUT_TOKENS:
        return parse(_reduce_once(
            provider, ctx, review_dicts, hunk_inventory, cfg, mode,
            repo_fingerprint, base_head, show_progress,
            label=f"Planning from {len(review_dicts)} evidence note(s)...",
        ))

    inventory_by_id = {item["hunk_id"]: item for item in hunk_inventory}
    batches: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    batch_reviews: list[dict[str, Any]] = []
    batch_inventory: list[dict[str, Any]] = []
    for review in review_dicts:
        review_inventory = [
            inventory_by_id[hunk_id]
            for hunk_id in review.get("hunk_ids", [])
            if hunk_id in inventory_by_id
        ]
        candidate_reviews = [*batch_reviews, review]
        candidate_inventory = [*batch_inventory, *review_inventory]
        candidate_tokens = _estimate_reduce_tokens(
            ctx, candidate_reviews, candidate_inventory, mode, repo_fingerprint, base_head,
        )
        if batch_reviews and candidate_tokens > _LEAD_INPUT_TOKENS:
            batches.append((batch_reviews, batch_inventory))
            batch_reviews = [review]
            batch_inventory = review_inventory
        else:
            batch_reviews = candidate_reviews
            batch_inventory = candidate_inventory
    if batch_reviews:
        batches.append((batch_reviews, batch_inventory))

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def can_split_after(error: Exception) -> bool:
        if isinstance(error, InvalidAIResponseError):
            return True
        message = str(error).lower()
        return any(
            text in message
            for text in (
                "empty content",
                "context budget",
                "context length",
                "invalid json",
                "output was truncated",
            )
        )

    def combine_parts(parts: list[CommitPlan]) -> CommitPlan:
        combined = CommitPlan(
            mode=mode,
            repo_fingerprint=repo_fingerprint,
            base_head=base_head,
        )
        for index, part in enumerate(parts, start=1):
            prefix = f"p{index}-"
            id_map = {group.group_id: prefix + group.group_id for group in part.groups}
            for group in part.groups:
                copied = group.model_copy(deep=True)
                copied.group_id = id_map[group.group_id]
                copied.depends_on = [
                    id_map[dependency]
                    for dependency in group.depends_on
                    if dependency in id_map
                ]
                combined.groups.append(copied)
            combined.warnings.extend(part.warnings)
        return combined

    def reviews_for_hunks(
        source_reviews: list[dict[str, Any]], wanted_ids: set[str],
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for review in source_reviews:
            hunk_ids = [
                hunk_id for hunk_id in review.get("hunk_ids", [])
                if hunk_id in wanted_ids
            ]
            if not hunk_ids:
                continue
            copied = dict(review)
            copied["hunk_ids"] = hunk_ids
            message_terms = copied.get("message_terms")
            if isinstance(message_terms, dict):
                copied["message_terms"] = {
                    hunk_id: terms
                    for hunk_id, terms in message_terms.items()
                    if hunk_id in wanted_ids
                }
            selected.append(copied)
        return selected

    def split_part(
        part_reviews: list[dict[str, Any]],
        part_hunks: list[dict[str, Any]],
    ) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
        if len(part_reviews) > 1:
            middle = len(part_reviews) // 2
            review_halves = (part_reviews[:middle], part_reviews[middle:])
            result = []
            for review_half in review_halves:
                wanted_ids = {
                    hunk_id
                    for review in review_half
                    for hunk_id in review.get("hunk_ids", [])
                }
                result.append((
                    review_half,
                    [hunk for hunk in part_hunks if hunk["hunk_id"] in wanted_ids],
                ))
            return result
        if len(part_hunks) > 1:
            middle = len(part_hunks) // 2
            hunk_halves = (part_hunks[:middle], part_hunks[middle:])
            return [
                (
                    reviews_for_hunks(
                        part_reviews, {hunk["hunk_id"] for hunk in hunk_half},
                    ),
                    hunk_half,
                )
                for hunk_half in hunk_halves
            ]
        return []

    def plan_batch(index: int) -> CommitPlan:
        batch_review_dicts, batch_hunks = batches[index]

        def plan_part(
            part_reviews: list[dict[str, Any]],
            part_hunks: list[dict[str, Any]],
        ) -> CommitPlan:
            wanted_ids = {hunk["hunk_id"] for hunk in part_hunks}
            cache_path: Path | None = None
            if session_dir is not None:
                cache_payload = json.dumps(
                    {
                        "version": "bounded-v1",
                        "model": cfg.model,
                        "mode": mode,
                        "base_head": base_head,
                        "reviews": part_reviews,
                        "hunks": part_hunks,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                )
                cache_key = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()
                cache_path = (
                    session_dir / "cache" / "plans" / "batches" / f"{cache_key}.json"
                )
                cached = _load_plan_cache(cache_path)
                if cached is not None:
                    assigned_ids = {
                        hunk_id for group in cached.groups for hunk_id in group.hunk_ids
                    }
                    missing_ids = wanted_ids - assigned_ids
                    if not missing_ids:
                        return cached
                    if missing_ids != wanted_ids:
                        missing_hunks = [
                            hunk for hunk in part_hunks if hunk["hunk_id"] in missing_ids
                        ]
                        completed = combine_parts([
                            cached,
                            plan_part(
                                reviews_for_hunks(part_reviews, missing_ids), missing_hunks,
                            ),
                        ])
                        _store_plan_cache(cache_path, completed)
                        return completed

            output_tokens = min(
                cfg.max_reducer_tokens,
                max(4096, 512 + len(part_hunks) * 160),
            )
            part_paths = {hunk.get("file_path") for hunk in part_hunks}
            part_context = dict(ctx)
            part_context["file_list"] = [
                item for item in ctx.get("file_list", [])
                if item.get("path", item.get("file_path")) in part_paths
            ]
            part_context["diffstat"] = ""
            part_context["planning_scope"] = (
                "This is one complete bounded slice of a larger change. Plan every hunk "
                "listed below. Other repository files are handled by separate requests."
            )
            try:
                planned = parse(_reduce_once(
                    provider, part_context, part_reviews, part_hunks, cfg, mode,
                    repo_fingerprint, base_head, False,
                    max_tokens=output_tokens,
                ))
            except (ProviderError, InvalidAIResponseError) as error:
                if not can_split_after(error):
                    raise
                smaller_parts = split_part(part_reviews, part_hunks)
                if not smaller_parts:
                    return CommitPlan(
                        mode=mode,
                        repo_fingerprint=repo_fingerprint,
                        base_head=base_head,
                        warnings=[
                            "One small planning request failed; ATC repaired its regions locally."
                        ],
                    )
                planned = combine_parts([
                    plan_part(smaller_reviews, smaller_hunks)
                    for smaller_reviews, smaller_hunks in smaller_parts
                ])

            assigned_ids = {
                hunk_id for group in planned.groups for hunk_id in group.hunk_ids
            }
            missing_ids = wanted_ids - assigned_ids
            if missing_ids:
                missing_hunks = [
                    hunk for hunk in part_hunks if hunk["hunk_id"] in missing_ids
                ]
                if missing_ids == wanted_ids:
                    smaller_parts = split_part(part_reviews, part_hunks)
                    if smaller_parts:
                        planned = combine_parts([
                            plan_part(smaller_reviews, smaller_hunks)
                            for smaller_reviews, smaller_hunks in smaller_parts
                        ])
                else:
                    planned = combine_parts([
                        planned,
                        plan_part(
                            reviews_for_hunks(part_reviews, missing_ids), missing_hunks,
                        ),
                    ])

            if cache_path is not None:
                _store_plan_cache(cache_path, planned)
            return planned

        return plan_part(batch_review_dicts, batch_hunks)

    total = len(batches)
    workers = min(total, cfg.max_parallel or 8)
    planned_batches: list[CommitPlan | None] = [None] * total
    output.note(
        f"Lead plan: {total} bounded batch(es), up to {workers} in parallel",
        enabled=show_progress,
        style="cyan",
    )
    with output.step(f"Planning {total} bounded batches...", enabled=show_progress):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_index = {pool.submit(plan_batch, index): index for index in range(total)}
            done = 0
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                planned_batches[index] = future.result()
                done += 1
                if show_progress:
                    output.err_console.print(
                        f"[dim]  plan batch {index + 1}/{total} done "
                        f"({done}/{total} complete)[/dim]"
                    )

    combined = CommitPlan(
        mode=mode,
        repo_fingerprint=repo_fingerprint,
        base_head=base_head,
        warnings=[_BOUNDED_PLAN_WARNING],
    )
    for index, batch_plan in enumerate(planned_batches, start=1):
        if batch_plan is None:
            continue
        prefix = f"b{index}-"
        id_map = {group.group_id: prefix + group.group_id for group in batch_plan.groups}
        for group in batch_plan.groups:
            copied = group.model_copy(deep=True)
            copied.group_id = id_map[group.group_id]
            copied.depends_on = [
                id_map[dependency]
                for dependency in group.depends_on
                if dependency in id_map
            ]
            combined.groups.append(copied)
        combined.warnings.extend(
            warning for warning in batch_plan.warnings
            if not (
                any(
                    source in warning.lower()
                    for source in (
                        "chunk_reviews",
                        "chunk reviews",
                        "chunk analysis",
                        "hunk_inventory",
                        "hunk inventory",
                        "file_list",
                        "file list",
                        "diffstat",
                        "provided hunks",
                        "in hunks",
                        "in chunks",
                    )
                )
                and any(
                    claim in warning.lower()
                    for claim in (
                        "not included",
                        "not covered",
                        "not represented",
                        "not present",
                        "not provided",
                        "only",
                    )
                )
            )
        )
    combined.warnings = list(dict.fromkeys(combined.warnings))
    return _repair_plan_locally(combined, snapshot, graph, review_dicts)


# ---------------------------------------------------------------------------
# Commit-message template
# ---------------------------------------------------------------------------
# If cfg.message_template is set (CONTRACT from the config agent), fill the
# ${scope}, ${verb}, ${object} placeholders from the AI-produced message and
# keep the default message when the template is None.


def _apply_message_template(message: str, template: str | None) -> str:
    """Fill ${scope}, ${verb}, ${object} from a ``scope: verb object`` message.

    Returns ``message`` unchanged when ``template`` is None or the message
    does not match the ``scope: verb object`` shape.
    """
    if not template:
        return message
    stripped = message.strip()
    subject = stripped.splitlines()[0] if stripped else ""
    # scope is everything before the first ": "; verb is the first token after,
    # object is the remainder.
    if ": " not in subject:
        return message
    scope, rest = subject.split(": ", 1)
    tokens = rest.split()
    verb = tokens[0] if tokens else ""
    obj = " ".join(tokens[1:]) if len(tokens) > 1 else ""
    filled = (
        template.replace("${scope}", scope)
        .replace("${verb}", verb)
        .replace("${object}", obj)
    )
    # Preserve any body lines after the subject (slice the stripped message so
    # the offset matches the subject's length).
    body = stripped[len(subject) :].lstrip()
    return filled if not body else f"{filled}\n\n{body}"


def _apply_template_to_plan(plan: CommitPlan, template: str | None) -> None:
    if not template:
        return
    for group in plan.groups:
        group.message = _apply_message_template(group.message, template)


_PLANNER_CACHE_VERSION = "adaptive-v8"


class _TimeBudget:
    def __init__(self, seconds: float) -> None:
        self.started = time.monotonic()
        self.seconds = max(float(seconds), 1.0)

    def remaining(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self.started))

    def require(self, phase: str) -> float:
        remaining = self.remaining()
        if remaining < 1.0:
            raise ProviderError(
                f"planning time ended before {phase}",
                hint="Run `atc commit` again; completed analysis is cached.",
            )
        return remaining


def _full_diff(snapshot: WorktreeSnapshot) -> str:
    parts: list[str] = []
    for file_change in snapshot.files:
        if not file_change.safety.safe:
            continue
        for hunk in file_change.hunks:
            parts.append(f"### {hunk.hunk_id}\n--- {hunk.file_path}\n{hunk.patch}\n")
    return "".join(parts)


def _planner_cache_path(
    git: GitClient, snapshot: WorktreeSnapshot, cfg: RunConfig, kind: str, extra: str = "",
) -> Path:
    from .session import SessionStore

    payload = "\n".join(
        [
            _PLANNER_CACHE_VERSION,
            kind,
            snapshot.fingerprint,
            cfg.model or "",
            cfg.mode,
            str(cfg.max_reducer_tokens),
            str(cfg.review_plan),
            extra,
        ]
    )
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return SessionStore(git).sessions_dir / "cache" / "plans" / f"{key}.json"


def _load_plan_cache(path: Path) -> CommitPlan | None:
    if not path.is_file():
        return None
    try:
        return CommitPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def _store_plan_cache(path: Path, plan: CommitPlan) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    except OSError:
        pass


def _parse_plan(raw: dict[str, Any], snapshot: WorktreeSnapshot, cfg: RunConfig) -> CommitPlan:
    raw.setdefault("repo_fingerprint", snapshot.fingerprint)
    raw.setdefault("base_head", snapshot.head_sha)
    raw.setdefault("mode", cfg.mode)
    try:
        return CommitPlan.model_validate(raw)
    except ValidationError as exc:
        raise InvalidAIResponseError(f"invalid commit plan: {exc}") from exc


def _request_plan(
    provider: AIProvider, *, system: str, user: str, cfg: RunConfig,
    snapshot: WorktreeSnapshot, budget: _TimeBudget, label: str, show_progress: bool,
    expected_groups: int | None = None,
) -> CommitPlan:
    timeout = budget.require(label)
    input_tokens = _estimate_tokens(system) + _estimate_tokens(user)
    region_count = sum(
        len(file_change.hunks)
        for file_change in snapshot.files
        if file_change.safety.safe
    )
    output_items = expected_groups if expected_groups is not None else region_count
    output_tokens = min(cfg.max_reducer_tokens, max(4096, 512 + output_items * 160))
    output.note(
        f"{label}: about {input_tokens:,} input tokens, up to {output_tokens:,} output, "
        f"{int(timeout)}s left",
        enabled=show_progress,
    )
    with output.step(f"{label}...", enabled=show_progress):
        raw = provider.complete_json(
            system=system,
            user=user,
            schema_name="CommitPlan",
            max_tokens=output_tokens,
            temperature=cfg.temperature,
            timeout=timeout,
            attempts=cfg.retry_attempts,
        )
    output.note("Model output decoded; validating the plan", enabled=show_progress)
    return _parse_plan(raw, snapshot, cfg)


def _validate(plan: CommitPlan, snapshot: WorktreeSnapshot, cfg: RunConfig) -> None:
    files = [file_change for file_change in snapshot.files if file_change.safety.safe]
    safe_ids = [hunk.hunk_id for file_change in files for hunk in file_change.hunks]
    hunk_to_path = {
        hunk.hunk_id: hunk.file_path for file_change in files for hunk in file_change.hunks
    }
    errors = validate_plan(
        plan,
        safe_hunk_ids=safe_ids,
        hunk_to_path=hunk_to_path,
        mode=cfg.mode,
        indivisible_hunks_by_path={
            file_change.path: {hunk.hunk_id for hunk in file_change.hunks}
            for file_change in files
            if file_change.status in {"deleted", "renamed", "mode"}
        },
    )
    if errors:
        raise PlanValidationError("plan validation failed:\n  " + "\n  ".join(errors))


def _repair_plan_locally(
    plan: CommitPlan, snapshot: WorktreeSnapshot, graph: ChangeGraph, evidence: Any,
) -> CommitPlan:
    """Guarantee structural coverage after the model's bounded repair fails."""
    repaired = plan.model_copy(deep=True)
    ordered_hunks = [
        hunk.hunk_id
        for file_change in snapshot.files if file_change.safety.safe
        for hunk in file_change.hunks
    ]
    hunk_paths = {
        hunk.hunk_id: hunk.file_path
        for file_change in snapshot.files if file_change.safety.safe
        for hunk in file_change.hunks
    }
    indivisible_by_hunk: dict[str, list[str]] = {}
    for file_change in snapshot.files:
        if file_change.safety.safe and file_change.status in {"deleted", "renamed", "mode"}:
            file_hunks = [hunk.hunk_id for hunk in file_change.hunks]
            indivisible_by_hunk.update({hunk_id: file_hunks for hunk_id in file_hunks})
    units = {unit.unit_id: unit for unit in graph.units}
    terms: dict[str, list[str]] = {}
    if isinstance(evidence, list):
        for review in evidence:
            if isinstance(review, dict) and isinstance(review.get("message_terms"), dict):
                terms.update(review["message_terms"])

    def scope_for(path: str) -> str:
        stem = Path(path).stem.lower()
        return "".join(char if char.isalnum() else "-" for char in stem).strip("-") or "core"

    def message_for(hunk_id: str) -> str:
        unit = units.get(hunk_id)
        path = hunk_paths[hunk_id]
        scope = scope_for(path)
        kind = unit.change_kind if unit is not None else "code"
        commit_type, verb = {
            "test": ("test", "cover"),
            "docs": ("docs", "document"),
            "config": ("chore", "configure"),
            "dependency": ("build", "align"),
            "migration": ("feat", "migrate"),
        }.get(kind, ("refactor", "refine"))
        raw_object = " ".join(terms.get(hunk_id, [])[:2])
        if not raw_object and unit is not None:
            raw_object = unit.symbol
        object_text = " ".join(raw_object.split()) or scope
        return f"{commit_type}({scope}): {verb} {object_text} behavior"

    groups: list[CommitGroup] = []
    assigned: set[str] = set()
    group_ids: set[str] = set()
    for index, group in enumerate(repaired.groups, start=1):
        requested = [
            related
            for hunk_id in group.hunk_ids
            for related in indivisible_by_hunk.get(hunk_id, [hunk_id])
        ]
        hunk_ids = list(dict.fromkeys(
            hunk_id for hunk_id in requested
            if hunk_id in hunk_paths and hunk_id not in assigned
        ))
        if not hunk_ids:
            continue
        assigned.update(hunk_ids)
        group.hunk_ids = hunk_ids
        group.file_paths = list(dict.fromkeys(hunk_paths[hunk_id] for hunk_id in hunk_ids))
        if group.group_id in group_ids:
            group.group_id = f"{group.group_id}-{index}"
        group_ids.add(group.group_id)
        if validate_commit_message(group.message):
            group.message = message_for(hunk_ids[0])
        if len(hunk_ids) > 1 and not group.rationale.strip():
            behavior = group.message.split(": ", 1)[-1].rstrip(".")
            group.rationale = f"These regions jointly {behavior}."
        if not group.unsplittable_reason.strip():
            group.unsplittable_reason = (
                "Splitting these regions would separate parts of the same behavior."
                if len(hunk_ids) > 1
                else "This commit contains one changed region."
            )
        groups.append(group)

    for index, hunk_id in enumerate(ordered_hunks, start=1):
        if hunk_id in assigned:
            continue
        group_id = f"local-{index}"
        while group_id in group_ids:
            group_id += "-x"
        group_ids.add(group_id)
        groups.append(CommitGroup(
            group_id=group_id,
            message=message_for(hunk_id),
            rationale="The model omitted this independent changed region.",
            hunk_ids=[hunk_id],
            file_paths=[hunk_paths[hunk_id]],
            risk="medium",
            unsplittable_reason="This commit contains one changed region.",
        ))

    known = {group.group_id for group in groups}
    for group in groups:
        group.depends_on = list(dict.fromkeys(
            dependency for dependency in group.depends_on
            if dependency in known and dependency != group.group_id
        ))
    ordered: list[CommitGroup] = []
    emitted: set[str] = set()
    remaining = list(groups)
    while remaining:
        ready = next(
            (group for group in remaining if set(group.depends_on) <= emitted), None
        )
        if ready is None:
            ready = remaining[0]
            ready.depends_on = []
        remaining.remove(ready)
        ordered.append(ready)
        emitted.add(ready.group_id)
    repaired.groups = ordered
    for excluded in repaired.excluded:
        if not excluded.reason.strip():
            excluded.reason = "Excluded by repository safety rules."
    repaired.warnings.append("ATC repaired model coverage and ordering locally.")
    return repaired


def ensure_applicable_plan(
    commit_plan: CommitPlan, snapshot: WorktreeSnapshot, cfg: RunConfig,
) -> CommitPlan:
    """Return a structurally valid plan, repairing saved/legacy plans locally."""
    try:
        _validate(commit_plan, snapshot, cfg)
        return commit_plan
    except PlanValidationError as exc:
        if "whole-file change" not in str(exc):
            return commit_plan
        repaired = _repair_plan_locally(
            commit_plan, snapshot, build_change_graph(snapshot), [],
        )
        _validate(repaired, snapshot, cfg)
        return repaired


def plan(
    provider: AIProvider, git: GitClient, snapshot: WorktreeSnapshot, cfg: RunConfig,
    show_progress: bool = True,
) -> CommitPlan:
    """Plan the finest useful commits with a direct or evidence-assisted path."""
    budget = _TimeBudget(cfg.deadline if cfg.deadline is not None else DEFAULT_DEADLINE)
    with output.step("Reading repository context...", enabled=show_progress):
        context = build_context_pack(git, snapshot, cfg)
        graph = build_change_graph(snapshot)
        diff = _full_diff(snapshot)
    safe_files = [f for f in snapshot.files if f.safety.safe]
    safe_hunks = sum(len(f.hunks) for f in safe_files)
    graph_dict = graph.model_dump()
    direct_user = prompts.direct_user(
        context.model_dump(), graph_dict, diff, cfg.mode, snapshot.fingerprint, snapshot.head_sha
    )
    direct_tokens = _estimate_tokens(prompts.DIRECT_SYSTEM) + _estimate_tokens(direct_user)
    output.note(
        f"Snapshot: {len(safe_files)} safe files, {safe_hunks} changed regions, "
        f"{len(graph.links)} local links",
        enabled=show_progress,
    )

    evidence: Any = diff
    initial_cache = _planner_cache_path(git, snapshot, cfg, "initial")
    commit_plan = _load_plan_cache(initial_cache)
    if commit_plan is not None:
        try:
            _validate(commit_plan, snapshot, cfg)
        except PlanValidationError:
            output.note(
                "Cached plan was incomplete; rebuilding it from saved analysis",
                enabled=show_progress,
                style="yellow",
            )
            commit_plan = None
    if commit_plan is not None:
        if direct_tokens > _direct_max_tokens(cfg):
            evidence = []
        output.note("Initial plan: reused cached result", enabled=show_progress)
    elif direct_tokens <= _direct_max_tokens(cfg):
        output.note(
            f"Plan method: one full-change request ({direct_tokens:,} estimated tokens)",
            enabled=show_progress,
            style="cyan",
        )
        commit_plan = _request_plan(
            provider,
            system=prompts.DIRECT_SYSTEM,
            user=direct_user,
            cfg=cfg,
            snapshot=snapshot,
            budget=budget,
            label="Finding commit groups",
            show_progress=show_progress,
        )
        _store_plan_cache(initial_cache, commit_plan)
    else:
        # Smaller evidence prompts return much faster on large repositories.
        # The CLI value remains an advanced ceiling, never a requirement for
        # users to know the provider's context window.
        chunks = chunk_hunks(snapshot, min(cfg.max_chunk_tokens, _EVIDENCE_INPUT_TOKENS), graph)
        output.note(
            f"Plan method: {len(chunks)} parallel evidence request(s), then commit planning",
            enabled=show_progress,
            style="cyan",
        )
        from .session import SessionStore

        evidence_cfg = replace(
            cfg,
            provider_timeout=budget.require("evidence analysis"),
            max_chunk_tokens=min(cfg.max_chunk_tokens, 4000),
        )
        session_dir = SessionStore(git).sessions_dir
        reviews = run_map(
            provider,
            context,
            chunks,
            evidence_cfg,
            show_progress=show_progress,
            max_parallel=cfg.max_parallel,
            snapshot=snapshot,
            session_dir=session_dir,
        )
        lead_cfg = replace(cfg, provider_timeout=budget.require("lead planning"))
        commit_plan = run_reduce(
            provider, context, reviews, snapshot, graph, lead_cfg,
            show_progress=show_progress, session_dir=session_dir,
        )
        evidence = [review.model_dump() for review in reviews]
        _store_plan_cache(initial_cache, commit_plan)

    if cfg.review_plan and _BOUNDED_PLAN_WARNING not in commit_plan.warnings:
        review_key = hashlib.sha256(commit_plan.model_dump_json().encode("utf-8")).hexdigest()
        review_cache = _planner_cache_path(git, snapshot, cfg, "review", review_key)
        reviewed = _load_plan_cache(review_cache)
        if reviewed is not None:
            output.note("Split review: reused cached result", enabled=show_progress)
        else:
            path_by_unit = {unit.unit_id: unit.file_path for unit in graph.units}
            file_links = sorted({
                (path_by_unit[link.source], path_by_unit[link.target], link.kind)
                for link in graph.links
                if path_by_unit[link.source] != path_by_unit[link.target]
            })
            review_graph = {
                "links": [
                    {"source_file": source, "target_file": target, "kind": kind}
                    for source, target, kind in file_links
                ],
            }
            try:
                _validate(commit_plan, snapshot, cfg)
            except PlanValidationError as lead_error:
                repair_payload = json.loads(prompts.review_user(
                    context.model_dump(), review_graph, commit_plan.model_dump(), evidence,
                    cfg.mode, snapshot.fingerprint, snapshot.head_sha,
                ))
                repair_payload["task"] = (
                    "Repair every listed validation error and return one complete valid plan."
                )
                repair_payload["validation_errors"] = lead_error.message
                reviewed = _request_plan(
                    provider,
                    system=prompts.REVIEW_SYSTEM,
                    user=json.dumps(repair_payload, ensure_ascii=False),
                    cfg=cfg,
                    snapshot=snapshot,
                    budget=budget,
                    label="Repairing invalid lead plan",
                    show_progress=show_progress,
                    expected_groups=len(commit_plan.groups) * 2,
                )
                try:
                    _validate(reviewed, snapshot, cfg)
                except PlanValidationError as repair_error:
                    reviewed = _repair_plan_locally(reviewed, snapshot, graph, evidence)
                    try:
                        _validate(reviewed, snapshot, cfg)
                    except PlanValidationError as local_error:
                        raise local_error from repair_error
            else:
                reviewed = _request_plan(
                    provider,
                    system=prompts.REVIEW_SYSTEM,
                    user=prompts.review_user(
                        context.model_dump(), review_graph, commit_plan.model_dump(), evidence,
                        cfg.mode, snapshot.fingerprint, snapshot.head_sha,
                    ),
                    cfg=cfg,
                    snapshot=snapshot,
                    budget=budget,
                    label=(
                        f"Checking whether {len(commit_plan.groups)} group(s) can be split further"
                    ),
                    show_progress=show_progress,
                    expected_groups=len(commit_plan.groups) * 2,
                )
                try:
                    _validate(reviewed, snapshot, cfg)
                except PlanValidationError:
                    output.note(
                        "Split review returned an invalid plan; keeping the valid lead plan",
                        enabled=show_progress,
                        style="yellow",
                    )
                    reviewed = commit_plan
            _store_plan_cache(review_cache, reviewed)
        commit_plan = reviewed
    elif cfg.review_plan:
        output.note(
            "Final review: completed inside bounded lead batches",
            enabled=show_progress,
            style="green",
        )

    _apply_template_to_plan(commit_plan, cfg.message_template)
    with output.step("Checking plan coverage and order...", enabled=show_progress):
        _validate(commit_plan, snapshot, cfg)
    _store_plan_cache(initial_cache, commit_plan)
    output.note(
        f"Final plan: {len(commit_plan.groups)} meaningful commit(s), "
        f"{safe_hunks}/{safe_hunks} changed regions assigned",
        enabled=show_progress,
        style="green",
    )
    return commit_plan
