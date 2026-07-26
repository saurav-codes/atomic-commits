# atc — Atomic Commits CLI

`atc` turns one dirty Git worktree into the greatest useful number of meaningful,
atomic commits. Local code analysis records symbols, tests, imports, and related
changes without spending AI tokens. A strong model then makes the final commit plan.

> Status: under active construction. See `docs/implementation.md` for the full spec
> and `docs/operation.md` for build progress.

## Why atomic commits

Small, behavior-scoped commits make history reviewable, bisectable, and revertible.
`atc` keeps splitting while each result remains useful and complete. It never creates
empty commits or arbitrary fragments just to raise the count.

## Install

```bash
pipx install .
atc --version
```

## Provider setup

`atc` supports two providers: `openai-compatible` (any OpenAI-compatible endpoint)
and `anthropic`. Configuration is resolved in this order (highest first):

1. CLI flags (`--model`, `--base-url`, `--api-key-env`, `--provider`)
2. Environment variables
3. Config file (`.atc.toml` or `~/.config/atc/config.toml`) — see [Config file](#config-file)
4. Built-in defaults (errors with a setup hint if a required value is missing)

### Environment variables

OpenAI-compatible:

```bash
export ATC_OPENAI_API_KEY=sk-...
export ATC_OPENAI_BASE_URL=https://api.openai.com/v1
export ATC_OPENAI_MODEL=gpt-4o-mini
export ATC_TIME_LIMIT=600
export ATC_FULL_CHANGE_LIMIT=120000
```

Anthropic:

```bash
export ATC_ANTHROPIC_API_KEY=sk-ant-...
export ATC_ANTHROPIC_MODEL=claude-3-5-sonnet-latest
```

### Config file

Instead of (or in addition to) environment variables, `atc` reads a TOML config
file from the first of these locations that exists:

- `.atc.toml` in the current working directory (repo-local), and
- `~/.config/atc/config.toml` (user-global).

The file has one top-level table per provider. Both the hyphenated and the
underscored table names are accepted — `[openai-compatible]` and
`[openai_compatible]` are equivalent, and likewise `[anthropic]`.

```toml
# ~/.config/atc/config.toml
[openai-compatible]
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key = "sk-..."                 # inline key (prefer api_key_env for safety)
api_key_env = "ATC_OPENAI_API_KEY"  # env var to read the key from
message_template = "${scope}: ${verb} ${object}"  # optional commit-message template

[anthropic]
model = "claude-3-5-sonnet-latest"
base_url = "https://api.anthropic.com"
api_key_env = "ATC_ANTHROPIC_API_KEY"
message_template = "${scope}: ${verb} ${object}"
```

Supported keys (each key is `snake_case` only; only the table name accepts both
the hyphenated and underscored spellings):

| Key | Description |
|-----|-------------|
| `model` | Model name sent to the provider. |
| `base_url` | Provider API base URL. OpenAI-compatible defaults to `https://api.openai.com/v1`; Anthropic defaults to `https://api.anthropic.com`. |
| `api_key` | Inline API key. Avoid committing this to a repo; prefer `api_key_env`. |
| `api_key_env` | Name of the environment variable holding the API key (e.g. `ATC_OPENAI_API_KEY`). |
| `message_template` | Optional commit-message template; placeholders like `${scope}`, `${verb}`, `${object}` are filled by the planner. |
| `time_limit` | Total planning time in seconds. Completed work is cached for retry. |
| `full_change_limit` | Estimated input-token limit for using one full-change request. |

Repo-local `.atc.toml` overrides the user-global file. CLI flags override both.
A malformed config file is reported with the file path and parse error rather
than being silently ignored.

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
