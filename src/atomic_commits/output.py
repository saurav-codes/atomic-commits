"""Rich terminal output (implementation.md section 20)."""

from __future__ import annotations

import contextvars
import json
import sys
from contextlib import nullcontext
from threading import Event, Thread
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .models import AppliedCommit, CommitPlan, Hunk, WorktreeSnapshot

console = Console()
err_console = Console(stderr=True)

# Track the currently-active spinner so nested `step()` calls can suspend
# the outer spinner before starting their own, avoiding garbled output.
#
# A ContextVar (rather than a module global) keeps the nesting chain
# per-context/per-thread, so two consoles running concurrently (e.g. in
# tests) don't clobber each other's active spinner.
_active_spinner: contextvars.ContextVar[_ElapsedSpinner | None] = contextvars.ContextVar(
    "atc_active_spinner", default=None
)

# Rich color per CommitGroup.risk level, used by print_plan.
_RISK_COLOR: dict[str, str] = {"high": "red", "medium": "yellow", "low": "green"}

# Rich color per session status, used by print_sessions for dict input.
_STATUS_COLOR: dict[str, str] = {"planned": "yellow", "committed": "green", "failed": "red"}


class _ElapsedSpinner:
    """Wraps Rich's Status and updates its text every second with elapsed time.

    Keeps the spinner animation alive (Rich handles the icon cycling) while
    appending "(Ns elapsed)" so the user sees continuous progress during long
    LLM calls instead of a frozen message. Suspends any outer spinner while
    running so nested spinners don't garble the terminal.
    """

    def __init__(self, message: str, console: Console) -> None:
        self._base = message
        self._console = console
        self._status = console.status(message, spinner="dots")
        self._stop = Event()
        self._thread: Thread | None = None
        self._outer: _ElapsedSpinner | None = None
        self._token: contextvars.Token[_ElapsedSpinner | None] | None = None

    def __enter__(self) -> _ElapsedSpinner:
        # Suspend the outer spinner (if any) before starting ours.
        self._outer = _active_spinner.get()
        if self._outer is not None:
            self._outer._suspend()
        self._status.start()
        self._thread = Thread(target=self._tick, daemon=True)
        self._thread.start()
        self._token = _active_spinner.set(self)
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._status.stop()
        if self._token is not None:
            _active_spinner.reset(self._token)
        # Resume the outer spinner (if any).
        if self._outer is not None:
            self._outer._resume()

    def _suspend(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        self._status.stop()

    def _resume(self) -> None:
        self._stop = Event()
        self._status.start()
        self._thread = Thread(target=self._tick, daemon=True)
        self._thread.start()

    def _tick(self) -> None:
        import time

        start = time.monotonic()
        while not self._stop.wait(1.0):
            elapsed = int(time.monotonic() - start)
            self._status.update(f"{self._base} ({elapsed}s elapsed)")


def step(message: str, *, enabled: bool = True):
    """Context manager showing a Rich spinner on stderr while a block runs.

    The spinner appends elapsed time every second so the user sees continuous
    progress. Nested `step()` calls suspend their outer spinner while running
    so the terminal doesn't garble. No-op when disabled (JSON mode) or stderr
    isn't a TTY, so piped/CI output stays clean.
    """
    if not enabled or not err_console.is_terminal:
        return nullcontext()
    return _ElapsedSpinner(message, err_console)


def note(message: str, *, enabled: bool = True, style: str = "dim") -> None:
    """Print one real progress fact without polluting piped or JSON output."""
    if enabled and err_console.is_terminal:
        err_console.print(f"[{style}]{escape(message)}[/{style}]")


def format_files(paths: list[str], max_show: int = 3, verbose: bool = False) -> str:
    """Compact label for a list of file paths, truncating with '+N more'.

    When ``verbose`` is True (or the list fits within ``max_show``), every
    path is shown; otherwise the list is truncated to ``max_show`` entries
    with a ``+N more`` suffix.
    """
    if not paths:
        return "no files"
    if verbose or len(paths) <= max_show:
        return ", ".join(paths)
    shown = ", ".join(paths[:max_show])
    return f"{shown}, +{len(paths) - max_show} more"


def format_token_usage(usage: dict[str, Any]) -> str:
    """Render a provider telemetry dict as a human-readable string.

    Expected keys (from providers.base.get_total_usage): ``prompt_tokens``,
    ``completion_tokens``, ``total_tokens``, ``estimated_cost_usd``. Missing
    keys are treated as zero. Returns a string like ``~1,234 tokens`` and
    appends an estimated cost when available, e.g.
    ``~1,234 tokens, ~$0.0123 est.``.
    """
    total = usage.get("total_tokens")
    if not total:
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        total = prompt + completion
    try:
        total_int = int(total)
    except (TypeError, ValueError):
        total_int = 0
    parts = [f"~{total_int:,} tokens"]
    cost = usage.get("estimated_cost_usd")
    if cost:
        try:
            cost_f = float(cost)
        except (TypeError, ValueError):
            cost_f = 0.0
        if cost_f > 0:
            parts.append(f"~${cost_f:.4f} est.")
    return ", ".join(parts)


def _hunk_diff_lines(hunk: Hunk) -> list[str]:
    """Return a hunk's diff as rich-markup-colored lines.

    Prefers the stored ``patch`` text; falls back to reconstructing from the
    header and the context/added/removed lists. ``+`` lines are green, ``-``
    lines red, ``@@`` headers cyan, and ``\\ No newline`` markers dim. All
    content is escaped so code containing ``[``/`]`` renders literally.
    """
    text = hunk.patch
    if not text:
        parts = [hunk.header]
        for line in hunk.context_before:
            parts.append(f" {line}")
        for line in hunk.removed:
            parts.append(f"-{line}")
        for line in hunk.added:
            parts.append(f"+{line}")
        for line in hunk.context_after:
            parts.append(f" {line}")
        text = "\n".join(parts)
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("@@"):
            out.append(f"[cyan]{escape(line)}[/cyan]")
        elif line.startswith("+"):
            out.append(f"[green]{escape(line)}[/green]")
        elif line.startswith("-"):
            out.append(f"[red]{escape(line)}[/red]")
        elif line.startswith("\\"):
            out.append(f"[dim]{escape(line)}[/dim]")
        else:
            out.append(escape(line))
    return out


def print_plan(
    plan: CommitPlan,
    snapshot: WorktreeSnapshot,
    *,
    as_json: bool = False,
    show_diff: bool = False,
) -> None:
    if as_json:
        sys.stdout.write(json.dumps(plan.model_dump(), default=str) + "\n")
        sys.stdout.flush()
        return
    safe_hunks = sum(len(f.hunks) for f in snapshot.files if f.safety.safe)
    excluded = [f for f in snapshot.files if not f.safety.safe]
    console.print("[bold]Atomic Commits Plan[/bold]")
    console.print(f"Repo: {snapshot.repo_root}")
    console.print(f"Mode: {plan.mode}")
    console.print(f"Safe hunks: {safe_hunks}")
    console.print(f"Excluded files: {len(excluded)}")
    console.print(f"Planned commits: {len(plan.groups)}\n")

    hunk_by_id: dict[str, Hunk] = {}
    if show_diff:
        for f in snapshot.files:
            for h in f.hunks:
                hunk_by_id[h.hunk_id] = h

    for i, group in enumerate(plan.groups, start=1):
        risk_color = _RISK_COLOR.get(group.risk, "white")
        console.print(f"[bold {risk_color}]{i}. {group.message}[/bold {risk_color}]")
        console.print(f"   Hunks: {', '.join(group.hunk_ids)}")
        if group.rationale:
            console.print(f"   Why: {group.rationale}")
        if show_diff:
            for hid in group.hunk_ids:
                hunk = hunk_by_id.get(hid)
                if hunk is None:
                    continue
                console.print(f"   [dim]{escape(hunk.file_path)} {escape(hunk.header)}[/dim]")
                for line in _hunk_diff_lines(hunk):
                    console.print(f"      {line}")
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
        sys.stdout.write(json.dumps([r.model_dump() for r in results], default=str) + "\n")
        sys.stdout.flush()
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


def print_sessions(sessions: list[str] | list[dict[str, Any]]) -> None:
    """Render the session list.

    Accepts either a list of session-id strings (legacy: prints a bare ID
    list) or a list of dicts with ``id``, ``timestamp``, ``status``
    (``planned``/``committed``/``failed``), and ``commits`` (count) keys,
    rendered as a rich table with color-coded status.
    """
    if not sessions:
        console.print("No sessions found.")
        return
    if isinstance(sessions[0], dict):
        table = Table(title="atc sessions")
        table.add_column("Session")
        table.add_column("Timestamp")
        table.add_column("Status")
        table.add_column("Commits", justify="right")
        for s in sessions:
            sid = str(s.get("id", s.get("session_id", "")))
            ts = str(s.get("timestamp", s.get("created", "")))
            status = str(s.get("status", ""))
            commits = s.get("commits", s.get("commit_count", 0))
            color = _STATUS_COLOR.get(status, "white")
            console_status = f"[{color}]{status}[/{color}]" if status else ""
            table.add_row(sid, ts, console_status, str(commits))
        console.print(table)
        return
    console.print("[bold]atc sessions[/bold]")
    for sid in sessions:
        console.print(f"  {sid}")
