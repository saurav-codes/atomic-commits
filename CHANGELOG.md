# Changelog

Notable user-visible changes are recorded here. The project follows Semantic
Versioning and the structure of Keep a Changelog.

## [0.3.1] - 2026-07-26

### Added

- Durable apply progress and resume recovery: each successful commit is recorded
  so an interrupted run can continue the remaining groups.
- Local plan repair splits indivisible hunks and removes duplicate groups
  before applying.
- Provider retries automatically on malformed streamed JSON before raising.
- Compact plan output hides hunk IDs and rationale; verbose mode still shows
  them.

### Changed

- Default hook mode is now `once` (was `each`). Standard pre-commit runs once
  before planning and the result is reused for every atomic commit.
- `atc commit` and the default flow restore staged files before running
  pre-commit so formatters see the full worktree.
- OpenAI-compatible provider migrated to the official SDK with streaming; a
  transient live display replaces the spinner and chunk/batch counters.
- Apply failure now reports the real planned total and advises `atc resume`.
- Progress callbacks are wired through the provider factory and prompts.

### Fixed

- Stager rejects a rename when the file content changed after the plan was
  saved, instead of silently staging the old shape.
- Stager rejects whole-file-split changes that the plan did not authorize.
- `_apply_with_progress` returns the combined prior-plus-new results so
  `resume` keeps already-committed groups.
- Resume skips missing hunks when the content is unchanged and Git reclassified
  a rename as a deletion plus addition.

## [0.2.5] - 2026-07-26

### Added

- Made bare `atc` the plan, review, confirm, and commit workflow; added the
  explicit `atc plan` dry-run and `atc apply` saved-plan commands.
- Added `atc commit`, including staged changes and configurable `once`, `each`,
  or `skip` hook handling.
- Added session inspection, plan rendering, group explanation, interrupted-run
  resume, backup reversal, interactive config, and deterministic self-test
  commands.
- Added `--amend` and `--squash` modes for explicitly recreating the last
  commit, repeatable commit trailers, and commit-message templates.
- Added JSON output across planning, apply, diagnostics, sessions, plan
  inspection, explanations, recovery, and self-test flows.
- Added `.atcallow` glob overrides and an opt-in safe binary asset policy.
- Added provider timeout, retry, total planning time, full-change threshold,
  parallelism, instruction loading, and split-review controls.

### Changed

- OpenAI-compatible planning now replaces its live status with the latest
  streamed response preview instead of opaque chunk and character counters.
- Replaced file-by-file planning with an adaptive semantic planner. Ordinary
  changes use one globally informed request; larger changes use cached parallel
  evidence and bounded lead-planning batches.
- Added deterministic local analysis for Python symbols, imports, matching
  test/source names, same-file regions, and change kinds before provider calls.
- Split nearby independent edits into separately stageable regions and added
  file-level handling for pure renames, deletions, additions, and mode changes.
- Plans now pass a final split review by default and are validated for exact
  safe-hunk coverage, dependency order, declared paths, and commit messages.
- Applying now rematches content fingerprints, verifies the index contains no
  extra paths, commits one group at a time, and rescans after every commit.
- Untracked-file diffs are synthesized in one batch instead of one Git process
  per file.
- Planning and evidence results are content-addressed so unchanged retries can
  reuse completed provider work.
- Session JSON writes are atomic and locked; session artifacts and backups are
  owner-readable only.
- Removed redundant adapters, JSON round trips, sentinel configuration values,
  duplicate diff splitting, and the background spinner thread.

### Fixed

- Accept provider config tables with either hyphenated or underscored names and
  honor configured base URLs, key environment names, timeouts, retries, full
  change limits, and planning deadlines.
- Normalize numeric model output for the plan schema version to the required
  string value.
- Refuse stale plans and stale remaining resume hunks instead of applying a
  mismatched change set.
- Stop partial-file staging when fingerprint/content rematching fails instead
  of expanding to a whole-file fallback.
- Clean touched index paths after staging or commit failure.
- Handle repositories invoked through a subdirectory, safe untracked files,
  non-ASCII Git paths, no-newline markers, and staged-plus-unstaged snapshots.
- Honor `Retry-After`, redact common API key formats from provider errors, and
  adapt OpenAI-compatible output limits after a parseable context-window error.
- Keep streamed previews transient even when model output contains the word
  `retry`; only actual provider retries are written to terminal history.
- Keep parallel chunk and batch counters transient, cap compact warning output,
  avoid repeating the proposed commit list, and report the real plan total after
  an apply failure.
- Run standard formatter hooks once before the default workflow, retry malformed
  streamed JSON automatically, and resume unchanged hunks when Git reclassifies
  a rename as a deletion plus addition.

### Documentation

- Replaced implementation plans, progress logs, and completed review notes with
  current user, architecture/lifecycle, and development documentation.
