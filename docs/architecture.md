# Architecture

`atc` is a Python CLI. It scans a Git worktree, asks an AI provider for a commit
plan, then stages and commits one group at a time. Code is the source of truth;
this file is a short map.

## Modules

All live under `src/atomic_commits/`.

| Module | Job |
|---|---|
| `cli.py` | Typer commands and flags. Builds `RunConfig`, dispatches to `flows.py`. |
| `config.py` | `RunConfig`, config file and env resolution, `atc init` writer. |
| `flows.py` | Preflight, hooks, plan/apply/resume orchestration. |
| `scanner.py` | Read Git state into a frozen `WorktreeSnapshot`. |
| `safety.py` | Denylist paths, `.atcallow` overrides, binary rules. |
| `diff_parser.py` | Parse unified diffs and rebuild selected-hunk patches. |
| `fingerprints.py` | Hash hunks and the whole worktree for stable identity. |
| `change_analysis.py` | Local graph of symbols, imports, tests. No AI call. |
| `planner.py` | Build context pack, call provider, validate the plan. |
| `prompts.py` | System and user prompt text. |
| `providers/base.py` | `AIProvider` protocol, shared HTTP/retry/usage helpers. |
| `providers/openai_compatible.py` | OpenAI SDK with streaming preview. |
| `providers/anthropic.py` | Anthropic over plain `urllib`. |
| `validators/plan_schema.py` | Final plan shape and coverage checks. |
| `validators/commit_messages.py` | Per-message rules. |
| `stager.py` | Rematch planned hunks and stage them exactly. |
| `committer.py` | Loop: stage, commit, rescan, repeat per group. |
| `session.py` | Locked, atomic JSON state under `.git/atc/`. |
| `git_client.py` | Typed wrapper around `git` subprocesses. |
| `output.py` | Rich display and JSON output. |
| `errors.py` | Shared error types with hints. |
| `selftest.py` | Deterministic local harness, no provider needed. |
| `models.py` | Pydantic contracts shared by all modules. |

## Flow

```text
cli.py  ->  flows.preflight  ->  scanner.scan  ->  planner.plan  ->  validators
                                 SessionStore writes snapshot + plan
cli.py  ->  flows.apply_saved  ->  scanner.scan  ->  ensure_applicable_plan
                                 Committer.apply: stager -> commit -> rescan (per group)
                                 SessionStore writes apply_log
```

- Bare `atc`: plan, confirm, commit.
- `atc plan`: plan only.
- `atc apply`: apply a saved plan.
- `atc resume`: continue the latest incomplete session.
- `atc commit`: include staged, run pre-commit once, then commit.
- `atc --amend` / `--squash`: reset `HEAD~1`, re-plan, rewrite.

## State

```text
.git/atc/
├── lock                 flock for writes
├── plan.json            latest plan pointer
└── sessions/<id>/
    ├── snapshot.json
    ├── plan.json
    ├── backup.patch
    └── apply_log.json
```

JSON files are written atomically and are owner-only.

## Invariants

- Provider output is data, not commands.
- Unsafe paths stay unsafe. The provider cannot allow them.
- Every safe hunk is planned exactly once.
- A saved plan must match the worktree fingerprint.
- Staging rematches content. No whole-file fallback.
- One failed group stops the run and clears touched index entries.
- `atc` never pushes and never makes a broad fallback commit.
