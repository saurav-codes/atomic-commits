# atc — Atomic Commits CLI

`atc` turns the safe changes in a dirty Git worktree into a reviewed sequence of
meaningful, atomic commits. It freezes the worktree, builds a local change
graph, asks an AI provider for a plan, validates exact hunk coverage, then
stages and commits one group at a time.

It only commits locally. It does not push, rewrite history unless asked, edit
source, or format code.

## Demo

Watch `atc` turn a dirty worktree into reviewed atomic commits:

<https://x.com/saurav__codes/status/2081345535701877241>

## Requirements

- Python 3.11 or newer
- Git on `PATH`
- A repo with at least one commit
- An OpenAI-compatible or Anthropic API key

## Install

```bash
pipx install .
atc --version
```

Or from a checkout:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
atc --version
```

## Configure

The fastest path is the wizard:

```bash
atc init
```

`atc` supports `openai-compatible` and `anthropic`. Values resolve in order:

1. CLI options
2. Environment variables
3. First config file found: `./.atc.toml`, then `~/.config/atc/config.toml`
4. Built-in defaults

A repo config replaces the global config for that run.

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

Planner limits and retry knobs can also be set via `ATC_PROVIDER_TIMEOUT`,
`ATC_RETRY_ATTEMPTS`, `ATC_FULL_CHANGE_LIMIT`, and `ATC_TIME_LIMIT`.

### Config file

Provider table names accept a hyphen or underscore: `[openai-compatible]` and
`[openai_compatible]` are equal.

```toml
[openai-compatible]
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "ATC_OPENAI_API_KEY"
message_template = "${scope}: ${verb} ${object}"
```

`api_key_env` is safer than an inline `api_key`. Files written by `atc init` are
owner-readable only.

## Quick start

```bash
atc                 # plan, confirm, commit
atc --yes           # plan and commit without asking
atc plan            # plan only
atc apply           # apply the latest saved plan
atc commit          # include staged + run pre-commit once
atc commit --yes    # same, no confirmation
```

Bare `atc` rejects a staged index unless `--include-staged` is set. `atc commit`
always includes staged and defaults to `--hooks once`.

With `--json`, global options go before the subcommand. `atc --json` prints the
plan and does not commit unless `--yes` is also set.

## Commands

| Command | Purpose |
|---|---|
| `atc` | Plan, confirm, and commit safe changes. |
| `atc plan` | Build and save a plan. |
| `atc apply [PLAN_PATH]` | Apply the latest or a given saved plan. |
| `atc commit` | Include staged changes and run hooks before planning. |
| `atc doctor` | Local check of git, provider, model, base URL, and API-key env presence. |
| `atc sessions` | List session IDs, status, and commit counts. |
| `atc show-plan [PATH]` | Render a saved plan. Add `--show-diff` or `--json`. |
| `atc explain GROUP_ID [PATH]` | Show one group's rationale and hunk diffs. |
| `atc resume` | Continue the latest incomplete session. |
| `atc undo SESSION_ID` | Reverse that session's `backup.patch`. |
| `atc selftest` | Run the deterministic local harness. |
| `atc init` | Write provider config interactively and run doctor. |

Run `atc --help` or `atc COMMAND --help` for the full option list.

`doctor` is hermetic: it never reads config files or calls a provider. `atc init`
runs it with the values just entered.

## Workflows

### Limit scope

`--paths` is repeatable and takes files or dirs relative to the repo root:

```bash
atc --paths src/atomic_commits --paths tests plan
```

### Inspect before applying

```bash
atc plan
atc show-plan --show-diff
atc explain group-id
atc apply
```

`atc apply` rescans and refuses a saved plan whose fingerprint no longer matches.

### Hooks

```bash
atc commit --hooks once   # default: run standard pre-commit once
atc commit --hooks each   # let Git run hooks for every commit
atc commit --hooks skip   # skip hooks
```

With `once`, `atc` runs a standard `pre-commit` hook against the safe changed
files before planning, retrying up to three times if files change. Custom
pre-commit hooks and active `commit-msg` hooks run through Git on every
commit. `--no-verify` skips all hooks.

### Rewrite the last commit

```bash
atc --amend          # re-plan HEAD's diff into atomic commits
atc --squash         # re-plan HEAD's diff as one commit
atc --amend --yes    # non-interactive
```

Both reset `HEAD~1` mixed and recreate the last commit's changes. They need a
HEAD with a parent and confirm unless `--yes` is set.

### Trailers and templates

```bash
atc --trailer "Co-authored-by: A User <a@example.com>"
atc --message-template '${scope}: ${verb} ${object}'
```

`--trailer` is repeatable. Templates support `${scope}`, `${verb}`, `${object}`
and can also be set per provider in the config file.

### Resume or recover

```bash
atc sessions
atc resume
atc undo 20260726-120000-abcdef
```

Before applying, `atc` stores a binary-safe worktree patch. `resume` checks that
every remaining planned hunk still exists. `undo` runs `git apply --reverse` on
the session backup; it does not reset commits already made.

## Safety

By default `atc` excludes common secret, env, VCS, dependency, cache, build,
database, and log paths. Sample env files like `.env.example` are allowed.
Binary changes are rejected unless `--allow-binary` is set, and even then only
known asset extensions are accepted.

A repo-root `.atcallow` can override the path denylist with one glob per line:

```gitignore
fixtures/example.key
generated/reference/**
```

Treat `.atcallow` as security-sensitive: it can allow paths that are denied by
default. Git-ignored untracked files are not seen by the scanner.

Apply guards:

1. A saved plan must match the current worktree fingerprint.
2. Every safe hunk must appear exactly once.
3. Staging rematches content fingerprints and rejects extra paths.
4. A failure unstages touched paths and stops. No broad fallback commit.

## Repository instructions

Unless `--no-instructions` is set, the planner sends up to 4,000 characters
total from these repo-root files, in this order:

1. `AGENTS.md`
2. `.cursorrules`
3. `CLAUDE.md`
4. `README.md`

They are context only and never executed.

## Session data

State lives in the target repo's Git directory:

```text
.git/atc/
├── lock
├── plan.json
└── sessions/
    ├── cache/
    │   ├── chunk-*.json
    │   └── plans/
    └── <timestamp>-<random>/
        ├── snapshot.json
        ├── plan.json
        ├── backup.patch
        └── apply_log.json
```

Writes are locked and JSON files are replaced atomically. Session and backup
files are owner-readable only.

## JSON output

Use `--json` before the subcommand:

```bash
atc --json plan
atc --json sessions
atc --json show-plan
atc --json explain group-id
```

JSON mode suppresses interactive progress. Errors are emitted as JSON with a
non-zero exit code. `atc init` is interactive and does not support JSON.
Destructive paths like `undo` and history rewrites require `--yes` in JSON or
non-interactive terminals.

## Further reading

- [Architecture](docs/architecture.md)
- [Development](docs/development.md)
- [Changelog](CHANGELOG.md)
