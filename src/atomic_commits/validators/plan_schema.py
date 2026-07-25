"""Plan schema and semantic validation (implementation.md section 24).

Checks that every safe hunk is assigned exactly once, no duplicates, excluded
hunks have reasons, groups are non-empty with valid messages, and group file
paths match their hunk paths.
"""

from __future__ import annotations

from ..models import CommitPlan, Mode
from .commit_messages import validate_commit_message


def validate_plan(
    plan: CommitPlan,
    *,
    safe_hunk_ids: list[str],
    hunk_to_path: dict[str, str],
    mode: Mode,
) -> list[str]:
    """Return a list of validation error strings. Empty means valid."""
    errors: list[str] = []
    safe_set = set(safe_hunk_ids)

    assigned: list[str] = []
    group_ids = [group.group_id for group in plan.groups]
    if len(group_ids) != len(set(group_ids)):
        errors.append("group IDs must be unique")
    known_groups = set(group_ids)
    prior_groups: set[str] = set()
    for group in plan.groups:
        if not group.hunk_ids:
            errors.append(f"group '{group.group_id}' has no hunks")
        for hid in group.hunk_ids:
            if hid not in hunk_to_path:
                errors.append(f"group '{group.group_id}' references unknown hunk '{hid}'")
            assigned.append(hid)

        msg_reasons = validate_commit_message(group.message)
        if msg_reasons:
            errors.append(
                f"group '{group.group_id}' message rejected: {'; '.join(msg_reasons)}"
            )

        # File paths declared on the group must match the paths derived from its
        # hunks. An empty declared list is allowed (we derive from hunks); a
        # non-empty list must match exactly so a group cannot silently claim a
        # path it has no hunks for, or omit one it does.
        hunk_paths = {hunk_to_path[h] for h in group.hunk_ids if h in hunk_to_path}
        if group.file_paths:
            declared = set(group.file_paths)
            if declared != hunk_paths:
                errors.append(
                    f"group '{group.group_id}' file_paths {sorted(declared)} "
                    f"do not match its hunk paths {sorted(hunk_paths)}"
                )

        if mode == "verbose" and len(group.hunk_ids) > 1 and not group.rationale.strip():
            errors.append(
                f"verbose group '{group.group_id}' has multiple hunks without rationale"
            )
        for dependency in group.depends_on:
            if dependency not in known_groups:
                errors.append(
                    f"group '{group.group_id}' depends on unknown group '{dependency}'"
                )
            elif dependency not in prior_groups:
                errors.append(
                    f"group '{group.group_id}' must appear after dependency '{dependency}'"
                )
        prior_groups.add(group.group_id)

    # Duplicate assignment.
    seen: set[str] = set()
    for hid in assigned:
        if hid in seen:
            errors.append(f"hunk '{hid}' assigned to more than one group")
        seen.add(hid)

    # Every safe hunk assigned exactly once.
    missing = safe_set - seen
    if missing:
        errors.append(
            f"{len(missing)} safe hunk(s) not assigned to any group: "
            + ", ".join(sorted(missing)[:10])
        )

    # Excluded entries need reasons.
    for exc in plan.excluded:
        if not exc.reason.strip():
            errors.append(f"excluded entry for '{exc.path}' has no reason")

    return errors
