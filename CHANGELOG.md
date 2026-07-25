# Changelog

All notable changes to `atc` are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Breaking — one-command default

- Bare `atc` now plans, asks, and commits in one run (the previous default was
  a dry-run plan with no commits). The old dry-run is now `atc plan`.
- `--apply` and `--now` flags are removed; the default flow commits, and
  `atc apply` re-applies a saved plan.
- `--json` with no `--yes` emits the plan as JSON and does not commit.
- Merged `--amend` and `--squash` into one rewrite path (`_run_rewrite`).
- Replaced the hand-rolled TOML writer with `tomli_w` (new dependency).
- Removed duplicate `_split_diff_sections` (reused `diff_parser`).
- Removed dead `_normalize_doctor` doctor-shape adapter.
- `--json` plan/apply output is now plain JSON on stdout (no rich color codes).

### Other

- Fixed overlapping context when nearby Git changes are split into separate regions.
- `atc commit` now runs standard pre-commit checks once before planning; use
  `--hooks each` or `--hooks skip` for other workflows.
- Custom pre-commit and commit-message hooks keep Git's per-commit behavior.

### Adaptive semantic planner

- Added `atc commit` for one-command planning, review, and commit creation.
- Ordinary changes now use one globally informed model request instead of one request per file.
- Large changes use packed parallel evidence requests followed by one lead planner.
- Added a final split review that seeks the greatest useful number of atomic commits.
- Added local, token-free change facts for symbols, imports, tests, docs, migrations, and renames.
- Nearby independent edits inside one Git hunk can now be staged separately.
- Added content-addressed plan/evidence caches for unchanged retries.
- Added a shared planning time limit and honest phase/request progress.
- Provider retries now share one timeout budget instead of multiplying it per attempt.

This entry summarizes the improvements applied across the categories in
`IMPROVEMENTS.md` (1–8). Individual line references there point at the working
tree as reviewed.

### 1. Bugs / correctness

- **config.py** — provider lookup now accepts both `[openai-compatible]` and
  `[openai_compatible]` (and the Anthropic table) so a hyphenated config
  section is no longer silently ignored (1.1).
- **providers** — `base_url` now falls back to `ANTHROPIC_BASE_URL` /
  `DEFAULT_OPENAI_BASE_URL` instead of forwarding `None`, preventing a crash in
  the Anthropic provider when no base URL is configured (1.2).
- **stager** — fingerprint-rematch failures now raise `PatchApplyError` instead
  of silently staging every hunk of a file, preserving the atomic-commit
  guarantee (1.3).
- **config** — malformed config files are reported with the file path and
  parse error rather than being silently swallowed (1.4).
- **providers/base** — retry backoff honors the `Retry-After` header (seconds
  or HTTP-date) with ±25% jitter, and the attempt count is configurable (1.5).
- **planner** — `chunk_hunks` emits a warning when a single chunk exceeds the
  token budget so downstream truncation is not silent (1.6).
- **validators** — unused format regex removed / scope enforcement tightened
  (1.7).
- **session** — `SessionStore` now takes a `fcntl.flock` on `.git/atc/lock`
  around create/apply to prevent interleaved writes from concurrent invocations
  (1.8).
- **committer** — cleanup on `CommitError` derives paths from `group.hunk_ids`
  so a failed stage no longer leaves a dirty index when `file_paths` is empty
  (1.9).
- **flows.doctor** — check results return `(label, ok)` tuples, replacing
  fragile substring matching (1.10).
- **scanner** — `file_entries` typing fixed so mypy no longer complains about
  the `Literal` vs `str` tuple variance (1.11).

### 2. Architecture / scaling

- Hierarchical / greedy reduce added so large changesets no longer blow
  `max_reducer_tokens` (2.1).
- Chunk-review cache keyed by `(chunk_id, hunk_fingerprints)` persisted in the
  session dir; `atc resume` reuses it (2.2).
- `--max-parallel` flag added (replaces the hardcoded
  `min(total, 8)`); default tied to the provider (2.3).
- `_candidate_json_objects` switched to a single forward scan, eliminating the
  O(n²) nested-brace scan (2.4).
- Token / cost telemetry: providers surface `usage` and `atc` prints an
  estimated `~N tokens, $M est.` at end of run (2.5).
- Provider interface gained an opt-in `stream` path so streaming / local /
  MCP-served models can be added without reworking the protocol (2.6).
- `GitClient` batches untracked-file diffs to avoid one subprocess per file
  (2.7).

### 3. UX

- `--apply --now` now requires `--yes` or an interactive confirmation listing
  the proposed commits before applying (3.1).
- `--json` contract documented: a single JSON object on stdout, nothing on
  stderr except errors; `doctor`, `sessions`, and `resume` honor it (3.2).
- `show-plan` prints a human-readable rendering by default and gains a
  `--json` flag (3.3).
- `atc init` config wizard added: prompts for provider/model/key, writes
  `~/.config/atc/config.toml`, and runs `doctor` (3.4).
- `atc undo <session-id>` reverses the session's `backup.patch` with a
  confirmation prompt (3.5).
- `--show-diff` (or `[d] show diff` prompt) added so groups can be audited
  before `--apply` (3.6).
- `print_sessions` shows timestamp, status, and commit count per session (3.7).
- `format_files` honors `--verbose` to list all files instead of truncating at
  three (3.8).

### 4. Features

- `--amend` / `--squash` modes to re-plan and rewrite the last commit (4.1).
- `message_template` config key (e.g. `${scope}: ${verb} ${object} [JIRA-1234]`)
  filled by the planner (4.2).
- `.atcignore` / `.atcallow` to override unconditional safety excludes for
  opt-in files (4.3).
- `atc selftest [--cases N] [--seed S] [--live]` CLI command wired from the
  deterministic harness (4.4).
- `atc explain <group-id>` prints the rationale + hunk diffs for one group
  from a saved plan (4.5).
- `--trailer "Co-authored-by: ..."` (repeatable) appended to every commit
  message (4.6).
- Per-group `risk` color-coded in plan output (red = high) (4.7).

### 5. Code quality / maintainability

- `from __future__ import annotations` policy settled consistently (5.1).
- `src/atomic_commits/py.typed` marker added; the wheel advertises typed
  package data (5.2).
- `output._ElapsedSpinner` nesting moved off the global `_active_spinner` onto
  a `contextvars.ContextVar` (5.3).
- `scanner._within_paths` replaced with `Path.is_relative_to` (5.4).
- `diff_parser._unquote_path` UTF-8 handling fixed for non-ASCII paths (5.5).
- `safety.py` filename patterns consolidated into a single regex (5.6).
- `selftest._run_case` narrowed from bare `Exception` to
  `(AtcError, OSError, subprocess.CalledProcessError)` (5.7).

### 6. Testing

- Parallel `run_map` path covered by a fake-provider test asserting
  concurrency and ordered assembly (6.1).
- Config-file loader test added (writes a TOML fixture, asserts model /
  base_url resolution); would have caught issue 1.1 (6.2).
- `--json` output shape snapshot tests for `plan`, `apply`, `doctor` (6.3).
- `ruff check` clean on the whole tree; import-order errors fixed (6.4).
- `mypy` clean: `base_url: str | None` and scanner tuple variance fixed (6.5).
- Coverage gate added to `pyproject.toml` via `--cov=atomic_commits
  --cov-fail-under=<n>` (6.6).

### 7. Security

- Provider error hints redact `Bearer ...`, `sk-...`, `sk-ant-...` before
  being shown to the user (7.1).
- `backup.patch` filters denylisted paths (or warns) so a manual `git apply`
  cannot stage a tracked `.env` change (7.2).
- Session files (`.git/atc/`, session dir) written with mode `0o600` (7.3).
- Instruction-file loading (`AGENTS.md`, `.cursorrules`, `CLAUDE.md`,
  `README.md`) is now documented; `--no-instructions` opts out of sending
  instruction content to the provider (7.4).

### 8. Documentation

- `README.md` now documents the `.atc.toml` / `~/.config/atc/config.toml`
  config file: locations, provider tables, accepted keys, and the
  `message_template` option (8.1).
- Dev docs (`implementation.md`, `operation.md`) moved to `docs/` and excluded
  from the wheel; `docs/README.md` added as an index (8.2).
- README gains an `Examples` section for `resume`, `sessions`, `show-plan`,
  `init`, `selftest`, and `undo`; subcommand `--help` text carries matching
  examples (8.3).
- `CHANGELOG.md` created with this `Unreleased` entry (8.4).
