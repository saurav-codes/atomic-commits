---
name: atomic-commits
description: Analyzes git worktree changes and commits them as clean, granular, conventional atomic commits in logical dependency order. Commit-maxing: split changes as finely as possible.
---

# Atomic Commits

A skill for AI coding agents to dissect dirty Git working trees into clean, granular, and self-contained **atomic commits** following the Conventional Commits specification.

**Commit-maxing mindset**: The default unit of a commit is the *hunk* or *symbol*, not the file. Never stop splitting because "the file is already committed" — stop only when the next split would create a commit that is meaningless on its own or breaks the build. If two candidate changes *could* be separate commits, they **must** be separate commits. There are no numeric commit targets; the target is maximal decomposition of the actual diff.

---

## 1. What Makes a Commit "Atomic"?

A commit is **atomic** if and only if:
1. **Single Purpose**: It accomplishes exactly one focused task (e.g., adds one helper function, renames one symbol, fixes one bug, adds one test case).
2. **Separation of Concerns**: Unrelated changes (refactors, business logic, dependencies, documentation) are **never combined** in the same commit.
3. **Standalone Completeness**: The codebase remains buildable, testable, and functional after every single commit in history whenever possible.
4. **Minimal Diff**: Each commit touches the fewest lines necessary. Whole-file staging is a last resort, only when every hunk in the file serves the same single purpose.

---

## 2. Standard Commit Ordering (Topological Flow)

When partitioning changes across multiple files, always stage and commit in this logical dependency order:

1. **`build` / `chore(deps)`**: Dependencies, package manifests, lockfiles, tool configs (`package.json`, `pyproject.toml`, `tsconfig.json`, `.gitignore`). Split per-concern: one commit per dependency bump or config change.
2. **`refactor` / `style`**: Preparatory changes, extractions, renames, file reorganization, formatting changes (**strictly zero behavior change**). Split per-symbol: one rename, one extraction, one moved block per commit.
3. **`feat` / `fix` / `perf`**: Core domain logic, schemas, models, API endpoints, algorithms, bug fixes, or performance enhancements. Split per behavior: one endpoint, one validation rule, one branch of logic per commit.
4. **`test`**: Unit tests, integration tests, mock fixtures, and regression test suites covering the changes. Split per test case or per fixture where feasible.
5. **`docs`**: Documentation, README updates, changelogs, architecture notes, and docstrings. Split per doc section or topic.

---

## 3. Strict Conventional Commit Rules

Every commit message MUST follow this structure:

```text
<type>(<scope>): <imperative verb> <exact behavior>
```

### Commit Types
- `feat`: A new feature or capability for the user or API consumer
- `fix`: A bug fix
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `perf`: Performance improvement
- `test`: Adding missing tests or correcting existing tests
- `build`: Changes that affect the build system or external dependencies
- `ci`: Changes to CI configuration files and scripts
- `docs`: Documentation-only changes
- `chore`: Tooling, formatting, repository maintenance, or metadata

### Formatting Rules
- **Scope is mandatory**: Use lowercase noun/module name (e.g., `auth`, `router`, `cli`, `storage`, `deps`).
- **Imperative mood**: Use `"add"`, `"fix"`, `"refactor"`, `"remove"`, `"extract"`, not `"added"`, `"adds"`, `"fixing"`.
- **Lowercase subject**: Start the description with a lowercase letter (unless referencing a proper noun or code identifier).
- **No trailing period**: Keep the subject concise (50–72 characters max).
- ❌ **Forbidden generic terms**: Never use words like `"update files"`, `"misc changes"`, `"clean up"`, `"wip"`, `"fix stuff"`, or `"various updates"`. With hunk-level commits, every message describes one concrete symbol or behavior, so generic messages signal under-splitting.

---

## 4. Splitting Techniques (Commit-Maxing)

Apply these techniques to decompose the diff to the finest practical grain:

### Per-file splitting
- Each file with an independent change gets its own commit. Never stage multiple files together unless they form one indivisible change (e.g., a rename touching definition + all references).

### Per-hunk splitting (the default)
- Within a file, stage hunks individually with `git add -p <file>` (interactive) or by crafting patches and applying them to the index:
  ```bash
  git diff <file> > /tmp/full.patch
  # split /tmp/full.patch into per-hunk patches, then:
  git apply --cached /tmp/hunk-1.patch
  git commit -m "<type>(<scope>): <exact behavior of hunk 1>"
  ```
- Verify what is staged before each commit: `git diff --cached` (full diff, not just `--stat`).

### Per-symbol splitting
- One new function, one new class, one new constant, one new route per commit. A commit adding three helpers becomes three commits: `feat(utils): add parse-duration helper`, then `feat(utils): add format-bytes helper`, then `feat(utils): add clamp-number helper`.
- Order within a file respects dependencies: the helper is committed before the call site that uses it.

### Separating mechanical from semantic changes
- **Import additions**: stage newly added imports separately (`refactor(<scope>): add imports for X`) when they can stand alone, or together with the first commit that requires them — never spread across unrelated commits.
- **Signature changes**: commit the signature change (`refactor(<scope>): change X signature to accept Y`) separately from call-site updates and separately from the behavior change that motivated it.
- **Formatting/whitespace**: isolated into `style(<scope>): ...` commits, never mixed with logic.
- **Type definitions / interfaces**: extracted into their own commits before the implementations that depend on them.

### Test interleaving
- When a test file accompanies logic changes, each test case (or tightly-coupled test group) is its own `test(<scope>): ...` commit after the corresponding logic commit. One test per commit when tests are independent.

### Maintaining buildability
- Before finalizing, sanity-check that splitting doesn't create commits referencing symbols that don't exist yet. Reorder or merge the minimum necessary hunks to keep each commit compiling.
- Run the project's type check or build (`tsc --noEmit`, `cargo check`, `go build ./...`, etc.) at key intermediate points; if a single hunk cannot compile alone, group it with the smallest set of hunks that compiles.

---

## 5. Execution Workflow

When the user requests atomic commits or when finishing a task:

### Step 1: Inspect the Worktree
Run Git porcelain status and diffstat:
```bash
git status --short
git diff --stat
```

### Step 2: Safety & Secret Verification
Inspect the file list to ensure no sensitive or transient files are staged:
- **Block**: `.env`, `.env.*`, `*.pem`, `*.key`, `*id_rsa*`, secrets, credentials, auth tokens.
- **Ignore**: Transient caches (`.DS_Store`, `node_modules/`, `__pycache__/`, `dist/`, build artifacts).
- *If untracked sensitive files exist, immediately warn the user and do not stage them.*

### Step 3: Formulate the Atomic Commit Plan
1. Review diffs for modified files (`git diff <path>`).
2. Group files into distinct atomic batches based on the **Topological Flow**.
3. If a single file contains both a pure refactor AND a new feature, stage the refactor first, commit, then stage the feature.
4. Present a quick preview to the user if interactive feedback is needed.

### Step 4: Stage and Commit Iteratively
For each group in sequence:
1. **Stage**:
   ```bash
   git add <file1> <file2> ...
   ```
2. **Verify Staged Content**:
   ```bash
   git diff --cached --stat
   ```
3. **Commit**:
   ```bash
   git commit -m "<type>(<scope>): <imperative verb> <exact behavior>"
   ```
4. **Confirm**: Ensure `HEAD` advanced and the index is ready for the next group.

### Step 5: Final Clean State Check
1. Run `git status` to verify the working tree is clean.
2. Output a summary table of the newly created commits:
   - Hash (`git log -n <count> --oneline`)
   - Type & Scope
   - Summary of changes
