"""Rich terminal output (implementation.md section 20)."""

from __future__ import annotations

import json

from rich.table import Table

from .logging import console
from .models import AppliedCommit, CommitPlan, WorktreeSnapshot


def print_plan(plan: CommitPlan, snapshot: WorktreeSnapshot, *, as_json: bool = False) -> None:
    if as_json:
        console.print_json(json.dumps(plan.model_dump()))
        return
    safe_hunks = sum(len(f.hunks) for f in snapshot.files if f.safety.safe)
    excluded = [f for f in snapshot.files if not f.safety.safe]
    console.print("[bold]Atomic Commits Plan[/bold]")
    console.print(f"Repo: {snapshot.repo_root}")
    console.print(f"Mode: {plan.mode}")
    console.print(f"Safe hunks: {safe_hunks}")
    console.print(f"Excluded files: {len(excluded)}")
    console.print(f"Planned commits: {len(plan.groups)}\n")

    for i, group in enumerate(plan.groups, start=1):
        console.print(f"[bold]{i}. {group.message}[/bold]")
        console.print(f"   Hunks: {', '.join(group.hunk_ids)}")
        if group.rationale:
            console.print(f"   Why: {group.rationale}")
        console.print()

    if plan.excluded:
        console.print("[yellow]Excluded:[/yellow]")
        for e in plan.excluded:
            console.print(f"  - {e.path}: {e.reason}")
    if plan.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in plan.warnings:
            console.print(f"  - {w}")


def print_apply_progress(idx: int, total: int, group, sha: str) -> None:
    short = sha[:7] if sha else "???????"
    console.print(f"[{idx}/{total}] {group.message} ... [green]{short}[/green]")


def print_apply_result(results: list[AppliedCommit], *, as_json: bool = False) -> None:
    if as_json:
        console.print_json(json.dumps([r.model_dump() for r in results]))
        return
    failed = [r for r in results if r.status == "failed"]
    committed = [r for r in results if r.status == "committed"]
    if failed:
        last = failed[-1]
        console.print(f"\n[red]Stopped.[/red] Reason: {last.detail}")
        console.print("No broad fallback commit was created.")
        console.print("Next: rerun `atc` to create a fresh plan.")
    else:
        console.print("\n[green]Done.[/green] Worktree clean except excluded files.")
    console.print(f"Committed {len(committed)} of {len(results)} planned commits.")


def print_sessions(sessions: list[str]) -> None:
    if not sessions:
        console.print("No sessions found.")
        return
    table = Table(title="atc sessions")
    table.add_column("Session ID")
    for s in sessions:
        table.add_row(s)
    console.print(table)
