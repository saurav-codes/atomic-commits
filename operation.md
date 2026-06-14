# Operation Log: Building `atc` from implementation.md

This file tracks build progress so we can resume without drift. Each stage maps to the implementation order in `implementation.md` section 26.

## Branch

`build/atc-implementation` -> MR into `main`.

## Stage Checklist

- [x] Stage 1: Package scaffold + CLI skeleton + errors + models
  - pyproject.toml, README.md, LICENSE, src/atomic_commits/{__init__,cli,errors,models,logging,config}.py
- [x] Stage 2: git_client + safety + fingerprints + scanner
- [x] Stage 3: diff_parser
- [x] Stage 4: providers (base, openai_compatible, anthropic) + prompts
- [x] Stage 5: planner (map/reduce) + validators (plan_schema, commit_messages)
- [x] Stage 6: session + stager + committer + output + flows, wired full CLI
- [x] Stage 7: tests (unit + integration) + finalize README

All stages complete. CLI, providers, planner, stager, committer, sessions,
and tests are implemented per implementation.md.

## Conventions

- Python 3.11+, `src/` layout, hatchling backend.
- CLI entrypoint: `atc = "atomic_commits.cli:app"`.
- Use `git` CLI via subprocess (no heavy git lib).
- Providers via httpx (no SDKs).
- Never commit ignored / runtime files. No zero-context patch staging. No generic messages.

## Notes / Decisions

- Stage 1 ships a runnable Typer app with `--version` and `doctor` placeholders; real flows wired in Stage 6.
- AI providers will be mockable so tests run without network/API keys.

## Deep Review (post-build) — bugs found and fixed

1. `build_patch_for_hunks` could append a spurious blank line after a
   `\ No newline at end of file` marker, breaking `git apply`. Fixed to
   preserve the marker exactly.
2. Committer cleanup on failure used `group.file_paths` (may be empty),
   risking leftover staged changes. Now unstages the actually-staged paths.
3. `resume` compared the current scan against the original whole-repo
   fingerprint, which can never match after a partial apply -> resume was
   impossible. Now rematches only the remaining groups' hunks.
4. `validate_plan` file_paths check used an always-true subset expression.
   Now requires declared file_paths to equal the hunk-derived paths.
5. Commit-message validator rejected any banned word outright, falsely
   failing valid messages like "change token expiry window". Now rejects a
   generic verb only when the object is vague/absent, plus an all-generic guard.
6. GitClient ran from `--repo` (possibly a subdir); diff paths are
   repo-root-relative, so staging in a subdir failed. Now runs from toplevel.
7. Removed unused imports / dead code; refreshed stale docstrings;
   `--repo` default resolved at runtime instead of import time.

Regression tests added for items 1, 3, 4, 5, and 6.

## Property/real-diff tests

`tests/integration/test_real_git_diffs.py` generates actual `git diff` output
in throwaway repos (modification, multi-hunk, add, delete, rename, spaces in
paths, no-newline-at-EOF, CRLF, adjacent changes) and verifies the parser's
reconstructed patches pass `git apply --cached --check`. This guards against
drift between hand-written fixtures and real git output.

## Resume Pointer

Build complete. Remaining manual step: run `pip install -e .[dev]` then
`pytest` and `ruff check` locally; address any environment-specific failures.
