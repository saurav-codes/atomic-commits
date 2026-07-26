"""Prompt templates for direct, evidence, lead, and review requests.

Prompts make the AI's job explicit: it is planning Git commits, not editing
code; it must use only provided diffs; every safe hunk is assigned exactly
once; generic messages are invalid.
"""

from __future__ import annotations

import json
from typing import Any

MAP_SYSTEM = """You investigate part of a larger code change for a Git commit planner.
Rules:
- Use ONLY the provided diffs. Do not invent files, hunks, or behavior.
- Identify the likely intent of every changed region.
- Record exact hunk IDs, related tests/docs, dependencies, and unrelated changes.
- These are evidence notes, not the final commit groups.
- Flag any generated or runtime content the filters may have missed.
- Keep the summary under 80 words and every other string under 30 words.
- Use at most 3 message terms per hunk. Do not repeat diff text.
- Use clear, simple English.
Respond as a single JSON object matching the ChunkReview schema.
"""

REDUCE_SYSTEM = """You are the lead Git commit planner.
You receive global repository facts and evidence notes from large change regions.
Rules:
- Assign every safe hunk to exactly one commit group. Never duplicate a hunk.
- Keep unsafe/excluded hunks out of groups; list them under "excluded" with a reason.
- Create the greatest number of commits that remain independently meaningful and complete.
- Split unrelated work and separable refactors, behavior, tests, docs, config, and migrations.
- Keep pieces together when separating them would make either commit incomplete or broken.
- Add dependency group IDs to depends_on when order matters.
- For every final group, explain why it cannot be split further in unsplittable_reason.
- Commit messages must use clear, simple English in the form "type(scope): verb exact behavior".
- Reject and avoid generic words like update, change, misc, cleanup, wip, remaining.
- Do not create artificial micro-commits just to increase the count.
Respond as a single JSON object matching the CommitPlan schema.
"""

DIRECT_SYSTEM = """You are the lead Git commit planner. You see the complete final change.
Your goal is the greatest possible number of honest, useful atomic commits.
Rules:
- Use only supplied files and hunk IDs. Assign every safe hunk exactly once.
- Each commit must have one clear purpose and include everything required for that purpose.
- Split separable refactors, features, fixes, tests, docs, config, and migrations.
- Keep changes together when a split would be incomplete, misleading, or broken.
- Order dependent commits with depends_on group IDs.
- Use clear, simple English messages: "type(scope): verb exact behavior".
- Avoid generic words: update, change, misc, cleanup, wip, remaining.
- State why each group cannot be split further in unsplittable_reason.
- Never create empty or artificial commits.
Respond as one JSON object matching the CommitPlan schema.
"""

REVIEW_SYSTEM = """You are the final reviewer for an atomic Git commit plan.
Try hard to split every proposed commit into more useful commits while preserving meaning.
Also repair missing, duplicated, wrongly coupled, or wrongly ordered hunks.
Return the complete corrected CommitPlan, even when no changes are needed.
Use clear, simple English. Never create artificial micro-commits.
"""


def map_user(
    context_pack: dict[str, Any], chunk_id: str, chunk_diff: str, hunk_ids: list[str],
    change_facts: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "context": {
                key: value for key, value in context_pack.items()
                if key != "hunk_inventory"
            },
            "chunk_id": chunk_id,
            "hunk_ids_in_chunk": hunk_ids,
            "diff": chunk_diff,
            "local_change_facts": change_facts or {},
            "output_schema": {
                "chunk_id": "str",
                "summary": "str",
                "detected_concerns": ["str"],
                "risky_hunks": ["str"],
                "message_terms": {"<hunk_id>": ["term"]},
            },
        },
        ensure_ascii=False,
    )


def _reduce_payload(
    context_pack: dict[str, Any],
    chunk_reviews: list[dict[str, Any]],
    hunk_inventory: list[dict[str, Any]],
    mode: str,
    repo_fingerprint: str,
    base_head: str,
) -> dict[str, Any]:
    return {
        "context": {
            key: value for key, value in context_pack.items()
            if key != "hunk_inventory"
        },
        "mode": mode,
        "repo_fingerprint": repo_fingerprint,
        "base_head": base_head,
        "chunk_reviews": chunk_reviews,
        "hunk_inventory": hunk_inventory,
        "output_schema": {
            "version": "1",
            "mode": mode,
            "repo_fingerprint": repo_fingerprint,
            "base_head": base_head,
            "groups": [
                {
                    "group_id": "str",
                    "message": "type(scope): verb exact behavior",
                    "rationale": "str",
                    "hunk_ids": ["str"],
                    "file_paths": ["str"],
                    "risk": "low|medium|high",
                    "depends_on": ["group_id"],
                    "unsplittable_reason": "why another split would be incomplete or artificial",
                }
            ],
            "excluded": [{"path": "str", "reason": "str", "hunk_ids": ["str"]}],
            "warnings": ["str"],
        },
    }


def reduce_user(
    context_pack: dict[str, Any],
    chunk_reviews: list[dict[str, Any]],
    hunk_inventory: list[dict[str, Any]],
    mode: str,
    repo_fingerprint: str,
    base_head: str,
) -> str:
    return json.dumps(
        _reduce_payload(
            context_pack, chunk_reviews, hunk_inventory, mode, repo_fingerprint, base_head
        ),
        ensure_ascii=False,
    )


def direct_user(
    context_pack: dict[str, Any], change_graph: dict[str, Any], diff: str,
    mode: str, repo_fingerprint: str, base_head: str,
) -> str:
    payload = _reduce_payload(
        context_pack, [], context_pack["hunk_inventory"], mode, repo_fingerprint, base_head
    )
    payload.update(
        {
            "task": "Create the finest useful commit plan from this complete change.",
            "change_graph": change_graph,
            "diff": diff,
        }
    )
    return json.dumps(payload, ensure_ascii=False)


def review_user(
    context_pack: dict[str, Any], change_graph: dict[str, Any], current_plan: dict[str, Any],
    diff_or_evidence: Any, mode: str, repo_fingerprint: str, base_head: str,
) -> str:
    payload = _reduce_payload(
        context_pack, [], context_pack["hunk_inventory"], mode, repo_fingerprint, base_head
    )
    payload.update(
        {
            "task": "Return a corrected plan with the maximum honest number of atomic commits.",
            "current_plan": current_plan,
            "change_graph": change_graph,
            "diff_or_evidence": diff_or_evidence,
            "review_checks": [
                "Can any group be split into two independently meaningful commits?",
                "Does each group contain everything required for its purpose?",
                "Are unrelated hunks separated?",
                "Are dependencies ordered?",
                "Are all messages specific and plain?",
            ],
        }
    )
    return json.dumps(payload, ensure_ascii=False)
