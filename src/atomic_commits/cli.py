"""Typer CLI for atc.

Defines the full command surface (default plan/apply behavior plus the
doctor, resume, sessions, show-plan, init, undo, selftest, and explain
subcommands) and delegates the actual work to the flows module.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer

from . import __version__, flows
from .config import RunConfig
from .errors import AtcError
from .git_client import GitClient
from .models import CommitGroup, CommitPlan, WorktreeSnapshot
from .output import console, err_console, format_token_usage, print_plan, print_sessions
from .providers.base import get_total_usage
from .selftest import run_selftest
from .session import SessionStore

app = typer.Typer(
    name="atc",
    help="Turn a dirty Git worktree into meaningful atomic commits using AI.",
    no_args_is_help=False,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"atc {__version__}")
        raise typer.Exit()


# -- helpers ---------------------------------------------------------------


def _emit_json(obj: Any) -> None:
    """Write a single JSON object to stdout with no rich processing."""
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")
    sys.stdout.flush()


def _emit_json_error(message: str, hint: str | None = None) -> None:
    """Write a JSON error object to stderr (allowed under the --json contract)."""
    payload: dict[str, Any] = {"error": message}
    if hint:
        payload["hint"] = hint
    sys.stderr.write(json.dumps(payload) + "\n")


def _exit_on_atc_error(exc: AtcError, cfg: RunConfig) -> None:
    if cfg.json_output:
        _emit_json_error(exc.message, exc.hint)
    else:
        err_console.print(f"[red]error:[/red] {exc}")
    raise typer.Exit(code=exc.exit_code) from exc


def _print_doctor(results: list[tuple[str, bool]], *, as_json: bool) -> None:
    checks = [{"label": label, "ok": ok} for label, ok in results]
    if as_json:
        _emit_json({"checks": checks})
        return
    console.print("[bold]atc doctor[/bold]")
    for c in checks:
        marker = "[green]ok[/green]" if c["ok"] else "[red]check[/red]"
        console.print(f"{marker} {c['label']}")


def _doctor_diag(git: GitClient, cfg: RunConfig) -> list[tuple[str, bool]]:
    """Local, hermetic doctor diagnostic.

    Doctor is a local preflight check (git/python/credentials presence), not
    a live provider call, so it must NOT depend on the user's real
    ``~/.config/atc/config.toml`` or ``ATC_*`` env-var resolution. This avoids
    calling ``resolve_provider_credentials`` (which reads the config file and
    env); instead it checks credential ENV-VAR PRESENCE only.
    """
    results: list[tuple[str, bool]] = []
    results.append(("git repo", git.is_git_repo()))
    results.append((f"provider: {cfg.provider}", True))
    results.append((f"model: {cfg.model or 'NOT SET'}", bool(cfg.model)))
    if cfg.provider == "openai-compatible":
        results.append((f"base_url: {cfg.base_url or 'NOT SET'}", bool(cfg.base_url)))
    env_name = cfg.api_key_env or (
        "ATC_OPENAI_API_KEY" if cfg.provider == "openai-compatible" else "ATC_ANTHROPIC_API_KEY"
    )
    results.append((f"api key env: {env_name}", env_name in os.environ))
    return results


def _confirm_now(cfg: RunConfig, n: int, messages: list[str]) -> bool:
    """Gate --now/--amend commits: require --yes, or prompt in non-json mode (3.1)."""
    if cfg.yes:
        return True
    if cfg.json_output:
        raise AtcError(
            f"--now requires --yes to commit {n} proposed commit(s); pass --yes "
            "to proceed (or run without --json for an interactive prompt).",
        )
    console.print(f"[bold]Proposed commits ({n}):[/bold]")
    for i, msg in enumerate(messages, start=1):
        console.print(f"  {i}. {msg}")
    if not err_console.is_terminal:
        raise AtcError(
            f"--now requires --yes (non-interactive terminal); pass --yes to "
            f"commit {n} proposed commit(s).",
        )
    return typer.confirm("Apply these commits?", default=False)


def _empty_instructions(repo: Path) -> str:
    """Return empty instruction text; used to stub planner._load_instructions."""
    return ""


@contextmanager
def _no_instructions_patch(enabled: bool):
    """Skip instruction-file loading (AGENTS.md/.cursorrules/CLAUDE.md/README.md).

    Patches planner._load_instructions for the run so no instruction-file
    content is sent to the provider (IMPROVEMENTS 7.4).
    """
    if not enabled:
        yield
        return
    from . import planner as planner_mod

    orig = planner_mod._load_instructions
    planner_mod._load_instructions = _empty_instructions
    try:
        yield
    finally:
        planner_mod._load_instructions = orig


def _append_trailers(plan: CommitPlan, trailers: list[str]) -> CommitPlan:
    """Append trailer lines to each group message in place (4.6)."""
    if not trailers:
        return plan
    block = "\n".join(trailers)
    for group in plan.groups:
        if block and block not in group.message:
            group.message = f"{group.message}\n\n{block}"
    return plan


def _apply_with_trailers(
    git: GitClient, cfg: RunConfig, plan_path: Path | None, trailers: list[str]
) -> None:
    """Load the (latest or given) plan, append trailers, apply via apply_saved."""
    store = SessionStore(git)
    plan = store.load_plan_file(plan_path) if plan_path else store.load_latest_plan()
    if plan is None:
        raise AtcError("no saved plan found", hint="Run `atc` first to create a plan.")
    _append_trailers(plan, trailers)
    with tempfile.TemporaryDirectory(prefix="atc-trailers-") as d:
        tmp = Path(d) / "plan.json"
        tmp.write_text(plan.model_dump_json(), encoding="utf-8")
        flows.apply_saved(git, cfg, tmp)


def _sessions_data(store: SessionStore) -> list[Any]:
    """Prefer rich session metadata when the store exposes it (3.7)."""
    fn = getattr(store, "list_sessions_metadata", None)
    if callable(fn):
        try:
            data = fn()
            if data is not None:
                return data
        except Exception:  # noqa: BLE001 - fall back to plain id list
            pass
    return store.list_sessions()


def _snapshot_for_plan(
    store: SessionStore, path: Path | None, cfg: RunConfig, plan: CommitPlan
) -> WorktreeSnapshot:
    """Best-effort snapshot for a saved plan; falls back to a minimal stub."""
    if path is not None:
        sid = Path(path).parent.name
        try:
            snap = store.load_snapshot(sid)
            if snap is not None:
                return snap
        except Exception:  # noqa: BLE001 - fall back to stub snapshot
            pass
    return WorktreeSnapshot(
        repo_root=cfg.repo,
        branch="",
        head_sha=plan.base_head,
        fingerprint=plan.repo_fingerprint,
    )


def _collect_hunks(snapshot: WorktreeSnapshot, hunk_ids: list[str]) -> list[Any]:
    wanted = set(hunk_ids)
    out: list[Any] = []
    for f in snapshot.files:
        for h in f.hunks:
            if h.hunk_id in wanted:
                out.append(h)
    return out


# -- default command -------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    compact: bool = typer.Option(True, "--compact/--verbose", help="Compact (default) or verbose mode."),
    repo: Path = typer.Option(None, "--repo", help="Target Git repo. Defaults to the current directory."),
    paths: list[str] | None = typer.Option(None, "--paths", help="Limit planning to paths."),
    include_staged: bool = typer.Option(False, "--include-staged", help="Include staged changes."),
    allow_binary: bool = typer.Option(False, "--allow-binary", help="Allow safe binary file commits."),
    provider: str = typer.Option("openai-compatible", "--provider", help="openai-compatible|anthropic"),
    model: str | None = typer.Option(None, "--model", help="Model name."),
    base_url: str | None = typer.Option(None, "--base-url", help="Base URL for OpenAI-compatible providers."),
    api_key_env: str | None = typer.Option(None, "--api-key-env", help="Env var holding the provider API key."),
    max_chunk_tokens: int = typer.Option(32000, "--max-chunk-tokens", help="Advanced ceiling; ATC budgets requests automatically."),
    max_reducer_tokens: int = typer.Option(16000, "--max-reducer-tokens", help="Advanced ceiling; ATC budgets requests automatically."),
    direct_max_tokens: int = typer.Option(120000, "--full-change-limit", help="Use one full-change request up to this estimated input size."),
    temperature: float = typer.Option(0.0, "--temperature", help="Sampling temperature."),
    no_verify: bool = typer.Option(False, "--no-verify", help="Pass --no-verify to git commit."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts where safe."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
    debug: bool = typer.Option(False, "--debug", help="Keep extra session diagnostics."),
    amend: bool = typer.Option(False, "--amend", help="Re-plan the last commit's diff and rewrite it."),
    squash: bool = typer.Option(
        False, "--squash", help="Re-plan the last commit's diff and rewrite it as a SINGLE squash commit."
    ),
    trailer: list[str] | None = typer.Option(None, "--trailer", help="Append a trailer to every commit. Repeatable."),
    no_instructions: bool = typer.Option(False, "--no-instructions", help="Skip loading instruction files."),
    max_parallel: int | None = typer.Option(None, "--max-parallel", help="Cap the planner's thread pool size."),
    retry_attempts: int = typer.Option(3, "--retry-attempts", help="Per-request retry count for provider HTTP calls."),
    provider_timeout: float = typer.Option(180.0, "--provider-timeout", help="Network timeout for one provider request, in seconds."),
    deadline: float = typer.Option(600.0, "--time-limit", help="Total planning time limit in seconds. Work is cached for resume."),
    review_plan: bool = typer.Option(True, "--review/--no-review", help="Review the plan once for more useful atomic commits."),
    message_template: str | None = typer.Option(None, "--message-template", help="Custom commit-message template (${scope}, ${verb}, ${object})."),
    version: bool = typer.Option(None, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
) -> None:
    """Default command: plan, review, and commit in one run (with confirmation).

    Bare ``atc`` plans and commits the worktree. Without ``--yes`` it shows the
    proposed commits and asks before applying; with ``--json`` and no ``--yes`` it
    emits the plan as JSON without committing. To plan without committing, use
    ``atc plan``.

    Examples:

      atc                       # plan, then ask, then commit
      atc --yes                  # plan and commit without asking
      atc --verbose              # more detailed plan + commits
      atc --amend --yes          # rewrite the last commit as atomic commits
      atc --squash --yes         # rewrite the last commit as a single squash commit
      atc plan                   # dry-run plan only (no commits)
      atc --trailer "Co-authored-by: ..." --yes
    """

    cfg = RunConfig(
        mode="compact" if compact else "verbose",
        repo=repo or Path.cwd(),
        paths=list(paths or []),
        include_staged=include_staged,
        allow_binary=allow_binary,
        provider="anthropic" if provider == "anthropic" else "openai-compatible",
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
        max_chunk_tokens=max_chunk_tokens,
        max_reducer_tokens=max_reducer_tokens,
        direct_max_tokens=direct_max_tokens,
        temperature=temperature,
        provider_timeout=provider_timeout,
        no_verify=no_verify,
        yes=yes,
        json_output=json_output,
        debug=debug,
        max_parallel=max_parallel,
        retry_attempts=retry_attempts,
        deadline=deadline,
        review_plan=review_plan,
        message_template=message_template,
    )
    ctx.obj = cfg

    # If a subcommand was invoked, defer to it.
    if ctx.invoked_subcommand is not None:
        return

    trailers = list(trailer or [])
    try:
        with _no_instructions_patch(no_instructions):
            _run_default(cfg, amend=amend, squash=squash, trailers=trailers)
    except AtcError as exc:
        _exit_on_atc_error(exc, cfg)


def _print_token_usage(cfg: RunConfig) -> None:
    """Print cumulative token usage at the end of a run (2.5). Skip --json."""
    if cfg.json_output:
        return
    usage = get_total_usage()
    if not usage.get("total_tokens"):
        return
    console.print(f"[dim]{format_token_usage(usage)}[/dim]")


@contextmanager
def _bumped_committer_date(git: GitClient):
    """Bump ``GIT_COMMITTER_DATE`` past the current HEAD's committer timestamp.

    ``--amend`` / ``--squash`` reset HEAD~1 and re-commit the same diff. When the
    reset and the new commit land within the same wall-clock second, git
    produces an identical SHA (same tree, parents, author, committer) and the
    rewrite silently becomes a no-op. Forcing the committer date one second
    ahead of the original commit guarantees a distinct SHA every time.
    """
    orig_ts = git.run_text(["log", "-1", "--pretty=%ct"], check=False).strip()
    bumped = f"{int(orig_ts or 0) + 1} +0000" if orig_ts else None
    saved = os.environ.get("GIT_COMMITTER_DATE")
    if bumped:
        os.environ["GIT_COMMITTER_DATE"] = bumped
    try:
        yield
    finally:
        if bumped:
            if saved is None:
                os.environ.pop("GIT_COMMITTER_DATE", None)
            else:
                os.environ["GIT_COMMITTER_DATE"] = saved


def _run_default(
    cfg: RunConfig, *, amend: bool, squash: bool, trailers: list[str],
) -> None:
    """Dispatch the default (no-subcommand) behavior: plan and commit."""
    git = GitClient(cfg.repo)
    try:
        if amend:
            _run_rewrite(git, cfg, trailers, squash=False)
            return
        if squash:
            _run_rewrite(git, cfg, trailers, squash=True)
            return
        # Default: plan, confirm, commit. `atc plan` is the dry-run path.
        commit_plan = flows.dry_run(git, cfg)
        messages = [g.message for g in commit_plan.groups]
        # With --json and no --yes, stop at the plan (emit JSON, do not commit).
        # With --yes (or an interactive terminal), confirm then commit.
        if cfg.json_output and not cfg.yes:
            return
        if not _confirm_now(cfg, len(messages), messages):
            console.print("[yellow]aborted[/yellow]")
            return
        if trailers:
            _apply_with_trailers(git, cfg, None, trailers)
        else:
            flows.apply_saved(git, cfg, None)
    finally:
        _print_token_usage(cfg)


def _run_rewrite(git: GitClient, cfg: RunConfig, trailers: list[str], *, squash: bool) -> None:
    """Re-plan the last commit's diff and rewrite it (4.1).

    Resets HEAD~1 (mixed, keeping changes in the worktree), then re-plans. With
    ``squash=False`` (``--amend``) the result is committed as atomic commits;
    with ``squash=True`` (``--squash``) every planned group collapses into one
    commit rewriting HEAD. ``--amend``/``--squash`` reset HEAD~1 and re-commit the
    same diff; bumping the committer date avoids a same-second SHA collision.
    """
    flag = "--squash" if squash else "--amend"
    if not git.has_commits():
        raise AtcError(f"cannot {flag}: repository has no commits.")
    parent = git.run_text(["rev-parse", "--verify", "HEAD~1"], check=False).strip()
    if not parent:
        raise AtcError(f"cannot {flag}: HEAD has no parent (only one commit in repo).")
    if not cfg.yes:
        if cfg.json_output:
            raise AtcError(f"{flag} requires --yes (or run without --json).")
        if not err_console.is_terminal:
            raise AtcError(f"{flag} requires --yes (non-interactive terminal).")
        prompt = (
            "Rewrite the last commit by re-planning its diff into a SINGLE squash commit?"
            if squash
            else "Rewrite the last commit by re-planning its diff into atomic commits?"
        )
        if not typer.confirm(prompt, default=False):
            console.print("[yellow]aborted[/yellow]")
            return

    original_subject = git.run_text(["log", "-1", "--pretty=%s"], check=False).strip()
    orig_head = git.head_sha()
    with _bumped_committer_date(git):
        git.run_text(["reset", "--mixed", "HEAD~1"])
        commit_plan = flows.dry_run(git, cfg)
        if not commit_plan.groups:
            # Nothing safe to rewrite (last commit's diff was all
            # unsafe/excluded hunks). Restore HEAD and keep the changes in the
            # worktree for inspection instead of silently leaving the repo
            # reset one commit back.
            git.run_text(["reset", "--soft", orig_head])
            console.print("[yellow]nothing safe to rewrite; HEAD restored[/yellow]")
            return
        if squash:
            # Collapse every planned group into one commit rewriting HEAD.
            all_hunks = [hid for g in commit_plan.groups for hid in g.hunk_ids]
            all_paths = [p for g in commit_plan.groups for p in g.file_paths]
            commit_plan = CommitPlan(
                version=commit_plan.version,
                mode=commit_plan.mode,
                repo_fingerprint=commit_plan.repo_fingerprint,
                base_head=commit_plan.base_head,
                groups=[
                    CommitGroup(
                        group_id="squash",
                        message=original_subject or "squash: combine changes into one commit",
                        rationale="squashed from multiple atomic groups",
                        hunk_ids=all_hunks,
                        file_paths=all_paths,
                        risk="medium",
                    )
                ],
                excluded=commit_plan.excluded,
                warnings=commit_plan.warnings,
            )
        _append_trailers(commit_plan, trailers)
        with tempfile.TemporaryDirectory(prefix="atc-rewrite-") as d:
            tmp = Path(d) / "plan.json"
            tmp.write_text(commit_plan.model_dump_json(), encoding="utf-8")
            flows.apply_saved(git, cfg, tmp)


# -- subcommands -----------------------------------------------------------


@app.command(name="commit")
def commit_changes(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply the reviewed plan without asking."),
    hooks: str = typer.Option(
        "once", "--hooks", help="Hook policy: once (default), each, or skip."
    ),
) -> None:
    """Plan and create all useful atomic commits in one command.

    Runs pre-commit once before planning (when configured), includes staged
    and unstaged changes, and commits on confirmation. This is the same as the
    bare ``atc`` default but always includes staged changes and runs hooks.

    Examples:

      atc commit          # plan, ask, commit
      atc commit --yes     # plan and commit without asking
      atc commit --hooks each
    """
    cfg: RunConfig = ctx.obj
    if yes:
        cfg.yes = True
    cfg.include_staged = True
    if hooks not in {"once", "each", "skip"}:
        raise typer.BadParameter("must be once, each, or skip", param_hint="--hooks")
    if not cfg.no_verify:
        if hooks == "once":
            cfg.hook_mode = "once"
        elif hooks == "skip":
            cfg.hook_mode = "skip"
        else:
            cfg.hook_mode = "each"
    git = GitClient(cfg.repo)
    try:
        flows.prepare_hooks(git, cfg)
        plan = flows.dry_run(git, cfg)
        messages = [group.message for group in plan.groups]
        if not _confirm_now(cfg, len(messages), messages):
            console.print("[yellow]aborted[/yellow]")
            return
        flows.apply_saved(git, cfg, None)
    except AtcError as exc:
        _exit_on_atc_error(exc, cfg)


@app.command()
def plan(ctx: typer.Context) -> None:
    """Build a dry-run plan without committing (the old default behavior).

    Examples:

      atc plan          # plan only, no commits
      atc --verbose plan
      atc --json plan
    """
    cfg: RunConfig = ctx.obj
    try:
        flows.dry_run(GitClient(cfg.repo), cfg)
    except AtcError as exc:
        _exit_on_atc_error(exc, cfg)
    finally:
        _print_token_usage(cfg)


@app.command(name="apply")
def apply_saved_cmd(
    ctx: typer.Context,
    plan_path: Path | None = typer.Argument(None, help="Saved plan file (defaults to the latest plan)."),
) -> None:
    """Apply a previously saved plan.

    Examples:

      atc apply                       # apply the latest plan
      atc apply .git/atc/sessions/.../plan.json
    """
    cfg: RunConfig = ctx.obj
    try:
        flows.apply_saved(GitClient(cfg.repo), cfg, plan_path)
    except AtcError as exc:
        _exit_on_atc_error(exc, cfg)
    finally:
        _print_token_usage(cfg)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Validate git and provider configuration.

    Examples:

      atc doctor
      atc --json doctor
    """
    cfg: RunConfig = ctx.obj
    git = GitClient(cfg.repo)
    try:
        _print_doctor(_doctor_diag(git, cfg), as_json=cfg.json_output)
    except AtcError as exc:
        _exit_on_atc_error(exc, cfg)


@app.command()
def resume(ctx: typer.Context) -> None:
    """Resume the latest interrupted session and apply remaining groups.

    Examples:

      atc resume
      atc --json resume
    """
    cfg: RunConfig = ctx.obj
    try:
        flows.resume(GitClient(cfg.repo), cfg)
    except AtcError as exc:
        _exit_on_atc_error(exc, cfg)


@app.command()
def sessions(ctx: typer.Context) -> None:
    """List recorded sessions.

    Examples:

      atc sessions
      atc --json sessions
    """
    cfg: RunConfig = ctx.obj
    store = SessionStore(GitClient(cfg.repo))
    data = _sessions_data(store)
    if cfg.json_output:
        _emit_json({"sessions": data})
        return
    print_sessions(data)


@app.command(name="show-plan")
def show_plan(
    ctx: typer.Context,
    path: Path | None = typer.Argument(None),
    json_output: bool = typer.Option(False, "--json", help="Print the raw plan JSON."),
    show_diff: bool = typer.Option(False, "--show-diff", help="Include hunk diffs in the rendered plan."),
) -> None:
    """Pretty-print a saved plan.

    Examples:

      atc show-plan
      atc show-plan .git/atc/sessions/20240101-120000-abc/plan.json
      atc show-plan --json
      atc show-plan --show-diff
    """
    cfg: RunConfig = ctx.obj
    store = SessionStore(GitClient(cfg.repo))
    plan = store.load_plan_file(path) if path else store.load_latest_plan()
    if plan is None:
        if cfg.json_output or json_output:
            _emit_json_error("no saved plan found")
        else:
            console.print("[yellow]no saved plan found[/yellow]")
        raise typer.Exit(code=1)
    if cfg.json_output or json_output:
        _emit_json(plan.model_dump())
        return
    snapshot = _snapshot_for_plan(store, path, cfg, plan)
    # Only pass show_diff when requested so the call stays valid even before
    # the output module lands the show_diff kwarg (CONTRACT: print_plan).
    if show_diff:
        print_plan(plan, snapshot, show_diff=True)
    else:
        print_plan(plan, snapshot)


@app.command()
def init(ctx: typer.Context) -> None:
    """Interactive config wizard: prompt for provider/model/api-key-env/base_url, write config, run doctor.

    Examples:

      atc init
    """
    cfg: RunConfig = ctx.obj
    try:
        if cfg.json_output:
            raise AtcError("atc init is interactive and cannot run with --json.")
        provider = typer.prompt("Provider", default="openai-compatible")
        if provider not in ("openai-compatible", "anthropic"):
            raise AtcError(
                f"unknown provider: {provider}",
                hint="Choose 'openai-compatible' or 'anthropic'.",
            )
        default_model = "gpt-4o-mini" if provider == "openai-compatible" else "claude-3-5-sonnet-latest"
        model = typer.prompt("Model", default=default_model)
        default_key_env = (
            "ATC_OPENAI_API_KEY" if provider == "openai-compatible" else "ATC_ANTHROPIC_API_KEY"
        )
        api_key_env = typer.prompt("API key env var", default=default_key_env)
        default_base = (
            "https://api.openai.com/v1" if provider == "openai-compatible" else "https://api.anthropic.com"
        )
        base_url = typer.prompt("Base URL", default=default_base)
        try:
            from .config import init_config
        except ImportError as exc:  # pragma: no cover - backend provided by config module
            raise AtcError(
                "init_config is not available in this build.",
                hint="The config wizard backend (init_config) has not been wired yet.",
            ) from exc
        try:
            init_config(
                provider=provider,
                model=str(model or default_model),
                api_key_env=api_key_env,
                base_url=base_url or None,
            )
        except TypeError as exc:
            raise AtcError(
                "init_config rejected the provided arguments.",
                hint="The config wizard backend signature may differ in this build.",
            ) from exc
        console.print("[green]config written[/green]")
        git = GitClient(cfg.repo)
        # Run doctor against the values just written, not the stale CLI flags
        # (cfg.model/base_url are None when the user answered the prompts above
        # rather than passing --model/--base-url).
        diag_cfg = RunConfig(
            repo=cfg.repo,
            provider=("anthropic" if provider == "anthropic" else "openai-compatible"),
            model=str(model or default_model),
            base_url=base_url or None,
            api_key_env=api_key_env,
        )
        _print_doctor(_doctor_diag(git, diag_cfg), as_json=False)
    except AtcError as exc:
        _exit_on_atc_error(exc, cfg)


@app.command()
def undo(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Session id whose backup.patch to reverse."),
) -> None:
    """Reverse a session's backup.patch via `git apply --reverse` (3.5).

    Examples:

      atc undo 20240101-120000-abc
      atc --yes undo 20240101-120000-abc
    """
    cfg: RunConfig = ctx.obj
    git = GitClient(cfg.repo)
    store = SessionStore(git)
    patch_path = store.session_path(session_id) / "backup.patch"
    if not patch_path.is_file():
        msg = f"no backup.patch for session {session_id}"
        if cfg.json_output:
            _emit_json_error(msg)
        else:
            err_console.print(f"[red]error:[/red] {msg}")
        raise typer.Exit(code=1)
    if not cfg.yes:
        if cfg.json_output:
            raise AtcError("atc undo requires --yes (or run without --json).")
        if not err_console.is_terminal:
            raise AtcError("atc undo requires --yes (non-interactive terminal).")
        if not typer.confirm(
            f"Reverse the changes in {patch_path.name} (git apply --reverse)?",
            default=False,
        ):
            console.print("[yellow]aborted[/yellow]")
            return
    cwd = str(git.toplevel()) if git.is_git_repo() else str(cfg.repo)
    proc = subprocess.run(
        ["git", "apply", "--reverse", str(patch_path)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        msg = proc.stderr.strip() or "git apply --reverse failed"
        if cfg.json_output:
            _emit_json_error(msg)
        else:
            err_console.print(f"[red]error:[/red] {msg}")
        raise typer.Exit(code=1)
    if cfg.json_output:
        _emit_json({"ok": True, "session": session_id, "patch": str(patch_path)})
    else:
        console.print(f"[green]reversed[/green] {patch_path}")


@app.command()
def selftest(
    ctx: typer.Context,
    cases: int = typer.Option(1, "--cases", help="Number of repro cases to run."),
    seed: int = typer.Option(0, "--seed", help="Base seed for case generation."),
    live: bool = typer.Option(False, "--live", help="Use the configured live provider."),
) -> None:
    """Run the deterministic self-test harness against the install (4.4).

    Examples:

      atc selftest
      atc selftest --cases 5 --seed 42
      atc selftest --live --cases 2
    """
    cfg: RunConfig = ctx.obj
    # Keep inner runs quiet (no json/debug) so the outer --json output stays clean.
    clean_cfg = RunConfig(
        mode=cfg.mode,
        repo=cfg.repo,
        provider=cfg.provider,
        model=cfg.model,
        base_url=cfg.base_url,
        api_key_env=cfg.api_key_env,
        api_key=cfg.api_key,
        max_chunk_tokens=cfg.max_chunk_tokens,
        max_reducer_tokens=cfg.max_reducer_tokens,
        direct_max_tokens=cfg.direct_max_tokens,
        temperature=cfg.temperature,
        provider_timeout=cfg.provider_timeout,
        retry_attempts=cfg.retry_attempts,
        deadline=cfg.deadline,
        review_plan=cfg.review_plan,
        no_verify=cfg.no_verify,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="atc-selftest-") as work_dir:
            summary = run_selftest(
                work_dir=Path(work_dir),
                cfg_template=clean_cfg,
                cases=cases,
                seed=seed,
                live=live,
            )
    except AtcError as exc:
        _exit_on_atc_error(exc, cfg)
        return
    if cfg.json_output:
        _emit_json(
            {
                "cases_run": summary.cases_run,
                "failures": len(summary.failures),
                "log_path": str(summary.log_path),
                "failed": [
                    {
                        "case_id": f.case_id,
                        "status": f.status,
                        "error": f.error,
                        "planned_commits": f.planned_commits,
                        "committed": f.committed,
                    }
                    for f in summary.failures
                ],
            }
        )
        return
    console.print(f"[bold]atc selftest[/bold] cases={summary.cases_run}")
    if summary.failures:
        console.print(f"[red]{len(summary.failures)} failure(s)[/red]")
        for f in summary.failures:
            console.print(f"  - {f.case_id}: {f.error}")
        console.print(f"failures logged to {summary.log_path}")
        raise typer.Exit(code=1)
    console.print("[green]all cases passed[/green]")


@app.command()
def explain(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Group id to explain."),
    path: Path | None = typer.Argument(None, help="Saved plan file (defaults to the latest plan)."),
) -> None:
    """Print the rationale and hunk diffs for one group from a saved plan (4.5).

    Examples:

      atc explain selftest-1
      atc explain selftest-1 .git/atc/sessions/20240101-120000-abc/plan.json
      atc --json explain selftest-1
    """
    cfg: RunConfig = ctx.obj
    store = SessionStore(GitClient(cfg.repo))
    plan = store.load_plan_file(path) if path else store.load_latest_plan()
    if plan is None:
        if cfg.json_output:
            _emit_json_error("no saved plan found")
        else:
            err_console.print("[red]error:[/red] no saved plan found")
        raise typer.Exit(code=1)
    group = next((g for g in plan.groups if g.group_id == group_id), None)
    if group is None:
        msg = f"group {group_id} not found in plan"
        if cfg.json_output:
            _emit_json_error(msg)
        else:
            err_console.print(f"[red]error:[/red] {msg}")
        raise typer.Exit(code=1)
    snapshot = _snapshot_for_plan(store, path, cfg, plan)
    hunks = _collect_hunks(snapshot, group.hunk_ids)
    if cfg.json_output:
        _emit_json({"group": group.model_dump(), "hunks": [h.model_dump() for h in hunks]})
        return
    console.print(f"[bold]{group.message}[/bold]")
    console.print(f"group: {group.group_id}")
    if group.rationale:
        console.print(f"why: {group.rationale}")
    console.print(f"hunks: {', '.join(group.hunk_ids)}")
    if not hunks:
        console.print("[dim](no hunk patches available; pass a plan path under a session dir)[/dim]")
        return
    for h in hunks:
        console.print(f"\n[bold]--- {h.file_path} {h.header}[/bold]")
        console.print(h.patch)


if __name__ == "__main__":  # pragma: no cover
    app()
