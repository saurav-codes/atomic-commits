# Atomic Commits CLI (`atc`) Implementation Plan

## 1. Product Goal

Build an open-source Python CLI named `atc` that turns a dirty Git worktree into meaningful atomic commits.

The tool must:

- Scan all safe Git changes, including safe untracked files.
- Respect `.gitignore` and avoid obvious runtime files by default.
- Use AI to review the entire change set before committing.
- Handle long diffs with a map/reduce style review flow, similar to AI coding harnesses.
- Generate specific, hunk-aware commit messages.
- Support two modes:
  - `--compact`: atomic commits, but grouped into a practical number of commits.
  - `--verbose`: ultra-specific commits, often one contextual hunk per commit.
- Default to dry-run planning.
- Support both reviewed-plan apply and one-shot apply.
- Be provider-agnostic, with OpenAI-compatible and Anthropic providers in v1.
- Be robust enough for real public use, not just a prototype.

The core promise: `atc` should never create random `update file` commits. If it cannot understand a hunk well enough to name it specifically, it must stop or ask for more context rather than committing junk.

## 2. V1 Scope

V1 is exhaustive rather than minimal.

Implement:

- Installable Python package with `pipx install .`.
- CLI entrypoint named `atc`.
- OpenAI-compatible provider.
- Anthropic provider.
- Provider interface that can support future shell/custom providers.
- Dry-run planning.
- Plan file apply.
- One-shot apply.
- Compact and verbose modes.
- Safety filters for ignored, generated, runtime, and oversized files.
- Long-context AI review using chunked summaries and a reducer.
- Hunk-level diff parsing and staging.
- Commit-message validation and AI retry.
- Resume/session support.
- Rich terminal output.
- Unit tests and integration tests with temporary Git repos.
- README-level OSS documentation plan.

Do not implement:

- GUI.
- GitHub PR creation.
- History rewriting.
- Remote push.
- Automatic test execution for the target repo.
- Editing user code.
- Formatting user code.

## 3. Repository Layout

Create this project structure:

```text
atomic-commits/
  pyproject.toml
  README.md
  LICENSE
  implementation.md
  src/
    atomic_commits/
      __init__.py
      cli.py
      config.py
      errors.py
      models.py
      logging.py
      git_client.py
      scanner.py
      diff_parser.py
      safety.py
      fingerprints.py
      planner.py
      prompts.py
      stager.py
      committer.py
      session.py
      output.py
      providers/
        __init__.py
        base.py
        openai_compatible.py
        anthropic.py
      validators/
        __init__.py
        commit_messages.py
        plan_schema.py
  tests/
    unit/
      test_diff_parser.py
      test_safety.py
      test_fingerprints.py
      test_commit_message_validator.py
      test_plan_schema.py
    integration/
      test_dry_run.py
      test_apply_compact.py
      test_apply_verbose.py
      test_untracked_files.py
      test_path_filters.py
      test_staged_policy.py
      test_resume.py
      test_binary_files.py
      test_rename_delete_mode_changes.py
    fixtures/
      diffs/
```

Use `src` layout so packaging is clean.

## 4. Packaging

Use Python 3.11+.

`pyproject.toml`:

- Build backend: `hatchling`.
- CLI entrypoint:

```toml
[project.scripts]
atc = "atomic_commits.cli:app"
```

Suggested runtime dependencies:

- `typer` for CLI.
- `rich` for terminal output.
- `pydantic` for schemas.
- `httpx` for provider HTTP clients.
- `typing-extensions` if needed.

Suggested dev dependencies:

- `pytest`.
- `pytest-cov`.
- `ruff`.
- `mypy`.

No heavy Git library is required in v1. Use the `git` CLI through `subprocess` because Git patch/staging semantics are exact and battle-tested.

## 5. CLI Contract

Main command:

```bash
atc [OPTIONS]
```

Options:

```text
--compact                         Compact mode. Default.
--verbose                         Verbose mode.
--apply                           Apply a previously generated plan if present and valid.
--now                             With --apply, allow one-shot plan-and-commit in one run.
--plan PATH                       Use a specific plan file.
--repo PATH                       Target Git repo. Default: current directory.
--paths PATH...                   Limit planning to paths.
--include-staged                  Include already staged changes.
--allow-binary                    Allow safe binary file commits.
--allow-secret-like               Allow secret-looking files/hunks after warning.
--provider openai-compatible|anthropic
--model MODEL
--base-url URL                    For OpenAI-compatible providers.
--api-key-env ENV_NAME            Env var containing provider API key.
--max-chunk-tokens INT            Chunk budget for map phase.
--max-reducer-tokens INT          Summary budget for reduce phase.
--temperature FLOAT               Default 0.
--no-verify                       Pass --no-verify to git commit.
--yes                             Skip confirmation prompts where safe.
--json                            Emit machine-readable output.
--debug                           Keep extra session diagnostics.
--version
```

Subcommands:

```text
atc                         Create a dry-run plan.
atc --apply                 Apply existing plan if fingerprint matches.
atc --apply --now           Plan and apply in one run.
atc resume                  Resume latest interrupted session.
atc sessions                List sessions.
atc show-plan [PATH]        Pretty-print a saved plan.
atc doctor                  Validate git/provider configuration.
```

Default behavior:

- `atc` means compact dry-run.
- `atc --verbose` means verbose dry-run.
- `atc --apply` requires a saved valid plan.
- `atc --apply --now` plans and commits immediately.
- Existing staged changes are refused unless `--include-staged` is present.

## 6. Execution Flow

### 6.1 Dry Run Flow

1. Resolve repo path.
2. Run preflight checks.
3. Scan worktree.
4. Filter unsafe files.
5. Parse diffs into files and hunks.
6. Build full AI review context.
7. Run AI map phase over diff chunks.
8. Run AI reduce phase to produce commit groups.
9. Validate plan schema.
10. Validate every safe hunk is assigned exactly once.
11. Validate commit messages are specific.
12. Save `.git/atc/sessions/<session_id>/plan.json`.
13. Also write `.git/atc/plan.json` as latest plan pointer/copy.
14. Print proposed commits and warnings.
15. Do not stage or commit anything.

### 6.2 Apply Existing Plan Flow

1. Load `.git/atc/plan.json` or `--plan`.
2. Re-run preflight.
3. Re-scan worktree.
4. Recompute fingerprint.
5. Refuse if fingerprint does not match.
6. For each planned commit:
   - Re-match hunk fingerprints to current diff.
   - Stage only those hunks.
   - Validate staged diff matches the planned hunks.
   - Commit with validated message.
   - Record commit SHA.
   - Re-scan worktree.
7. Stop on the first unsafe mismatch.
8. Print final status and commit list.

### 6.3 One-Shot Apply Flow

`atc --apply --now`:

1. Runs the full dry-run planning flow internally.
2. Saves the plan.
3. Immediately applies it.

This mode is useful for automation, but the implementation must still write the plan/session files so failures are inspectable.

## 7. Preflight Rules

Run these before planning or applying:

```bash
git rev-parse --show-toplevel
git status --porcelain=v1 -z
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
```

Refuse if:

- Not inside a Git repo.
- Repo has no commits yet, unless v1 explicitly supports initial commits.
- `.git/MERGE_HEAD` exists.
- `.git/rebase-merge` exists.
- `.git/rebase-apply` exists.
- `.git/CHERRY_PICK_HEAD` exists.
- `.git/REVERT_HEAD` exists.
- Index has staged changes and `--include-staged` is not provided.
- Any pathspec in `--paths` does not exist and is not represented in Git status.
- Git executable is unavailable.

Warn but allow:

- Branch is behind/ahead remote.
- No remote exists.
- Hooks are configured.
- Large diff may cost more tokens.

## 8. Worktree Scanning

Use `git status --porcelain=v1 -z` as the source of truth for file state.

Collect:

- Branch name.
- HEAD SHA.
- Git root.
- Status entries.
- Tracked modified/deleted/renamed files.
- Safe untracked files.
- Existing staged files if `--include-staged`.
- Ignored files only for diagnostics; never include them.

Commands:

```bash
git status --porcelain=v1 -z --untracked-files=all
git diff --stat
git diff --find-renames --patch --unified=3 --binary
git diff --cached --find-renames --patch --unified=3 --binary
git ls-files --others --exclude-standard -z
git check-ignore -z --stdin
```

For untracked files:

- Use `git ls-files --others --exclude-standard -z`.
- Exclude unsafe paths.
- For safe text files, synthesize added-file diffs using `git diff --no-index /dev/null <file>` or internal text reading.
- Do not run `git add -N` during dry-run.
- During apply, `git add -N` is allowed for the specific file being staged.

## 9. Safety Filtering

Safety must happen before AI sees content and before staging.

### 9.1 Always Exclude Paths

Exclude if any path component matches:

```text
.git
.hg
.svn
.venv
venv
env
node_modules
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
.tox
.nox
dist
build
target
coverage
.coverage
.next
.nuxt
.turbo
.cache
logs
tmp
temp
.DS_Store
```

Exclude file names:

```text
.env
.env.*
*.pem
*.key
*.p12
*.pfx
id_rsa
id_ed25519
known_hosts
*.log
*.sqlite
*.db
```

Allow `.env.example`, `.env.sample`, and similar sample files only if they do not contain real-looking secrets.

### 9.2 Secret-Like Content Detection

Scan included text files and hunks for:

- AWS access key patterns.
- GitHub tokens.
- OpenAI/Anthropic API key patterns.
- Private key headers.
- JWT-looking long tokens.
- Slack tokens.
- Stripe keys.
- Generic assignment names like `SECRET`, `TOKEN`, `API_KEY`, `PASSWORD`, `PRIVATE_KEY` with long high-entropy values.

Default behavior:

- Hard refuse secret-like content.
- Print exact file and line if available.
- User can override only with `--allow-secret-like`.

Even with override, commit message must not include secret values.

### 9.3 Binary Files

Detect binary files by:

- NUL bytes in first 8192 bytes.
- Git binary patch markers.
- Common binary extensions.

Default:

- Refuse binary files.

With `--allow-binary`:

- Allow common safe assets as one file-level commit:
  - `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.avif`, `.ico`, `.pdf`.
- Still refuse archives, databases, executables, private keys, and unknown binary formats.

## 10. Diff Model

Define Pydantic models.

```python
class WorktreeSnapshot(BaseModel):
    repo_root: Path
    branch: str
    head_sha: str
    status_entries: list[StatusEntry]
    files: list[FileChange]
    fingerprint: str

class FileChange(BaseModel):
    path: str
    old_path: str | None
    status: Literal["modified", "added", "deleted", "renamed", "mode", "binary"]
    is_tracked: bool
    is_binary: bool
    hunks: list[Hunk]
    safety: SafetyResult

class Hunk(BaseModel):
    hunk_id: str
    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    context_before: list[str]
    removed: list[str]
    added: list[str]
    context_after: list[str]
    patch: str
    fingerprint: str

class CommitGroup(BaseModel):
    group_id: str
    message: str
    rationale: str
    hunk_ids: list[str]
    file_paths: list[str]
    risk: Literal["low", "medium", "high"]
```

Hunk IDs must be stable within a single scan:

```text
<relative-path>::hunk::<ordinal>
```

Fingerprints must be stable enough to rematch after earlier hunks have been committed:

- Include path.
- Include normalized added/removed lines.
- Include nearby function/class heading if detectable.
- Include old/new header as weak signal.
- Do not rely only on line numbers.

## 11. Diff Parsing

Use Git patch output as the primary source.

Parser must support:

- Modified files.
- Added files.
- Deleted files.
- Renames.
- Mode-only changes.
- Binary patches.
- Files with spaces.
- Quoted paths.
- Adjacent hunks.
- No-newline markers.

Do not use zero-context diffs for staging.

Use `--unified=3` or greater. Default to 3 context lines because it is safer for `git apply`.

Important implementation note:

- Never repeatedly stage from the same zero-context diff.
- After each commit, regenerate the current diff and rematch remaining hunks.
- This avoids duplicate index-only patch bugs and stale hunk application.

## 12. AI Provider Layer

Create a provider interface:

```python
class AIProvider(Protocol):
    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        ...
```

### 12.1 OpenAI-Compatible Provider

Use HTTP, not SDK, to keep dependencies smaller and support compatible providers.

Config:

- `ATC_OPENAI_API_KEY`
- `ATC_OPENAI_BASE_URL`
- `ATC_OPENAI_MODEL`

Default base URL:

```text
https://api.openai.com/v1
```

Endpoint:

```text
POST /chat/completions
```

Request:

- Use JSON mode or response format if supported.
- Temperature default `0`.
- Model required from env/config/CLI.

### 12.2 Anthropic Provider

Config:

- `ATC_ANTHROPIC_API_KEY`
- `ATC_ANTHROPIC_MODEL`

Endpoint:

```text
POST https://api.anthropic.com/v1/messages
```

Implementation:

- Ask for JSON-only output.
- Extract JSON robustly.
- Validate with Pydantic.

### 12.3 Provider Selection

Priority:

1. CLI flags.
2. Environment variables.
3. Config file.
4. Error with helpful setup instructions.

Possible config file locations:

```text
.atc.toml
~/.config/atc/config.toml
```

Do not require config files for v1. Env vars should be enough.

## 13. Long-Context Review Strategy

The AI must first review the whole change set before creating commits.

Use a map/reduce pipeline.

### 13.1 Context Pack

Build a compact context pack:

- Repo name.
- Branch.
- Recent commit subjects.
- User mode: compact or verbose.
- Safety exclusions.
- File list with statuses.
- Diffstat.
- Relevant repo instruction excerpts if found.
- Hunk inventory.

Instruction files to scan:

```text
AGENTS.md
.cursorrules
.cursor/rules/*
CLAUDE.md
README.md
```

Keep instruction scanning bounded:

- Root files first.
- For changed files, walk parent dirs looking for `AGENTS.md`.
- Limit total instruction text.

### 13.2 Map Phase

Split diffs into chunks by token budget.

Chunking rules:

- Keep each hunk intact.
- Keep small related hunks from the same file together.
- Never split a single hunk unless it exceeds the maximum chunk size.
- If one hunk is too large, summarize file metadata and include truncated safe excerpts with a warning.

Map output schema:

```python
class ChunkReview(BaseModel):
    chunk_id: str
    summary: str
    detected_concerns: list[str]
    suggested_groups: list[SuggestedGroup]
    risky_hunks: list[str]
    message_terms: dict[str, list[str]]
```

The map prompt must ask AI to:

- Identify behavior-level intent.
- Identify test/docs pairs.
- Identify unrelated hunks.
- Identify generated/runtime/secret concerns missed by filters.
- Suggest precise commit subjects.
- Avoid generic naming.

### 13.3 Reduce Phase

Input:

- All chunk reviews.
- Hunk inventory.
- Mode rules.

Output:

```python
class CommitPlan(BaseModel):
    version: Literal["1"]
    mode: Literal["compact", "verbose"]
    repo_fingerprint: str
    base_head: str
    groups: list[CommitGroup]
    excluded: list[ExcludedChange]
    warnings: list[str]
```

Reducer responsibilities:

- Assign every safe hunk exactly once.
- Keep unsafe hunks excluded.
- In compact mode, group by coherent intent.
- In verbose mode, prefer one hunk per commit.
- Pair tests with implementation only in compact mode when they validate exactly the same behavior.
- In verbose mode, tests usually get separate commits unless a hunk is inseparable.
- Generate final commit messages.

## 14. Compact Mode Rules

Compact mode should not mean broad or lazy.

It means:

- Fewer commits than verbose.
- Still atomic by behavior.
- No unrelated hunks together.
- Prefer implementation + matching tests in same commit if they are clearly the same behavior.
- Group docs with code only if docs describe that exact change.

Examples:

Good compact commits:

```text
backend(auth): add login rate-limit response handling
frontend(calendar): preserve dragged event rank during sync
test(tasks): cover archived subtask duration totals
config: document resend api key env var
```

Bad compact commits:

```text
backend: update core files
frontend: update components
tests: update tests
chore: remaining changes
```

Compact mode should target:

- Small diff: 1-8 commits.
- Medium diff: 5-30 commits.
- Large diff: as many as needed, but still behavior-grouped.

These are not hard limits. Correctness beats count.

## 15. Verbose Mode Rules

Verbose mode means ultra-specific.

Rules:

- Prefer one contextual hunk per commit.
- If a hunk has two independent concerns and can be safely split, split manually.
- If a hunk cannot be split safely, keep it as one commit and message it precisely.
- Do not create duplicate commits from the same hunk.
- Do not commit line-by-line if it breaks syntax or meaning.
- Do not use zero-context patches.

Verbose mode may create hundreds of commits.

Each message should reflect the hunk:

```text
backend(tasks): add expected version guard to move command
backend(tasks): emit outbox event after recurring task detach
frontend(store): reconcile task move by operation id
test(ordering): cover duplicate rank collision during sidebar move
```

## 16. Commit Message Validation

Reject messages if:

- Contains `update`, `change`, `misc`, `cleanup`, `fix stuff`, `remaining`, `wip` without specific object.
- Only names a file.
- Scope is too broad for the hunk.
- Longer than 72 chars.
- Uses secrets or copied code content.
- Does not include a concrete verb/object.

Message format:

```text
scope: verb exact behavior
```

Scope examples:

```text
backend(auth)
backend(tasks)
backend(config)
frontend(store)
frontend(calendar)
frontend(ui)
test(backend)
test(frontend)
docs
config
email
chore
```

Validator implementation:

- Rule-based first.
- If rejected, ask AI for a replacement with the specific hunk context.
- Retry at most two times.
- If still generic, stop safely.

## 17. Staging Algorithm

The stager must be conservative.

For each commit group:

1. Recompute current diff.
2. Rematch planned hunk fingerprints.
3. Build a patch containing only those hunks and required file headers.
4. Run:

```bash
git apply --cached --recount --check <patch>
```

5. If check passes:

```bash
git apply --cached --recount <patch>
```

6. Validate:

```bash
git diff --cached --patch
```

7. Ensure staged patch contains only planned paths/hunks.
8. Commit.
9. Clear assumptions and rescan.

For new files:

- If committing the whole new file, use `git add -- <path>`.
- If committing hunks from a new file, first run `git add -N -- <path>`, then patch-stage.

For deletes:

- Whole-file delete commits use `git rm --cached -- <path>` only if worktree delete matches plan.
- Do not delete files from disk; only stage existing deletion.

For renames:

- Use `git add -A -- old_path new_path`.
- If rename plus edits must be split, first commit pure rename only if Git can represent it safely. Otherwise commit rename+edit together with explicit message.

## 18. Session And Resume

Store session data in:

```text
.git/atc/sessions/<session_id>/
  snapshot.json
  plan.json
  chunk_reviews.json
  apply_log.json
  backup.patch
  excluded.json
```

Also write:

```text
.git/atc/plan.json
```

Session ID format:

```text
YYYYMMDD-HHMMSS-<short-random>
```

Resume rules:

- `atc resume` loads latest incomplete session.
- Refuse resume if current worktree fingerprint is incompatible.
- If some commits already succeeded, skip completed groups by recorded SHA.
- If HEAD changed unexpectedly, stop and tell the user to rerun planning.

Backup:

- Before apply, write `backup.patch` using `git diff --binary`.
- This is for user recovery, not automatic rollback.

Do not run destructive rollback commands automatically.

## 19. Fingerprinting

Compute repo fingerprint from:

- HEAD SHA.
- Branch.
- Included file paths.
- File statuses.
- Hunk fingerprints.
- Untracked safe file content hashes.

Do not include:

- Ignored files.
- Denylisted files.
- Session files.
- Timestamps.

Use SHA-256.

Plan apply requires fingerprint match unless `--now` generated the plan in the same process.

## 20. Output UX

Use Rich output.

Dry-run output:

```text
Atomic Commits Plan
Repo: /path/to/repo
Mode: compact
Safe hunks: 42
Excluded files: 3
Planned commits: 9

1. backend(auth): add login rate-limit response handling
   Hunks: backend/apps/auth/views.py::hunk::1
   Why: Adds explicit 429 response path.

2. test(auth): cover login rate-limit response
   Hunks: backend/tests/test_auth.py::hunk::2
   Why: Verifies throttled login behavior.
```

Apply output:

```text
Applying 9 commits
[1/9] backend(auth): add login rate-limit response handling ... abc1234
[2/9] test(auth): cover login rate-limit response ... def5678
Done. Worktree clean except excluded files.
```

On failure:

```text
Stopped before commit 4.
Reason: planned hunk no longer matches current worktree.
No broad fallback commit was created.
Session: .git/atc/sessions/...
Next: rerun `atc` to create a fresh plan.
```

## 21. Error Handling

Use typed errors:

- `PreflightError`
- `UnsafeFileError`
- `SecretDetectedError`
- `ProviderError`
- `InvalidAIResponseError`
- `PlanValidationError`
- `PatchApplyError`
- `FingerprintMismatchError`
- `CommitError`

Rules:

- Errors must be actionable.
- Never leave staged changes behind silently.
- If staging fails after partial staging, run `git restore --staged -- <paths>` for only paths touched by the current attempted group.
- Do not touch unrelated staged changes.
- If cleanup fails, print exact manual commands.

## 22. Hooks Policy

Default:

- Let normal Git hooks run.
- Stop if hooks fail.
- If a hook mutates files, detect changed fingerprint and stop.

`--no-verify`:

- Pass `--no-verify` to `git commit`.
- Still perform all `atc` safety checks.

Never default to `--no-verify`.

## 23. AI Prompt Requirements

Prompts must explicitly say:

- You are planning Git commits, not editing code.
- Use only provided diffs.
- Do not invent files or hunks.
- Every safe hunk must be assigned exactly once.
- Commit messages must be specific to the behavior.
- Generic messages are invalid.
- In compact mode, group by coherent behavior.
- In verbose mode, prefer hunk-level commits.
- If unsure, mark the hunk as requiring human review.

Provider responses must be JSON only.

The implementation must validate AI output and never trust it blindly.

## 24. Plan Schema Validation

Validation rules:

- Every `hunk_id` exists.
- No duplicate `hunk_id`.
- No missing safe hunk.
- Excluded hunks have a reason.
- Commit groups are non-empty.
- Commit messages pass validator.
- File paths in group match hunk paths.
- Compact mode cannot group unrelated path categories unless rationale explicitly links them.
- Verbose mode groups should usually contain one hunk; multi-hunk groups need rationale.

If validation fails:

1. Send validation errors back to AI once.
2. Revalidate.
3. Retry once more.
4. Stop safely if still invalid.

## 25. Testing Plan

### 25.1 Unit Tests

Test diff parsing:

- One modified file.
- Multiple hunks.
- Adjacent hunks.
- Added file.
- Deleted file.
- Rename.
- Rename plus edit.
- Mode-only change.
- Binary patch.
- File names with spaces.
- No newline at EOF.

Test safety:

- `.env` excluded.
- `.env.sample` allowed only with safe placeholder values.
- `.venv`, `node_modules`, caches excluded.
- Secret-looking token refused.
- Ignored files excluded.
- Safe untracked source file included.

Test fingerprints:

- Stable for same diff.
- Changes when hunk content changes.
- Can rematch after earlier unrelated hunk committed.

Test messages:

- Reject generic messages.
- Accept specific messages.
- Enforce 72 chars.
- Reject messages containing secret values.

Test provider layer:

- OpenAI-compatible request shape.
- Anthropic request shape.
- Invalid JSON handling.
- Rate-limit retry.
- Provider timeout.

### 25.2 Integration Tests

Use temporary Git repos.

Scenarios:

- Dry-run creates plan and no commits.
- `--apply` applies saved plan.
- `--apply --now` plans and commits in one run.
- Compact mode groups implementation and exact matching tests.
- Verbose mode commits individual hunks.
- Existing staged changes refused by default.
- `--include-staged` includes index changes.
- Unsafe untracked files remain uncommitted.
- Safe untracked files are committed.
- Secret-like content stops the run.
- Binary files refused by default.
- Safe image allowed with `--allow-binary`.
- Hook failure stops run.
- Hook mutation stops run.
- Interrupted run can resume.
- Fingerprint mismatch refuses stale plan.
- Failed patch apply leaves index clean.

### 25.3 Acceptance Tests

Manual acceptance:

```bash
pipx install .
atc --version
atc doctor
atc --compact
atc --verbose
atc --apply
atc --apply --now --verbose
```

Expected:

- CLI installs.
- Dry-run does not mutate Git state.
- Apply creates meaningful commits.
- No denylisted files committed.
- Final status is clean or only excluded files remain.

## 26. Implementation Order

Implement in this order:

1. Package scaffold and CLI skeleton.
2. Git command wrapper.
3. Worktree scanner.
4. Safety filters.
5. Diff parser.
6. Fingerprints.
7. Pydantic schemas.
8. Provider base interface.
9. OpenAI-compatible provider.
10. Anthropic provider.
11. Prompt templates.
12. Map/reduce planner.
13. Plan validation.
14. Commit-message validator.
15. Session writer/reader.
16. Dry-run output.
17. Stager.
18. Committer.
19. Apply saved plan.
20. One-shot apply.
21. Resume command.
22. Full test suite.
23. README and OSS docs.

Do not implement staging before parser/fingerprint tests exist.

## 27. Important Non-Negotiables

- Do not edit user code.
- Do not run formatters on target repos.
- Do not push.
- Do not rewrite history.
- Do not auto-delete files.
- Do not commit ignored files.
- Do not commit `.env` or secrets.
- Do not use zero-context patch staging.
- Do not create generic commit messages.
- Do not make broad fallback commits after hunk staging fails.
- Do not continue if the worktree changes unexpectedly mid-run.
- Do not silently truncate AI context.

## 28. README Requirements

README should include:

- What `atc` does.
- Why atomic commits matter.
- Install instructions with `pipx`.
- Provider setup for OpenAI-compatible and Anthropic.
- Quickstart.
- Compact vs verbose examples.
- Safety model.
- Dry-run/apply examples.
- Recovery/resume examples.
- Clear warning that `atc` commits locally only and never pushes.

## 29. Example User Flows

### Compact Dry Run

```bash
atc
```

Expected:

- Scans repo.
- Writes `.git/atc/plan.json`.
- Prints practical atomic commit plan.
- Makes no commits.

### Apply Reviewed Plan

```bash
atc --apply
```

Expected:

- Loads latest plan.
- Verifies fingerprint.
- Applies each commit.
- Stops safely on mismatch.

### Verbose One-Shot

```bash
atc --verbose --apply --now
```

Expected:

- Creates a very granular plan.
- Commits many specific hunk-level commits.
- Writes session logs.

### Include Staged Changes

```bash
atc --include-staged
```

Expected:

- Treats staged and unstaged changes as intentional input.
- Plan still validates every hunk exactly once.

## 30. Final Definition Of Done

The implementation is done when:

- `pipx install .` exposes `atc`.
- `atc doctor` validates provider setup.
- `atc` produces a dry-run plan in a dirty repo.
- `atc --apply` applies a saved plan.
- `atc --apply --now` works.
- `--compact` and `--verbose` create visibly different grouping.
- OpenAI-compatible provider works.
- Anthropic provider works.
- Tests cover parser, safety, planner validation, staging, apply, and resume.
- Secret/runtime files are not committed.
- Generic commit messages are rejected.
- Failed staging leaves the index clean.
- README explains installation and safety clearly.
