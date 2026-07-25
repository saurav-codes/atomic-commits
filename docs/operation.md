# ATC build record

## Current release

Version `0.2.0` implements the adaptive semantic planner.

Completed:

- One-command `atc commit` workflow.
- One-pass pre-commit formatting by default, with `--hooks each|skip` overrides.
- One full-change model request for ordinary changes.
- Packed parallel evidence plus one lead planner for oversized changes.
- Local symbol, import, test, docs, migration, and dependency facts.
- Final split review to seek more useful atomic commits.
- Finer changed regions for nearby independent edits inside one Git hunk.
- Plan and evidence caches for unchanged retries.
- Shared planning time budget and bounded provider retries.
- Elapsed phase, request-size, evidence-count, validation, and apply feedback.
- Exact coverage and dependency-order validation.
- Updated README, design document, changelog, and package version.

Verification completed on 2026-07-21:

- `ruff check src tests`: passed.
- `mypy src`: passed.
- `pytest -q`: 132 passed, 74% coverage.
- `atc selftest --cases 3 --seed 42`: passed.
- Installed wheel reports `atc 0.2.0`.
- Live OpenAI-compatible example used the configured `ATC_*` environment.
- The live example created five meaningful commits from five changed regions.
- The example worktree finished clean and all four example tests passed.

Observed live-provider timing:

- Local scan and change analysis completed immediately.
- Direct path selected one approximately 1,853-token planning request.
- The configured reasoning model spent several minutes on the first plan.
- The first five-group result was cached before the final split review.
- The CLI displayed both model phases and their elapsed time throughout the run.
