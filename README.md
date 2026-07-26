# atc — Atomic Commits CLI

`atc` turns the safe changes in a dirty Git worktree into a reviewed sequence of
meaningful, atomic commits. It freezes the current change set, builds a local
change graph, asks an AI provider for a plan, validates exact hunk coverage, and
then stages and commits one group at a time.

It commits locally only. It does not push, rewrite history unless explicitly
asked, edit source files, or format code.

## Requirements

- Python 3.11 or newer
- Git available on `PATH`
- A repository with at least one commit
- An OpenAI-compatible or Anthropic API key

## Install

From a checkout with `pipx`:

```bash
pipx install .
atc --version
```

From a checkout:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
atc --version
```

## Configure a provider

The fastest setup is the interactive wizard:

```bash
atc init
```

`atc` supports `openai-compatible` and `anthropic`. Values are resolved in
this order:

1. CLI options
2. Environment variables
3. The first config file found: `./.atc.toml`, then
   `~/.config/atc/config.toml`
4. Built-in defaults

A repo-local config therefore replaces, rather than merges with, the global
config for that run.

### Environment variables

```bash
# OpenAI-compatible
export ATC_OPENAI_API_KEY=sk-...
export ATC_OPENAI_MODEL=gpt-4o-mini
export ATC_OPENAI_BASE_URL=https://api.openai.com/v1

# Anthropic
export ATC_ANTHROPIC_API_KEY=sk-ant-...
export ATC_ANTHROPIC_MODEL=claude-3-5-sonnet-latest
export ATC_ANTHROPIC_BASE_URL=https://api.anthropic.com

# Optional split-review default
export ATC_REVIEW=true
```

Planner limits and retry controls are exposed as global CLI options. The
configuration resolver can also read `ATC_PROVIDER_TIMEOUT`,
`ATC_RETRY_ATTEMPTS`, `ATC_FULL_CHANGE_LIMIT`, and `ATC_TIME_LIMIT` when its
caller leaves those values unset; the CLI currently supplies the defaults
shown by `atc --help`.

### Config file

Provider table names may use a hyphen or underscore. For example,
`[openai-compatible]` and `[openai_compatible]` are equivalent.

```toml
[openai-compatible]
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "ATC_OPENAI_API_KEY"
message_template = "${scope}: ${verb} ${object}"
```

An `api_key` may be stored inline, but `api_key_env` is safer. Config files
created by `atc init` are owner-readable only.

## Quick start

```bash
atc                 # plan, show the plan, confirm, and commit
atc --yes           # plan and commit without confirmation
atc plan            # save and display a plan without committing
atc apply            # apply the latest saved plan
atc commit           # include staged changes and run pre-commit once
atc commit --yes     # same, without confirmation
```

Bare `atc` rejects an already-staged index unless `--include-staged` is set.
`atc commit` always includes staged and unstaged changes and defaults to
`--hooks once`.

With `--json`, global options must come before the subcommand. Bare
`atc --json` prints the plan and does not commit unless `--yes` is also passed.

## Commands

| Command | Purpose |
|---|---|
| `atc` | Plan, confirm, and apply the current safe changes. |
| `atc plan` | Build and save a dry-run plan. |
| `atc apply [PLAN_PATH]` | Apply the latest or a specified saved plan. |
| `atc commit` | Include staged changes and handle hooks before planning. |
| `atc doctor` | Locally check Git, current CLI provider/model/base URL values, and API-key environment presence. |
| `atc sessions` | List session IDs, status, and commit counts. |
| `atc show-plan [PATH]` | Render a saved plan; add `--show-diff` or `--json`. |
| `atc explain GROUP_ID [PATH]` | Show one group's rationale and hunk diffs. |
| `atc resume` | Continue the latest incomplete session. |
| `atc undo SESSION_ID` | Reverse that session's `backup.patch`. |
| `atc selftest` | Exercise the deterministic local harness. |
| `atc init` | Write provider config interactively and run diagnostics. |

### `atc init` — config wizard

`atc init` walks you through first-time setup interactively: it prompts for your
provider, model, base URL, and the env var that holds your API key, writes
`~/.config/atc/config.toml`, and runs `atc doctor` to verify the result. Use it
instead of hand-editing the config file on a fresh install.

```bash
atc init
```

### Validate setup

```bash
atc doctor
```

## Quickstart

```bash
atc              # plan, ask, then commit (the default one-command flow)
atc --yes        # plan and commit without asking
atc --verbose    # more detailed plan + commits
atc plan         # dry-run plan only (no commits)
atc apply        # apply the latest saved plan
atc commit       # same as bare `atc`, but also runs pre-commit and includes staged changes
atc commit --yes # plan + commit without asking, with pre-commit
```

Bare `atc` is the opinionated one-command: it plans, shows the proposed
commits, and commits them on confirmation. With `--json` and no `--yes` it
emits the plan as JSON without committing. Use `atc plan` for the old dry-run
behavior. `atc commit` adds pre-commit checks and includes already-staged
changes; use `--hooks each` for custom or per-message workflows, or `--hooks
skip` when checks already ran elsewhere.

## How planning works

1. Freeze one exact worktree snapshot.
2. Split nearby unrelated edits into separately stageable changed regions.
3. Build local facts about symbols, imports, tests, docs, migrations, and renames.
4. Send ordinary changes to one full-change model request.
5. For very large changes, gather evidence in parallel and give all notes to one lead planner.
6. Review every group once to find more honest splits.
7. Check exact coverage and order, then stage and commit each group.

No AI request runs while files are being edited. Unchanged planning results are cached,
so a failed or interrupted command can reuse completed work.

`--compact` and `--verbose` only control display detail. Both seek the greatest useful
number of commits.

## Progress during long runs

Interactive runs show the frozen snapshot size, local links found, chosen plan method,
estimated input size, elapsed request time, evidence progress, split review, validation,
and commit progress. Provider calls share the total `--time-limit`; retries cannot silently
multiply it into hours.

## Safety model

`atc` never commits `.env`, ignored files, caches, or runtime artifacts. Binary files are refused by default. It uses no zero-context patches and makes no broad fallback commits.

## Instruction files

When planning, `atc` reads up to 4000 characters (total) from the following
files in your repo root, in order, and sends the collected content to the
provider as context so commit messages can follow your project's conventions:

- `AGENTS.md`
- `.cursorrules`
- `CLAUDE.md`
- `README.md`

These are treated as untrusted content (never executed). If you do not want any
of these files sent to the provider, opt out with:

```bash
atc --no-instructions
```

## Recovery / resume

```bash
atc resume       # resume latest interrupted session
atc sessions     # list sessions
atc undo <id>    # reverse a session's backup.patch (with confirmation)
```

A `backup.patch` is written before applying, for manual recovery.

## Examples

### `resume` — continue an interrupted apply

```bash
atc resume                 # resume the latest incomplete session
atc resume --json          # machine-readable result on stdout
```

### `sessions` — inspect saved sessions

```bash
atc sessions               # list sessions with id, status, and commit count
```

### `show-plan` — review a saved plan

```bash
atc show-plan              # pretty-print the latest saved plan
atc show-plan path/to/plan.json   # pretty-print a specific plan file
atc show-plan --json       # emit the raw plan JSON
```

### `init` — first-time setup wizard

```bash
atc init                   # prompt for provider/model/key, write config, run doctor
```

### `selftest` — validate your install and provider

```bash
atc selftest               # run the deterministic local harness
atc selftest --cases 20 --seed 7   # reproduce a specific scenario
atc selftest --live                # also exercise the configured provider
```

### `undo` — roll back a session

```bash
atc undo <session-id>      # reverse the session's backup.patch (prompts first)
atc undo <session-id> --yes # skip the confirmation prompt
```

## Important

`atc` commits locally only. It never pushes, never rewrites history, and never edits or formats your code.

## Further reading

- `docs/implementation.md` — full product spec and design.
- `docs/operation.md` — build progress log.
- `CHANGELOG.md` — notable behavior changes.
