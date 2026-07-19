# atc — Improvement Suggestions

A review of the current codebase (`src/atomic_commits/`, `tests/`).
Findings are grouped by category and tagged with priority:
**P0 = correctness/security bug, P1 = high-impact, P2 = medium, P3 = polish**.
File:line references point at the working tree as of this commit.

---

## 1. Bugs / correctness

| # | Pri | Where | Issue |
|---|-----|-------|-------|
| 1.1 | **P0** | `config.py:81` | `provider_cfg = file_cfg.get(cfg.provider.replace("-", "_"), {})` looks up `"openai_compatible"` but the documented config-file key in README is `openai-compatible`. A `[openai-compatible]` table in `.atc.toml` is silently ignored. Fix: fall back to both spellings (`"openai-compatible"`, `"openai_compatible"`). |
| 1.2 | **P0** | `providers/__init__.py:30,35` | `base_url` is `str | None` when unset; `build_provider` forwards `None` directly to `AnthropicProvider`/`OpenAICompatibleProvider`, whose signatures are `str`. `resolve_provider_credentials` only sets `base_url` for the openai provider, so Anthropic without a configured `base_url` passes `None` and crashes at `self.base_url.rstrip("/")`. mypy already flags this. Fix: in `build_provider`, fall back to `ANTHROPIC_BASE_URL` / `DEFAULT_OPENAI_BASE_URL` before constructing. |
| 1.3 | **P1** | `stager.py` `stage_group` | When `path in whole_file_new` and the no-index diff is used as a fallback, the line `matched = [h for h in cur_fc.hunks if h.fingerprint in wanted_fps] or cur_fc.hunks` silently stages **all hunks of the file** when fingerprint rematching fails. This breaks the atomic-commit guarantee: a partial-file commit could grab unrelated hunks. Fix: if no fingerprint matches, raise `PatchApplyError` instead of `or cur_fc.hunks`. |
| 1.4 | **P1** | `config.py:60` | `_load_config_file` returns `{}` on the first malformed config file it finds, but continues silently. A typo in `~/.config/atc/config.toml` makes atc behave as if no config exists with no diagnostic. Fix: raise or log the `TOMLDecodeError` path with the file location. |
| 1.5 | **P1** | `providers/base.py` `_post_with_retry` | Retry backoff is fixed `min(2**attempt, 8)` and ignores the `Retry-After` header on 429/503. With only 3 attempts and no jitter, a provider rate-limit burst produces user-visible `ProviderError` even when waiting 20 s would have succeeded. Fix: read `Retry-After` (seconds or HTTP-date), add ±25% jitter, and make `attempts` configurable. |
| 1.6 | **P2** | `planner.py` `chunk_hunks` | If a single hunk is larger than `max_chunk_tokens`, the `if tokens + t > max_chunk_tokens and hunk_ids` guard is skipped (because `hunk_ids` is empty on the first hunk), and the oversized hunk is emitted as its own chunk silently. Downstream the LLM may truncate. Fix: emit a `warning` in the plan when any chunk exceeds the budget, or split very large hunks by context boundaries. |
| 1.7 | **P2** | `validators/commit_messages.py:14` | `_FORMAT_RE` is defined but never referenced (only `_VERB_RE` and `_FILENAME_ONLY_RE` are used). Either delete it or use it to enforce the `scope: verb object` shape — currently messages like `"fix thing"` (no scope) pass validation despite the spec requiring a scope. |
| 1.8 | **P2** | `session.py` `SessionStore` | No file locking. Two concurrent `atc --apply` processes on the same repo both call `store.create()`, both write `latest_plan_path`, and the apply logs interleave. Fix: `fcntl.flock` on `.git/atc/lock` around create/apply. |
| 1.9 | **P2** | `committer.py` `apply` | On `CommitError` the cleanup uses `staged or group.file_paths`, but `group.file_paths` may be empty (the validator allows it — paths are derived from hunks). If staging fails before returning paths **and** `file_paths` is empty, nothing is unstaged, leaving a dirty index. Fix: derive cleanup paths from `group.hunk_ids` via `planned_by_id` (already computed above). |
| 1.10 | **P3** | `flows.py` `doctor` | `ok = not (line.endswith("MISSING") or "NOT" in line or "NOT SET" in line)` is fragile string matching. A future check containing the word "NOTIFICATION" would render red. Fix: make `doctor` return `(label, ok_bool)` tuples. |
| 1.11 | **P3** | `scanner.py:152` | mypy: `file_entries` is `list[tuple[str, FileStatus]]` (a `Literal`) but `worktree_fingerprint` expects `list[tuple[str, str]]`. Fix the signature to `Sequence[tuple[str, str]]` or cast. |

---

## 2. Architecture / scaling

| # | Pri | Issue |
|---|-----|-------|
| 2.1 | **P1** | **No streaming/reduce for large changesets.** `run_reduce` sends the entire hunk inventory + all chunk reviews in one prompt. A 200-file PR blows `max_reducer_tokens` and fails with no recovery. Consider a hierarchical reduce (reduce the reductions) or a greedy assignment pass that only asks the LLM for messages/rationale, not the assignment. |
| 2.2 | **P1** | **No response caching.** Re-running `atc` after a plan-validation failure re-calls every chunk. Cache `(chunk_id, hunk_fingerprints) -> ChunkReview` in the session dir so only changed chunks re-run. This also makes `atc resume` cheaper. |
| 2.3 | **P2** | **Hardcoded `max_workers = min(total, 8)`** in `planner.run_map`. On a 32-core box with 60 files this under-parallelizes; on a rate-limited provider it over-parallelizes and triggers 429s. Make it `--max-parallel` with a sensible default tied to the provider. |
| 2.4 | **P2** | **`_candidate_json_objects` in `providers/base.py` is O(n²)** in the model output length (nested brace scanning from every `{`). For a 50 KB reduce response this is ~250 K iterations. Fine for now, but if large plans appear, switch to a single forward scan with a stack. |
| 2.5 | **P2** | **No cost / token telemetry.** Providers return usage in the response (`usage.total_tokens`); atc drops it. Sum and print `~N tokens, $M est.` at the end of a run. Helps users reason about cost. |
| 2.6 | **P2** | **Provider abstraction is HTTP-only.** `AIProvider.complete_json` is synchronous and assumes urllib-style request/response. Adding a streaming provider, a local llama.cpp server, or an MCP-served model would require reworking the protocol. Consider an `async` interface or at minimum a `stream: bool` path. |
| 2.7 | **P3** | **`GitClient` shells out per call.** Each `scan()` runs `status`, `diff`, `diff --cached`, `ls-files`, and one `diff --no-index` per untracked file. For 500 untracked files that's 500 subprocess invocations. Batch untracked diffs with a single `git diff --no-index --` over a temp dir, or use `git update-index --add --cacheinfo` once. |

---

## 3. UX

| # | Pri | Issue |
|---|-----|-------|
| 3.1 | **P1** | **`--apply --now` commits without confirmation.** The README says "keeps you in control", but `--now` plans and commits in one shot. Add a `--yes` gate (already a flag, currently unused for this) or an interactive confirm listing the N proposed commits. |
| 3.2 | **P1** | **`--json` is only partially implemented.** `print_plan` and `print_apply_result` honor it, but `doctor`, `sessions`, `resume` messages, and spinner diagnostics on stderr do not. Document the contract: `--json` emits a single JSON object on stdout and nothing on stderr except errors. |
| 3.3 | **P2** | **`show-plan` prints `console.print_json(plan.model_dump_json())`** — raw nested JSON, not a human-readable rendering. Reuse `print_plan` for non-JSON mode and add a `--json` flag to `show-plan`. |
| 3.4 | **P2** | **No `atc init` config wizard.** New users have to read the README to set env vars. A `atc init` that prompts for provider, writes `~/.config/atc/config.toml`, and runs `doctor` would smooth onboarding. |
| 3.5 | **P2** | **No `atc undo` / rollback from `backup.patch`.** `backup.patch` is written but recovery is manual (`git apply --reverse`). Add `atc undo <session-id>` that loads the session's `backup.patch` and reverses it, with a confirmation. |
| 3.6 | **P2** | **Plan output has no diff preview.** Each group shows hunk IDs but not what's in them. A `--show-diff` flag or a `[d] show diff` prompt would let users audit before `--apply`. |
| 3.7 | **P3** | **`print_sessions` only lists IDs.** Add timestamp, status (planned/committed/failed), and commit count per session — the data is already in `apply_log.json`. |
| 3.8 | **P3** | **`format_files` truncates at 3.** For a 10-file commit the user sees `a, b, c, +7 more` and can't tell what the +7 are. Add `--verbose` rendering or a hover/expand hint. |

---

## 4. Features

| # | Pri | Feature |
|---|-----|---------|
| 4.1 | **P1** | **`--amend` / `--squash` modes.** Many users want to fix the *last* commit's split, not create new ones. An `--amend` that re-plans only the working tree + HEAD's diff and rewrites the last commit (with confirmation) covers the "I committed too coarsely" case. |
| 4.2 | **P2** | **Custom commit-message template.** Let users supply a `message_template` in config (e.g. `${scope}: ${verb} ${object} [JIRA-1234]`) and have the planner fill placeholders. Avoids post-processing commits with a hook. |
| 4.3 | **P2** | **`.atcignore` override.** Safety excludes `.env*` unconditionally, but some repos keep a tracked `.env.example` already covered by `SAMPLE_ENV_RE`. A `.atcignore`/`.atcallow` file would let teams express "yes, really commit this" without the `--allow-binary` sledgehammer. |
| 4.4 | **P2** | **`atc selftest` CLI command.** `selftest.py` is a full deterministic harness but isn't wired to the CLI. Expose `atc selftest [--cases N] [--seed S] [--live]` so users can validate their install and providers. |
| 4.5 | **P3** | **`atc explain <group-id>`** to print the rationale + hunk diffs for one group from a saved plan. Useful for review. |
| 4.6 | **P3** | **Co-authored-by / trailers.** Optional `--trailer "Co-authored-by: ..."` repeated flag appended to every commit message. |
| 4.7 | **P3** | **Per-group risk in plan output.** `CommitGroup.risk` is in the model and validated but never shown. Color-code groups (red=high) in `print_plan`. |

---

## 5. Code quality / maintainability

| # | Pri | Issue |
|---|-----|-------|
| 5.1 | **P2** | **`from __future__ import annotations` is on every module** but `requires-python = ">=3.11"` (PEP 563 is default-optional and 3.14 makes it standard). It's harmless but worth a one-time decision: either drop it everywhere or keep it everywhere consistently. |
| 5.2 | **P2** | **No `py.typed` marker.** The package is fully typed but downstream users get no type info on import. Add `src/atomic_commits/py.typed` and `pyproject` `tool.hatch.build.targets.wheel` already covers it. |
| 5.3 | **P2** | **`output._ElapsedSpinner` uses a global `_active_spinner`** for nesting. This breaks if two consoles run concurrently (e.g. in tests). Move the "active spinner" reference onto the `Console` instance or use a `contextvars.ContextVar`. |
| 5.4 | **P3** | **`scanner.py` re-implements `_within_paths`** with a simple prefix check. `Path.is_relative_to` (3.9+) does this and handles edge cases. |
| 5.5 | **P3** | **`diff_parser._unquote_path`** uses `latin-1` + `unicode_escape` which mangles non-ASCII paths on Windows-style quoting. Git uses UTF-8 for unquoted paths; the quoted form uses octal `\NNN`. Consider `codecs.decode(raw, "unicode_escape")` only when quoted, else UTF-8. |
| 5.6 | **P3** | **`safety.py` mixes `re.compile` at module load with literal sets.** Minor, but `EXCLUDED_FILENAME_PATTERNS` could be a single combined regex for speed on large file lists. |
| 5.7 | **P3** | **`selftest._run_case` catches `Exception` broadly** (`# noqa: BLE001`). Acceptable for a harness, but narrowing to `(AtcError, OSError, subprocess.CalledProcessError)` would catch real bugs in the harness itself. |

---

## 6. Testing

| # | Pri | Issue |
|---|-----|-------|
| 6.1 | **P1** | **No test covers the parallel `run_map` path.** `planner.run_map` with `total > 1` spawns a `ThreadPoolExecutor`, but every unit test uses a single chunk. Add a test with a fake provider that records call timing / concurrency to assert parallelism and ordered result assembly. |
| 6.2 | **P1** | **No test for the config-file loader.** `resolve_provider_credentials` reading `.atc.toml` is untested; issue **1.1** above exists partly because of this. Add a fixture that writes a TOML file and asserts `cfg.model`/`base_url` resolution. |
| 6.3 | **P2** | **No test for `--json` output shape.** The JSON contract is undocumented and untested. Snapshot the stdout JSON for `plan`, `apply`, and `doctor`. |
| 6.4 | **P2** | **`ruff check` fails with 12 import-order errors** on the current working tree (mostly `tests/`). `ruff --fix` resolves them; CI should enforce this. |
| 6.5 | **P2** | **`mypy` reports 3 errors** (providers `base_url: str | None`, scanner tuple variance). These are real and should be fixed before they mask regressions. |
| 6.6 | **P3** | **No coverage gate.** `pytest-cov` is in dev deps but `addopts = "-q"` doesn't run it. Add `--cov=atomic_commits --cov-fail-under=80` (or whatever the current number is) to `pyproject.toml`. |

---

## 7. Security

| # | Pri | Issue |
|---|-----|-------|
| 7.1 | **P1** | **Provider error hints may echo the API key.** `providers/base.py` `_post_with_retry` puts `exc.read()[:300]` into the `hint`, and some providers echo the `Authorization` header value in 401 error bodies. The hint is printed to the user. Fix: redact anything matching `Bearer ...` / `sk-...` / `sk-ant-...` before putting it in a hint. |
| 7.2 | **P2** | **`backup.patch` may contain tracked `.env` changes.** `backup_patch()` runs `git diff --binary` over the whole worktree, including tracked files that safety would exclude from *commits*. If the user later runs `git apply` on the backup, they could stage an env-file change. Consider filtering the backup through the same safety list, or at least warning when the backup contains denylisted paths. |
| 7.3 | **P2** | **`session.json` files are world-readable by default.** They contain repo paths, branch names, and (in `debug`) prompt contents. `SessionStore._write_json` uses `path.write_text` with default mode. Set `0o600` on session files (esp. under `.git/atc/`). |
| 7.4 | **P3** | **`_load_instructions` reads `AGENTS.md`, `.cursorrules`, `CLAUDE.md`, `README.md`** and sends up to 4000 chars to the provider. These are untrusted-content vectors (per the repo's own `UNTRUSTED_CONTENT` warning). Document that instruction-file content is sent to the provider and add an opt-out (`--no-instructions`). |

---

## 8. Documentation

| # | Pri | Issue |
|---|-----|-------|
| 8.1 | **P2** | **`README.md` documents `ATC_OPENAI_BASE_URL` etc. but not the `.atc.toml` config file** that `config.py` supports. Either document the config file or remove the dead code path. |
| 8.2 | **P2** | **`implementation.md` (27 KB) and `operation.md` are dev docs** but ship in the sdist. Consider moving them to `docs/` and excluding from the wheel, or splitting into a spec vs. a user guide. |
| 8.3 | **P3** | **No `--help` examples for `resume`, `sessions`, `show-plan`.** The CLI help text is one line each. Add a "Examples" section per subcommand. |
| 8.4 | **P3** | **No `CHANGELOG.md`.** With `version = "0.1.0.12"` and active development, a changelog would help users track behavior changes. |

---

## Suggested order of attack

1. **P0 bug fixes** (1.1, 1.2) — config + base_url, both small and reproducible.
2. **P1 correctness** (1.3, 1.4, 1.5) — stager atomicity, config error surfacing, retry backoff.
3. **P1 scaling** (2.1, 2.2) — hierarchical reduce + chunk cache unblock large PRs.
4. **P1 UX** (3.1, 3.2) — confirmation gate + JSON contract; cheap, high-trust payoff.
5. **P1 testing** (6.1, 6.2, 6.4, 6.5) — close the gaps that let 1.1–1.2 ship.
6. **P1 security** (7.1) — redact secrets in provider hints.
7. Then P2/P3 in any order; most are independent.

---

*Generated by an OpenHands agent code review of the working tree on main.*
