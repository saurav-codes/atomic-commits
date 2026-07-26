"""Conservative, fingerprint-matched hunk staging."""

from __future__ import annotations

from . import diff_parser
from .errors import PatchApplyError
from .git_client import GitClient
from .models import CommitGroup


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
        wanted_fps: dict[str, set[str]] = {}
        wanted_changes: dict[str, set[tuple[tuple[str, ...], tuple[str, ...]]]] = {}
        wanted_paths: set[str] = set()
        whole_file_new: set[str] = set()
        whole_file_delete: set[str] = set()
        whole_file_mode: set[str] = set()
        whole_file_rename: set[tuple[str, str]] = set()

        planned_by_id = {
            h.hunk_id: (f, h)
            for f in planned_snapshot.files if f.safety.safe
            for h in f.hunks
        }
        file_by_path = {f.path: f for f in snapshot.files if f.safety.safe}
        planned_file_by_path = {f.path: f for f in planned_snapshot.files if f.safety.safe}

        for hid in group.hunk_ids:
            if hid not in planned_by_id:
                raise PatchApplyError(f"planned hunk '{hid}' not found in planned snapshot")
            fc, hunk = planned_by_id[hid]
            wanted_fps.setdefault(fc.path, set()).add(hunk.fingerprint)
            wanted_changes.setdefault(fc.path, set()).add(
                (tuple(hunk.removed), tuple(hunk.added))
            )
            wanted_paths.add(fc.path)
            if fc.status == "added":
                whole_file_new.add(fc.path)
            if fc.status == "deleted":
                whole_file_delete.add(fc.path)
            if fc.status == "mode":
                whole_file_mode.add(fc.path)
            if fc.status == "renamed" and fc.old_path:
                whole_file_rename.add((fc.old_path, fc.path))

        group_hunks = set(group.hunk_ids)
        for path in whole_file_delete | whole_file_mode | {new for _, new in whole_file_rename}:
            file_hunks = {h.hunk_id for h in planned_file_by_path[path].hunks}
            if not file_hunks.issubset(group_hunks):
                raise PatchApplyError(
                    f"whole-file change '{path}' is split across commit groups"
                )

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
        for old_path, new_path in sorted(whole_file_rename):
            self.git.add_all_paths([old_path, new_path])
            staged_rename = next(
                (
                    file_change
                    for file_change in diff_parser.parse_patch(
                        self.git.diff_cached(), is_tracked=True,
                    )
                    if file_change.path == new_path
                ),
                None,
            )
            actual = {
                (tuple(hunk.removed), tuple(hunk.added))
                for hunk in staged_rename.hunks
            } if staged_rename is not None else set()
            if actual != wanted_changes[new_path]:
                self.git.restore_staged([old_path, new_path])
                raise PatchApplyError(
                    f"renamed file '{new_path}' no longer matches the planned content"
                )
            staged_paths.append(new_path)
        for path in whole_file_mode:
            self.git.add_path(path)
            staged_paths.append(path)

        # Patch-stage remaining hunks per file.
        raw = self.git.diff_worktree()
        current = {f.path: f for f in diff_parser.parse_patch(raw, is_tracked=True)}
        for path in wanted_paths:
            if path in whole_file_delete or path in whole_file_mode:
                continue
            if path in staged_paths:
                continue
            cur_fc = current.get(path) or file_by_path.get(path)
            if cur_fc is None:
                raise PatchApplyError(f"file '{path}' no longer present for staging")
            matched = [
                h for h in cur_fc.hunks
                if h.fingerprint in wanted_fps.get(path, set())
                or (tuple(h.removed), tuple(h.added)) in wanted_changes.get(path, set())
            ]
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
                            if h.fingerprint in wanted_fps.get(path, set())
                            or (tuple(h.removed), tuple(h.added))
                            in wanted_changes.get(path, set())
                        ]
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
