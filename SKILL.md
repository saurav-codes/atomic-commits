---
name: atomic-commits
description: Analyzes git worktree changes and commits them as clean, granular, conventional atomic commits in logical dependency order.
---

# Atomic Commits

A skill for AI coding agents to dissect dirty Git working trees into clean, granular, and self-contained **atomic commits** following the Conventional Commits specification.

---

## 1. What Makes a Commit "Atomic"?

A commit is **atomic** if and only if:
1. **Single Purpose**: It accomplishes exactly one focused task (e.g., adds a helper function, refactors a module, fixes a specific bug, adds a test suite).
2. **Separation of Concerns**: Unrelated changes (refactors, business logic, dependencies, documentation) are **never combined** in the same commit.
3. **Standalone Completeness**: The codebase remains buildable, testable, and functional after every single commit in history whenever possible.

---

## 2. Standard Commit Ordering (Topological Flow)

When partitioning changes across multiple files, always stage and commit in this logical dependency order:

1. **`build` / `chore(deps)`**: Dependencies, package manifests, lockfiles, tool configs (`package.json`, `pyproject.toml`, `tsconfig.json`, `.gitignore`).
2. **`refactor` / `style`**: Preparatory changes, extractions, renames, file reorganization, formatting changes (**strictly zero behavior change**).
3. **`feat` / `fix` / `perf`**: Core domain logic, schemas, models, API endpoints, algorithms, bug fixes, or performance enhancements.
4. **`test`**: Unit tests, integration tests, mock fixtures, and regression test suites covering the changes.
5. **`docs`**: Documentation, README updates, changelogs, architecture notes, and docstrings.

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
- ❌ **Forbidden generic terms**: Never use words like `"update files"`, `"misc changes"`, `"clean up"`, `"wip"`, `"fix stuff"`, or `"various updates"`.

---

## 4. Execution Workflow

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
