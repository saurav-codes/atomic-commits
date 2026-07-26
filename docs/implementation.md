# ATC design

## Product

`atc` turns one final Git worktree into the greatest useful number of meaningful,
atomic commits. It protects repository safety first, then meaning and completeness,
then maximizes commit count, and only then optimizes time and token use.

The main command is:

```bash
atc commit
```

The command plans, shows the plan, asks for confirmation, stages exact changed
regions, and creates the commits. `atc commit --yes` skips confirmation.

## Rules

- Every safe changed region is assigned exactly once.
- Every commit has one clear purpose and a specific plain-English message.
- Unrelated work is split whenever the resulting commits remain useful and complete.
- Related work stays together when separation would be incomplete or broken.
- Dependency order is explicit and checked.
- Unsafe, ignored, generated, secret, and refused binary files are never included.
- There are no empty, catch-all, or artificial commits.
- No AI request runs while the user or coding agent is editing files.
- Provider failure never falls back to low-quality commits.

## Planning flow

1. Preflight checks ensure Git is ready and no conflicting operation is running.
2. The scanner freezes HEAD, branch, index policy, worktree, untracked files, and a
   content fingerprint.
3. The diff parser separates nearby independent edits into stageable changed regions.
4. `change_analysis.py` records local facts: symbols, languages, imports, tests,
   docs, dependencies, migrations, and file relationships.
5. The planner estimates the complete request size.
6. Ordinary changes use one full-change request to the lead model.
7. Oversized changes are packed into bounded evidence requests. They run in parallel,
   then one lead model sees all evidence and makes the global plan.
8. A review request tries to find more useful splits and repairs grouping or order.
9. Local validation checks exact coverage, unique group IDs, messages, paths, and
   dependency order.
10. The plan is saved before any commit is created.
11. The stager applies only the planned regions; the committer rescans after every
   commit and stops on the first mismatch.

## Local change facts

Local analysis provides evidence, not product intent. It may say that two regions
change the same symbol, that a test name matches a source file, or that one changed
file imports another. Only the model decides whether those regions form one commit.

The base implementation uses Python's AST where available and small language-neutral
rules elsewhere. Raw patches are always retained, so unsupported languages lose local
enrichment but never lose change content.

## Direct and large-change paths

Direct planning is selected when the full system prompt, repository context, local
change facts, and raw diff fit under `--full-change-limit` (default 120,000 estimated
tokens). The lead model sees the entire final change in one request.

Large planning packs related regions up to `--max-chunk-tokens`, gathers structured
evidence concurrently, and sends all compact evidence to one lead planner. Evidence
workers never create final commit groups.

## Time, retry, and resume

`--time-limit` is the planning wall-clock budget (default 600 seconds). Provider
attempts share a timeout budget; retries cannot multiply one request timeout into
hours. Interactive output shows the snapshot, chosen path, request size, elapsed time,
parallel completion counts, review, validation, and apply progress.

Evidence and plans are cached below `.git/atc/sessions/cache/` using the snapshot,
model, prompt version, and content fingerprints. An unchanged retry resumes completed
AI work. A changed snapshot causes a new final plan.

## Data

- `Hunk`: one independently stageable changed region.
- `ChangeUnit`: compact local facts for one region.
- `ChangeLink`: a locally proven relationship between two regions.
- `ChangeGraph`: all units and links for the frozen snapshot.
- `ChunkReview`: evidence from one large-change request.
- `CommitGroup`: message, reason, regions, dependencies, risk, and split reason.
- `CommitPlan`: ordered groups, exclusions, warnings, and snapshot identity.

## Safety and apply

The existing Git CLI implementation remains the source of truth. Plans are saved with
a full backup patch. Staging rematches stable content fingerprints and exact removed /
added lines after each earlier commit changes line numbers. Failed staging or commit
operations stop immediately and clean the paths touched by the failed group. Completed
groups can be resumed from the session log.

## Main modules

- `scanner.py`, `diff_parser.py`, `fingerprints.py`: frozen change input.
- `change_analysis.py`: local token-free change facts.
- `planner.py`, `prompts.py`: adaptive planning, caching, review, and validation.
- `providers/`: bounded OpenAI-compatible and Anthropic requests.
- `stager.py`, `committer.py`: exact Git execution.
- `session.py`: plans, backups, caches, and resume state.
- `output.py`: elapsed phase feedback and final rendering.
- `cli.py`, `flows.py`: command contract and orchestration.

## Verification

Required checks:

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
atc selftest --cases 3 --seed 42
```

Live verification uses the configured `ATC_*` environment variables in a disposable
repository and confirms the final worktree, commit history, and target tests.
