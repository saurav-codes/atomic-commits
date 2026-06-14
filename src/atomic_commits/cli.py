"""Typer CLI for atc.

Defines the full command surface (default plan/apply behavior plus the
doctor, resume, sessions, and show-plan subcommands) and delegates the actual
work to the flows module.
"""

from __future__ import annotations

from pathlib import Path

import typer

from . import __version__, flows
from .config import RunConfig
from .errors import AtcError
from .git_client import GitClient
from .logging import configure, console, err_console
from .output import print_sessions
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


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    compact: bool = typer.Option(True, "--compact/--verbose", help="Compact (default) or verbose mode."),
    apply: bool = typer.Option(False, "--apply", help="Apply a previously generated valid plan."),
    now: bool = typer.Option(False, "--now", help="With --apply, plan and commit in one run."),
    plan: Path | None = typer.Option(None, "--plan", help="Use a specific plan file."),
    repo: Path = typer.Option(None, "--repo", help="Target Git repo. Defaults to the current directory."),
    paths: list[str] | None = typer.Option(None, "--paths", help="Limit planning to paths."),
    include_staged: bool = typer.Option(False, "--include-staged", help="Include staged changes."),
    allow_binary: bool = typer.Option(False, "--allow-binary", help="Allow safe binary file commits."),
    provider: str = typer.Option("openai-compatible", "--provider", help="openai-compatible|anthropic"),
    model: str | None = typer.Option(None, "--model", help="Model name."),
    base_url: str | None = typer.Option(None, "--base-url", help="Base URL for OpenAI-compatible providers."),
    api_key_env: str | None = typer.Option(None, "--api-key-env", help="Env var holding the provider API key."),
    max_chunk_tokens: int = typer.Option(6000, "--max-chunk-tokens", help="Chunk budget for map phase."),
    max_reducer_tokens: int = typer.Option(4000, "--max-reducer-tokens", help="Summary budget for reduce phase."),
    temperature: float = typer.Option(0.0, "--temperature", help="Sampling temperature."),
    no_verify: bool = typer.Option(False, "--no-verify", help="Pass --no-verify to git commit."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts where safe."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
    debug: bool = typer.Option(False, "--debug", help="Keep extra session diagnostics."),
    version: bool = typer.Option(None, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
) -> None:
    """Default command. With no subcommand, builds a dry-run plan (or applies with --apply)."""
    configure(debug=debug)

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
        temperature=temperature,
        no_verify=no_verify,
        yes=yes,
        json_output=json_output,
        debug=debug,
    )
    ctx.obj = cfg

    # If a subcommand was invoked, defer to it.
    if ctx.invoked_subcommand is not None:
        return

    try:
        _run_default(cfg, apply=apply, now=now, plan=plan)
    except AtcError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=exc.exit_code) from exc


def _run_default(cfg: RunConfig, *, apply: bool, now: bool, plan: Path | None) -> None:
    """Dispatch the default (no-subcommand) behavior."""
    git = GitClient(cfg.repo)
    if apply and now:
        flows.apply_now(git, cfg)
    elif apply:
        flows.apply_saved(git, cfg, plan)
    else:
        flows.dry_run(git, cfg)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Validate git and provider configuration."""
    cfg: RunConfig = ctx.obj
    git = GitClient(cfg.repo)
    console.print("[bold]atc doctor[/bold]")
    for line in flows.doctor(git, cfg):
        ok = not (line.endswith("MISSING") or "NOT" in line or "NOT SET" in line)
        marker = "[green]ok[/green]" if ok else "[red]check[/red]"
        console.print(f"{marker} {line}")


@app.command()
def resume(ctx: typer.Context) -> None:
    """Resume latest interrupted session."""
    cfg: RunConfig = ctx.obj
    try:
        flows.resume(GitClient(cfg.repo), cfg)
    except AtcError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=exc.exit_code) from exc


@app.command()
def sessions(ctx: typer.Context) -> None:
    """List sessions."""
    cfg: RunConfig = ctx.obj
    store = SessionStore(GitClient(cfg.repo))
    print_sessions(store.list_sessions())


@app.command(name="show-plan")
def show_plan(ctx: typer.Context, path: Path | None = typer.Argument(None)) -> None:
    """Pretty-print a saved plan."""
    cfg: RunConfig = ctx.obj
    store = SessionStore(GitClient(cfg.repo))
    plan = store.load_plan_file(path) if path else store.load_latest_plan()
    if plan is None:
        console.print("[yellow]no saved plan found[/yellow]")
        raise typer.Exit(code=1)
    console.print_json(plan.model_dump_json())


if __name__ == "__main__":  # pragma: no cover
    app()
