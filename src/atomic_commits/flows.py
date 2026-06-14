"""High-level flows and preflight (implementation.md sections 6, 7).

Ties scanning, planning, validation, session writing, and applying together so
the CLI stays thin. Async planning is driven via asyncio.run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import output
from .committer import Committer
from .config import RunConfig, resolve_provider_credentials
from .errors import FingerprintMismatchError, PreflightError
from .git_client import GitClient
from .models import CommitPlan
from .planner import plan as run_planning
from .providers import build_provider
from .scanner import scan
from .session import SessionStore


def preflight(git: GitClient, cfg: RunConfig) -> None:
    if not git.is_git_repo():
        raise PreflightError(
            "not inside a Git repository",
            hint="Run atc from within a Git working tree or pass --repo.",
        )
    markers = git.in_progress_state_files()
    if markers:
        raise PreflightError(
            f"git operation in progress: {', '.join(markers)}",
            hint="Finish or abort the in-progress operation first.",
        )
    if not git.has_commits():
        raise PreflightError(
            "repository has no commits yet",
            hint="Create an initial commit before running atc.",
        )
    if git.has_staged_changes() and not cfg.include_staged:
        raise PreflightError(
            "index has staged changes",
            hint="Unstage them, or rerun with --include-staged.",
        )
    root = git.toplevel()
    for p in cfg.paths:
        if not (root / p).exists():
            raise PreflightError(f"path does not exist: {p}")


def _build_plan(git: GitClient, cfg: RunConfig, store: SessionStore, session_id: str) -> CommitPlan:
    cfg = resolve_provider_credentials(cfg)
    snapshot = scan(git, cfg)
    store.write_snapshot(session_id, snapshot)
    provider = build_provider(cfg)
    commit_plan = asyncio.run(run_planning(provider, git, snapshot, cfg))
    store.write_plan(session_id, commit_plan)
    store.write_excluded(session_id, commit_plan)
    output.print_plan(commit_plan, snapshot, as_json=cfg.json_output)
    return commit_plan


def dry_run(git: GitClient, cfg: RunConfig) -> CommitPlan:
    preflight(git, cfg)
    store = SessionStore(git)
    session_id = store.create()
    return _build_plan(git, cfg, store, session_id)


def apply_saved(git: GitClient, cfg: RunConfig, plan_path: Path | None) -> None:
    preflight(git, cfg)
    store = SessionStore(git)
    commit_plan = store.load_plan_file(plan_path) if plan_path else store.load_latest_plan()
    if commit_plan is None:
        raise PreflightError(
            "no saved plan found",
            hint="Run `atc` first to create a plan, or pass --plan PATH.",
        )
    snapshot = scan(git, cfg)
    if snapshot.fingerprint != commit_plan.repo_fingerprint:
        raise FingerprintMismatchError(
            "worktree no longer matches the saved plan",
            hint="Rerun `atc` to create a fresh plan.",
        )
    session_id = store.create()
    store.write_backup(session_id, git.backup_patch())
    committer = Committer(git, cfg)
    results = committer.apply(commit_plan, on_event=output.print_apply_progress)
    store.write_apply_log(session_id, results)
    output.print_apply_result(results, as_json=cfg.json_output)


def apply_now(git: GitClient, cfg: RunConfig) -> None:
    preflight(git, cfg)
    store = SessionStore(git)
    session_id = store.create()
    commit_plan = _build_plan(git, cfg, store, session_id)
    store.write_backup(session_id, git.backup_patch())
    committer = Committer(git, cfg)
    results = committer.apply(commit_plan, on_event=output.print_apply_progress)
    store.write_apply_log(session_id, results)
    output.print_apply_result(results, as_json=cfg.json_output)


def resume(git: GitClient, cfg: RunConfig) -> None:
    preflight(git, cfg)
    store = SessionStore(git)
    session_id = store.latest_incomplete_session()
    if session_id is None:
        raise PreflightError("no incomplete session to resume")
    commit_plan = store.load_plan_file(store.session_path(session_id) / "plan.json")
    planned_snapshot = store.load_snapshot(session_id)

    done = {a.group_id for a in store.load_apply_log(session_id) if a.status == "committed"}
    remaining_groups = [g for g in commit_plan.groups if g.group_id not in done]
    if not remaining_groups:
        output.print_apply_result(store.load_apply_log(session_id), as_json=cfg.json_output)
        return

    # Do NOT compare against the original whole-repo fingerprint: once some
    # groups have been committed, HEAD has advanced and committed hunks are gone
    # from the worktree, so the original fingerprint can never match again
    # (section 18). Instead, verify that every hunk still required by the
    # remaining groups is present in the current worktree scan.
    snapshot = scan(git, cfg)
    reference_snapshot = planned_snapshot or snapshot
    present_fps = {h.fingerprint for f in snapshot.files if f.safety.safe for h in f.hunks}
    planned_fps = {
        h.hunk_id: h.fingerprint
        for f in reference_snapshot.files
        if f.safety.safe
        for h in f.hunks
    }
    missing = [
        hid
        for g in remaining_groups
        for hid in g.hunk_ids
        if planned_fps.get(hid) not in present_fps
    ]
    if missing:
        raise FingerprintMismatchError(
            "worktree changed since the session was created; "
            f"{len(missing)} planned hunk(s) no longer match",
            hint="Rerun `atc` to create a fresh plan.",
        )

    remaining = CommitPlan(
        version=commit_plan.version,
        mode=commit_plan.mode,
        repo_fingerprint=commit_plan.repo_fingerprint,
        base_head=commit_plan.base_head,
        groups=remaining_groups,
        excluded=commit_plan.excluded,
        warnings=commit_plan.warnings,
    )
    store.write_backup(session_id, git.backup_patch())
    committer = Committer(git, cfg)
    results = committer.apply(
        remaining, on_event=output.print_apply_progress, planned_snapshot=planned_snapshot
    )
    # Merge new results with previously-committed ones for an accurate log.
    prior = [a for a in store.load_apply_log(session_id) if a.group_id in done]
    store.write_apply_log(session_id, prior + results)
    output.print_apply_result(results, as_json=cfg.json_output)


def doctor(git: GitClient, cfg: RunConfig) -> list[str]:
    """Return a list of human-readable check results."""
    results: list[str] = []
    results.append(
        "git repo: ok" if git.is_git_repo() else "git repo: NOT a git repository"
    )
    cfg = resolve_provider_credentials(cfg)
    results.append(f"provider: {cfg.provider}")
    results.append(f"model: {cfg.model or 'NOT SET'}")
    if cfg.provider == "openai-compatible":
        results.append(f"base_url: {cfg.base_url}")
    results.append("api key: found" if cfg.api_key else "api key: MISSING")
    return results
