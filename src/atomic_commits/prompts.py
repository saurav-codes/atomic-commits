"""Prompt templates (implementation.md sections 13 and 23).

Prompts make the AI's job explicit: it is planning Git commits, not editing
code; it must use only provided diffs; every safe hunk is assigned exactly
once; generic messages are invalid.
"""

from __future__ import annotations

import json
from typing import Any

MAP_SYSTEM = """You are planning Git commits, not editing code.
You review a chunk of a larger change set and report structured findings.
Rules:
- Use ONLY the provided diffs. Do not invent files, hunks, or behavior.
- Identify behavior-level intent for each hunk.
- Identify test/docs pairs and unrelated hunks.
- Flag any generated or runtime content the filters may have missed.
- Suggest precise, specific commit subjects. Generic subjects are invalid.
Respond as a single JSON object matching the ChunkReview schema.
"""

REDUCE_SYSTEM = """You are planning Git commits, not editing code.
You combine per-chunk reviews into a final commit plan.
Rules:
- Assign every safe hunk to exactly one commit group. Never duplicate a hunk.
- Keep unsafe/excluded hunks out of groups; list them under "excluded" with a reason.
- Commit messages must be specific to the behavior, in the form "scope: verb exact behavior".
- Reject and avoid generic words like update, change, misc, cleanup, wip, remaining.
- In compact mode, group by coherent behavior; pair implementation with its exact matching tests.
- In verbose mode, prefer one hunk per commit.
- If unsure about a hunk, place it in its own group with a precise message or mark it risky.
Respond as a single JSON object matching the CommitPlan schema.
"""


def map_user(context_pack: dict[str, Any], chunk_id: str, chunk_diff: str, hunk_ids: list[str]) -> str:
    return json.dumps(
        {
            "context": context_pack,
            "chunk_id": chunk_id,
            "hunk_ids_in_chunk": hunk_ids,
            "diff": chunk_diff,
            "output_schema": {
                "chunk_id": "str",
                "summary": "str",
                "detected_concerns": ["str"],
                "suggested_groups": [{"subject": "str", "hunk_ids": ["str"], "rationale": "str"}],
                "risky_hunks": ["str"],
                "message_terms": {"<hunk_id>": ["term"]},
            },
        },
        ensure_ascii=False,
    )


def reduce_user(
    context_pack: dict[str, Any],
    chunk_reviews: list[dict[str, Any]],
    hunk_inventory: list[dict[str, Any]],
    mode: str,
    repo_fingerprint: str,
    base_head: str,
) -> str:
    return json.dumps(
        {
            "context": context_pack,
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
                        "message": "scope: verb exact behavior",
                        "rationale": "str",
                        "hunk_ids": ["str"],
                        "file_paths": ["str"],
                        "risk": "low|medium|high",
                    }
                ],
                "excluded": [{"path": "str", "reason": "str", "hunk_ids": ["str"]}],
                "warnings": ["str"],
            },
        },
        ensure_ascii=False,
    )


def rename_message_user(hunk_context: str, rejected: str, reasons: list[str]) -> str:
    return json.dumps(
        {
            "task": "Rewrite this commit message to be specific to the hunk behavior.",
            "rejected_message": rejected,
            "rejection_reasons": reasons,
            "hunk_context": hunk_context,
            "format": "scope: verb exact behavior",
            "output_schema": {"message": "str"},
        },
        ensure_ascii=False,
    )
