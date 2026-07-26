"""Integration test: Stager raises PatchApplyError when a whole_file_new path's
worktree content is mutated after the plan was created.

Guards the atomic-commit guarantee for untracked partial-file staging. When the
no-index diff fallback is used to re-derive hunks for an intent-to-add new
file, a fingerprint rematch failure must raise PatchApplyError rather than
silently staging every hunk of the file (the old ``or cur_fc.hunks``
behaviour). A future refactor that reverts the raise would stage the mutated
content and break the guarantee; this test catches that regression.
"""

from __future__ import annotations

import pytest

from atomic_commits.errors import PatchApplyError
from atomic_commits.git_client import GitClient
from atomic_commits.models import CommitGroup
from atomic_commits.scanner import scan
from atomic_commits.stager import Stager

from .helpers import git, make_cfg, staged_paths


def test_stage_group_raises_when_new_file_mutated_after_plan(git_repo):
    # 1. Create a new untracked file and scan it. The scanner synthesizes a
    #    single added-file hunk covering the whole content.
    new_file = git_repo / "new_module.py"
    new_file.write_text("def foo():\n    return 1\n")
    cfg = make_cfg(git_repo, mode="compact")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)

    added = [f for f in snapshot.files if f.path == "new_module.py"]
    assert len(added) == 1
    planned_fc = added[0]
    assert planned_fc.status == "added"
    assert len(planned_fc.hunks) == 1
    wanted_hunk = planned_fc.hunks[0]

    # 2. Build a planned snapshot giving the new file a second hunk the group
    #    will not request. stage_group only routes a whole_file_new file
    #    through the intent-to-add -> no-index fallback path when the group
    #    does not cover every hunk of the file; a real new file has one hunk,
    #    so a phantom second hunk is required to reach that branch.
    phantom = wanted_hunk.model_copy(update={"hunk_id": "new_module.py::hunk::phantom"})
    planned_fc = planned_fc.model_copy(update={"hunks": [wanted_hunk, phantom]})
    planned_snapshot = snapshot.model_copy(
        update={
            "files": [
                planned_fc if f.path == "new_module.py" else f for f in snapshot.files
            ]
        }
    )
    group = CommitGroup(
        group_id="g1",
        message="feat(core): add new_module",
        hunk_ids=[wanted_hunk.hunk_id],
        file_paths=["new_module.py"],
    )

    # 3. Mutate the worktree after the plan was captured so the hunk
    #    fingerprint no longer matches.
    new_file.write_text("def foo():\n    return 2\n")

    # 4. Staging must raise: the planned hunk no longer matches the worktree.
    stager = Stager(gc)
    with pytest.raises(PatchApplyError) as exc:
        stager.stage_group(group, snapshot, planned_snapshot)
    assert "no longer match" in str(exc.value)

    # 5. Nothing was staged. Intent-to-add alone does not appear in
    #    `git diff --cached`, so the index is clean. A regression to
    #    `or cur_fc.hunks` would stage the mutated content here.
    assert staged_paths(git_repo) == []


def test_stage_group_rejects_one_hunk_of_a_rename(git_repo):
    lines = [f"line {index}" for index in range(40)]
    (git_repo / "old.py").write_text("\n".join(lines) + "\n")
    git(git_repo, "add", "old.py")
    git(git_repo, "commit", "-q", "-m", "seed old.py")
    git(git_repo, "mv", "old.py", "new.py")
    lines[2] = "changed near start"
    lines[30] = "changed near end"
    (git_repo / "new.py").write_text("\n".join(lines) + "\n")

    cfg = make_cfg(git_repo, mode="compact", include_staged=True)
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    renamed = next(file_change for file_change in snapshot.files if file_change.status == "renamed")
    assert len(renamed.hunks) == 2
    group = CommitGroup(
        group_id="g1",
        message="refactor(core): update part of renamed file",
        hunk_ids=[renamed.hunks[0].hunk_id],
    )

    with pytest.raises(PatchApplyError, match="split across commit groups"):
        Stager(gc, include_staged=True).stage_group(group, snapshot, snapshot)
