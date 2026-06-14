"""Conservative hunk staging (implementation.md section 17).

For each group: recompute the current diff, rematch planned hunk fingerprints,
build a minimal patch, check then apply it to the index, verify the staged diff
contains only planned paths, and never use zero-context patches.
"""

from __future__ import annotations

from . import diff_parser
from .errors import PatchApplyError
from .git_client import GitClient
from .models import CommitGroup, FileChange
from .scanner import safe_files


def _index_current_files(git: GitClient, include_staged: bool) -> dict[str, FileChange]:
    patch = git.diff_worktree()
    files = diff_parser.parse_patch(patch, is_tracked=True)
    return {f.path: f for f in files}


class Stager:
    def __init__(self, git: GitClient, include_staged: bool = False) -> None:
        self.git = git
        self.include_staged = include_staged

    def stage_group(self, group: CommitGroup, snapshot, planned_snapshot=None) -> list[str]:
        """Stage exactly the hunks of one group. Returns staged file paths.

        Rescans the worktree to rematch fingerprints, so it is safe to call in
        sequence after earlier commits have changed line numbers.
        """
        planned_snapshot = planned_snapshot or snapshot
        wanted_fps: set[str] = set()
        wanted_paths: set[str] = set()
        whole_file_new: set[str] = set()
        whole_file_delete: set[str] = set()
        whole_file_mode: set[str] = set()
        whole_file_rename: list[tuple[str, str]] = []

        planned_by_id = {
            h.hunk_id: (f, h)
            for f in safe_files(planned_snapshot)
            for h in f.hunks
        }
        file_by_path = {f.path: f for f in safe_files(snapshot)}
        planned_file_by_path = {f.path: f for f in safe_files(planned_snapshot)}

        for hid in group.hunk_ids:
            if hid not in planned_by_id:
                raise PatchApplyError(f"planned hunk '{hid}' not found in planned snapshot")
            fc, hunk = planned_by_id[hid]
            wanted_fps.add(hunk.fingerprint)
            wanted_paths.add(fc.path)
            if fc.status == "added":
                whole_file_new.add(fc.path)
            if fc.status == "deleted":
                whole_file_delete.add(fc.path)
            if fc.status == "mode":
                whole_file_mode.add(fc.path)
            if fc.status == "renamed" and fc.old_path:
                whole_file_rename.append((fc.old_path, fc.path))

        staged_paths: list[str] = []

        # Whole-file additions / deletions handled directly.
        for path in whole_file_new:
            fc = planned_file_by_path[path]
            # If every hunk of the file is in this group, add the whole file.
            file_hids = {h.hunk_id for h in fc.hunks}
            if file_hids.issubset(set(group.hunk_ids)):
                self.git.add_path(path)
                staged_paths.append(path)
            else:
                self.git.add_intent(path)
        for path in whole_file_delete:
            self.git.rm_cached(path)
            staged_paths.append(path)
        for old_path, new_path in whole_file_rename:
            self.git.add_all_paths([old_path, new_path])
            staged_paths.append(new_path)
        for path in whole_file_mode:
            self.git.add_path(path)
            staged_paths.append(path)

        # Patch-stage remaining hunks per file.
        current = _index_current_files(self.git, self.include_staged)
        for path in wanted_paths:
            if path in whole_file_delete or path in whole_file_mode:
                continue
            if path in staged_paths:
                continue
            cur_fc = current.get(path) or file_by_path.get(path)
            if cur_fc is None:
                raise PatchApplyError(f"file '{path}' no longer present for staging")
            matched = [h for h in cur_fc.hunks if h.fingerprint in wanted_fps]
            if not matched:
                # New file added via intent-to-add: re-derive hunks from no-index diff.
                if path in whole_file_new:
                    synth = diff_parser.parse_patch(self.git.diff_no_index(path), is_tracked=False)
                    if synth:
                        cur_fc = synth[0]
                        cur_fc.path = path
                        cur_fc.status = "added"
                        matched = [
                            h for h in cur_fc.hunks
                            if h.fingerprint in wanted_fps
                        ] or cur_fc.hunks
                if not matched:
                    raise PatchApplyError(
                        f"planned hunk(s) for '{path}' no longer match the worktree"
                    )
            patch = diff_parser.build_patch_for_hunks(cur_fc, matched)
            patch_bytes = patch.encode("utf-8", "surrogateescape")
            try:
                self.git.apply_cached_check(patch_bytes)
                self.git.apply_cached(patch_bytes)
            except PatchApplyError:
                self.git.restore_staged(list(staged_paths) + [path])
                raise
            staged_paths.append(path)

        self._verify_only(wanted_paths)
        return staged_paths

    def _verify_only(self, wanted_paths: set[str]) -> None:
        staged = self.git.diff_cached()
        staged_files = diff_parser.parse_patch(staged, is_tracked=True)
        extra = {f.path for f in staged_files} - wanted_paths
        if extra:
            raise PatchApplyError(
                f"staged changes include unplanned paths: {', '.join(sorted(extra))}"
            )

    def unstage(self, paths: list[str]) -> None:
        self.git.restore_staged(paths)
