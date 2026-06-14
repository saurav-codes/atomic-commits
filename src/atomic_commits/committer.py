"""Committer: stage each group, commit, rescan (implementation.md sections 6.2, 17, 22)."""

from __future__ import annotations

from .config import RunConfig
from .errors import CommitError, PatchApplyError
from .git_client import GitClient
from .models import AppliedCommit, CommitPlan, WorktreeSnapshot
from .scanner import scan
from .stager import Stager


class Committer:
    def __init__(self, git: GitClient, cfg: RunConfig) -> None:
        self.git = git
        self.cfg = cfg
        self.stager = Stager(git, include_staged=cfg.include_staged)

    def apply(
        self, plan: CommitPlan, on_event=None, planned_snapshot: WorktreeSnapshot | None = None
    ) -> list[AppliedCommit]:
        """Apply each commit group in order, rescanning between commits.

        Stops on the first unsafe mismatch. Never creates a broad fallback
        commit. Returns the per-group apply log.
        """
        results: list[AppliedCommit] = []
        snapshot = scan(self.git, self.cfg)
        planned_snapshot = planned_snapshot or snapshot
        if self.cfg.include_staged:
            planned_paths = {
                path
                for file_change in planned_snapshot.files
                if file_change.safety.safe
                for path in (file_change.path, file_change.old_path)
                if path
            }
            staged_paths = set(self.git.cached_changed_paths())
            staged_rename_sources = {
                file_change.old_path
                for file_change in planned_snapshot.files
                if file_change.status == "renamed"
                and file_change.path in staged_paths
                and file_change.old_path
            }
            self.stager.unstage(sorted((planned_paths & staged_paths) | staged_rename_sources))
            snapshot = scan(self.git, self.cfg)
        head_before = self.git.head_sha()

        for idx, group in enumerate(plan.groups, start=1):
            record = AppliedCommit(group_id=group.group_id, message=group.message)
            staged: list[str] = []
            try:
                staged = self.stager.stage_group(group, snapshot, planned_snapshot)
                if not staged:
                    raise PatchApplyError("no hunks staged for group")
                sha = self.git.commit(group.message, no_verify=self.cfg.no_verify)
                record.sha = sha
                record.status = "committed"
                if on_event:
                    on_event(idx, len(plan.groups), group, sha)
            except (PatchApplyError, CommitError) as exc:
                record.status = "failed"
                record.detail = str(exc)
                results.append(record)
                # Never leave staged changes behind (section 21). Unstage the
                # paths we actually touched, falling back to the group's
                # declared file paths if staging failed before returning any.
                cleanup = staged or group.file_paths
                self.stager.unstage(cleanup)
                return results

            results.append(record)

            # Hook-mutation / unexpected HEAD detection, then rescan.
            new_head = self.git.head_sha()
            if new_head == head_before:
                record.status = "failed"
                record.detail = "commit did not advance HEAD"
                return results
            head_before = new_head
            snapshot = scan(self.git, self.cfg)

        return results
