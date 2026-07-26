# Architecture and code lifecycles

This document describes the current `0.2.5` implementation. Names in diagrams
map directly to modules, classes, or functions under `src/atomic_commits/`.

## System boundaries

`atc` is a synchronous Python CLI. Git is invoked as a subprocess. OpenAI-compatible
requests stream through the official OpenAI client; Anthropic uses the shared `urllib`
helper. Pydantic models are the contracts between scanning, planning, persistence,
and applying.

```mermaid
flowchart LR
    User["User / CI"] --> CLI["cli.py<br/>Typer commands"]

    subgraph Core["Application core"]
        Flow["flows.py<br/>orchestration + preflight"]
        Scan["scanner.py<br/>worktree snapshot"]
        Graph["change_analysis.py<br/>local change graph"]
        Plan["planner.py<br/>adaptive planning"]
        Commit["committer.py<br/>commit loop"]
        Stage["stager.py<br/>exact hunk staging"]
    end

    subgraph Boundaries["External boundaries"]
        Git["git_client.py<br/>Git subprocesses"]
        Provider["providers/<br/>OpenAI-compatible / Anthropic"]
        Disk["session.py<br/>.git/atc state"]
    end

    subgraph Support["Shared contracts and policy"]
        Models["models.py<br/>Pydantic models"]
        Safety["safety.py<br/>path + binary policy"]
        Diff["diff_parser.py<br/>parse/build patches"]
        Validate["validators/<br/>plan + message rules"]
        Output["output.py<br/>Rich + JSON output"]
    end

    CLI --> Flow
    Flow --> Scan
    Flow --> Plan
    Flow --> Commit
    Flow --> Disk
    Scan --> Git
    Scan --> Safety
    Scan --> Diff
    Plan --> Graph
    Plan --> Provider
    Plan --> Validate
    Commit --> Stage
    Commit --> Scan
    Stage --> Git
    Stage --> Diff
    Core --> Models
    Flow --> Output
```

### Module responsibilities

| Module | Owns | Does not own |
|---|---|---|
| `cli.py` | Option parsing, command dispatch, confirmation, JSON error envelope | Git mechanics or planning logic |
| `config.py` | `RunConfig`, config/env resolution, config creation | CLI prompts and hermetic doctor checks |
| `flows.py` | Preflight, hook preparation, plan/apply/resume orchestration | Hunk parsing or model prompts |
| `git_client.py` | Typed wrapper around Git commands | Policy decisions |
| `scanner.py` | Frozen `WorktreeSnapshot`, staged/untracked handling, path scope | Commit grouping |
| `safety.py` | Denylist, `.atcallow`, safe binary policy | Git ignore rules |
| `diff_parser.py` | Unified-diff parsing and selected-hunk patch reconstruction | Staging |
| `change_analysis.py` | Deterministic local facts and links | Final grouping decisions |
| `planner.py` | Direct/large planning, caches, review, repair, validation | Applying commits |
| `providers/` | HTTP request/response translation, retries, JSON extraction, usage | Planning policy |
| `stager.py` | Fingerprint rematch, exact index updates, extra-path check | Commit loop |
| `committer.py` | Stage/commit/rescan loop and failure cleanup | Session selection |
| `session.py` | Locked, persistent snapshots, plans, logs, backups, cache paths | Worktree mutation |
| `output.py` | Human and machine-readable rendering | Application decisions |

## Command dispatch lifecycle

Typer invokes the root callback first. It builds one `RunConfig`, stores it on
the context, and either dispatches the default flow or leaves it for a
subcommand.

```mermaid
flowchart TD
    Start(["atc invocation"]) --> Parse["cli.main<br/>parse global options"]
    Parse --> Config["construct RunConfig"]
    Config --> Command{"Subcommand?"}

    Command -- No --> Rewrite{"--amend or --squash?"}
    Rewrite -- No --> Default["_run_default<br/>plan → confirm → apply"]
    Rewrite -- Yes --> RewriteFlow["_run_rewrite"]

    Command -- plan --> PlanOnly["flows.dry_run"]
    Command -- commit --> Hooks["prepare_hooks → dry_run → confirm → apply_saved"]
    Command -- apply --> Apply["flows.apply_saved"]
    Command -- resume --> Resume["flows.resume"]
    Command -- inspect --> Inspect["doctor / sessions / show-plan / explain"]
    Command -- utility --> Utility["init / undo / selftest"]

    Default --> Result(["human output or JSON"])
    RewriteFlow --> Result
    PlanOnly --> Result
    Hooks --> Result
    Apply --> Result
    Resume --> Result
    Inspect --> Result
    Utility --> Result
```

The default command and `plan` reject staged changes unless
`--include-staged` is set. `commit` sets `include_staged=True` itself.

## Plan-and-apply lifecycle

Bare `atc` uses `flows.dry_run()` to create a persisted plan and then
`flows.apply_saved()` to apply the latest plan. The split keeps `atc plan` and
`atc apply` reusable while preserving the same validation path.

```mermaid
sequenceDiagram
    actor U as User
    participant C as cli.py
    participant F as flows.py
    participant S as scanner.py
    participant P as planner.py
    participant D as session.py
    participant M as committer.py
    participant G as Git

    U->>C: atc [--yes]
    C->>F: dry_run(git, cfg)
    F->>F: preflight()
    F->>D: create session
    F->>S: scan()
    S->>G: status + diffs + untracked files
    S-->>F: WorktreeSnapshot
    F->>D: write snapshot.json
    F->>P: plan(snapshot)
    P-->>F: CommitPlan
    F->>D: write plan.json + latest plan
    F-->>C: rendered plan
    C->>U: confirm unless --yes
    U-->>C: yes
    C->>F: apply_saved(latest)
    F->>F: preflight()
    F->>S: rescan()
    F->>F: compare fingerprint
    F->>D: create apply session + backup.patch
    F->>M: apply(plan)
    loop Every commit group
        M->>G: stage exact planned hunks
        M->>G: git commit
        M->>S: rescan remaining worktree
    end
    M-->>F: AppliedCommit records
    F->>D: write apply_log.json
    F-->>C: result
```

`apply_saved()` creates a new session even when the plan came from an earlier
planning session. This gives the apply attempt its own snapshot, backup, and
log while keeping `.git/atc/plan.json` as the latest-plan pointer.

## Preflight

`flows.preflight()` is shared by planning and applying:

```mermaid
flowchart TD
    P(["preflight"]) --> Repo{"Git repository?"}
    Repo -- No --> Fail1["fail with setup hint"]
    Repo -- Yes --> Op{"merge/rebase/cherry-pick/revert active?"}
    Op -- Yes --> Fail2["fail until operation finishes"]
    Op -- No --> Head{"Repository has a commit?"}
    Head -- No --> Fail3["require initial commit"]
    Head -- Yes --> Index{"Staged changes allowed?"}
    Index -- No --> Fail4["unstage or use --include-staged"]
    Index -- Yes --> Paths{"All --paths targets exist?"}
    Paths -- No --> Fail5["report missing path"]
    Paths -- Yes --> OK(["continue"])
```

## Scan lifecycle

`scanner.scan()` converts mutable Git state into a `WorktreeSnapshot` with a
stable fingerprint.

```mermaid
flowchart TD
    Start(["scan(git, cfg)"]) --> Meta["read root, branch, HEAD, porcelain status"]
    Meta --> Allow["load repo-root .atcallow"]
    Allow --> DiffMode{"include_staged?"}
    DiffMode -- No --> Worktree["git diff"]
    DiffMode -- Yes --> HeadDiff["git diff HEAD"]
    Worktree --> Parse["parse tracked patch"]
    HeadDiff --> Parse
    Parse --> Scope["filter by --paths"]
    Scope --> Safe["evaluate path and binary safety"]
    Safe --> Rename["synthesize pure-rename hunks from status"]
    Rename --> Untracked["list non-ignored untracked files"]
    Untracked --> Batch["batch safe untracked no-index diff"]
    Batch --> Fix["normalize paths, IDs, fingerprints"]
    Fix --> FP["hash HEAD + branch + file states + hunk fingerprints + untracked bytes"]
    FP --> Snapshot(["WorktreeSnapshot"])
```

Tracked changes use `git diff` or `git diff HEAD`. Untracked files are absent
from ordinary Git diffs, so the scanner synthesizes added-file patches in one
batched no-index operation. Pure renames receive a file-level synthetic hunk so
they cannot disappear from planning.

### Change identity

Each `Hunk` has both a human-readable ID (`path::hunk::N`) and a content
fingerprint derived from its path, added/removed lines, header, and leading
context. IDs make plans readable; fingerprints allow the stager to find the
same change after earlier commits shift line numbers.

## Local change graph

`change_analysis.build_change_graph()` runs without a provider call. Each safe
hunk becomes a `ChangeUnit` with path, status, language, change kind, nearby
lines, and—where available—a Python symbol.

```mermaid
flowchart LR
    H["Safe hunks"] --> U["ChangeUnit per hunk"]
    U --> SameFile["same_file links"]
    U --> SameSymbol["same_symbol links"]
    U --> Tests["test/source stem links"]
    U --> Imports["import links"]
    SameFile --> G["ChangeGraph"]
    SameSymbol --> G
    Tests --> G
    Imports --> G
    G --> Planner["Provider planning context"]
```

Links are deliberately sparse: adjacent units bridge a file or symbol, and one
bridge represents each test/source or importing/imported relationship. This
avoids quadratic prompt growth.

## Adaptive planning lifecycle

The planner estimates input size with a four-characters-per-token heuristic.
It chooses a direct path when the complete prompt fits `full_change_limit` and
an evidence-assisted path otherwise.

```mermaid
flowchart TD
    Start(["planner.plan"]) --> Context["context pack + change graph + full safe diff"]
    Context --> Initial{"Valid initial-plan cache hit?"}
    Initial -- Yes --> Candidate["reuse cached plan"]
    Initial -- No --> Fits{"Estimated prompt ≤ full-change limit?"}
    Fits -- Yes --> Direct["one complete planning request"]
    Fits -- No --> Chunk["pack graph-related hunks into evidence chunks"]
    Chunk --> Map["parallel run_map requests<br/>bounded by --max-parallel"]
    Map --> Lead["run_reduce in bounded lead batches"]
    Direct --> Save["cache initial plan"]
    Lead --> Save
    Save --> Review{"review enabled and plan not already bounded-reviewed?"}
    Candidate --> Review
    Review -- Yes --> Split["one split/repair review request"]
    Review -- No --> Template["apply optional message template"]
    Split --> ReviewValid{"Reviewed plan valid?"}
    ReviewValid -- Yes --> Template
    ReviewValid -- No --> LeadValid{"Original plan valid?"}
    LeadValid -- Yes --> Keep["keep original plan"]
    LeadValid -- No --> Repair["provider repair → local repair fallback"]
    Keep --> Template
    Repair --> Template
    Template --> Validate["exact coverage + messages + dependencies"]
    Validate --> Final(["CommitPlan"])
```

### Context pack

`build_context_pack()` contains repository name, branch, base HEAD, recent
commit subjects, diffstat, file/hunk inventory, and up to 4,000 characters from
supported instruction files unless disabled.

### Large-change path

Evidence chunks preserve complete hunks and prefer locally connected changes.
`run_map()` caches each structured `ChunkReview` and preserves deterministic
result order despite parallel execution. Lead planning partitions oversized
inputs into bounded batches; failed or incomplete batches are split and
retried. This may produce a bounded-plan warning, in which case the batch lead
work includes the final review instead of issuing another global review.

### Plan validation

The final plan is accepted only when:

- group IDs are unique;
- every safe hunk is assigned exactly once;
- no group is empty or references an unknown hunk;
- declared file paths exactly match the group's hunks;
- dependencies reference known, earlier groups;
- excluded entries have reasons; and
- every commit message passes the message validator.

Verbose multi-hunk groups must also include a rationale.

## Provider lifecycle

Both providers implement `AIProvider.complete_json()` and return the same validated
JSON shape. The OpenAI-compatible adapter streams partial output into Rich's live
progress display; Anthropic remains a bounded request/response adapter.

```mermaid
sequenceDiagram
    participant P as planner.py
    participant A as provider adapter
    participant H as OpenAI client / base HTTP helper
    participant API as Provider API

    P->>A: complete_json(system, user, limits)
    A->>H: JSON request payload + headers
    loop Configured attempts within timeout
        H->>API: streaming or bounded HTTP request
        alt success
            API-->>H: SSE chunks or response
            H-->>A: rolling response preview
            H-->>A: decoded JSON
        else retryable HTTP/network failure
            API-->>H: error / Retry-After
            H->>H: bounded jittered backoff
        end
    end
    A->>A: record token usage
    A->>A: extract JSON object from text/tool response
    A-->>P: dict
```

Provider error details are redacted for bearer tokens and common key formats.
OpenAI-compatible requests accumulate SSE deltas and replace the live status with
a whitespace-normalized preview of the latest response fragment. The preview is
bounded and transient, so it neither grows memory with a second response copy nor
clogs terminal history. The adapter can also reduce requested output tokens when
an endpoint reports a parseable context-window overflow. Both providers normalize
usage into the shared prompt/completion/total counters.

## Exact staging and commit lifecycle

The model never supplies a patch to execute. It selects hunk IDs; the local
stager reconstructs patches from the frozen snapshot and current worktree.

```mermaid
flowchart TD
    Group(["Next CommitGroup"]) --> Resolve["resolve planned hunk IDs"]
    Resolve --> Types{"File-level operation?"}
    Types -- add/delete/rename/mode --> Whole["use exact Git path operation"]
    Types -- ordinary or partial add --> Current["read current worktree diff"]
    Current --> Match["match content fingerprint or added/removed lines"]
    Match --> Found{"All requested content found?"}
    Found -- No --> Fail["unstage touched paths and stop"]
    Found -- Yes --> Patch["build selected-hunk patch"]
    Patch --> Check["git apply --cached --check"]
    Check --> Stage["git apply --cached"]
    Whole --> Verify["verify index contains no extra paths"]
    Stage --> Verify
    Verify --> Commit["git commit"]
    Commit --> Head{"HEAD advanced?"}
    Head -- No --> Fail
    Head -- Yes --> Rescan["rescan worktree"]
    Rescan --> More{"More groups?"}
    More -- Yes --> Group
    More -- No --> Done(["apply log"])
```

When `include_staged` is enabled, the committer first restores planned staged
paths to the worktree view and rescans. This lets every group pass through the
same exact staging logic instead of trusting the pre-existing index.

## Session, apply, and resume state

```mermaid
stateDiagram-v2
    [*] --> Planned: snapshot.json + plan.json
    Planned --> Applying: backup.patch written
    Applying --> Applying: group committed + worktree rescanned
    Applying --> Failed: staging or commit fails
    Applying --> Complete: every group committed
    Failed --> Applying: atc resume and remaining fingerprints match
    Planned --> Applying: atc apply / atc resume
    Complete --> [*]
```

`latest_incomplete_session()` treats a plan with no log as resumable and a log
with `pending` or `failed` entries as incomplete. Resume does not compare the
whole original repository fingerprint because earlier groups may already have
advanced HEAD. Instead, it verifies that every fingerprint required by the
remaining groups is still present.

## Hook lifecycle

Only `atc commit` calls `prepare_hooks()` automatically.

```mermaid
flowchart TD
    Start(["atc commit"]) --> Skip{"--no-verify or --hooks skip?"}
    Skip -- Yes --> Disabled["set no_verify; skip hooks"]
    Skip -- No --> Each{"--hooks each?"}
    Each -- Yes --> GitEach["Git runs hooks on every commit"]
    Each -- No --> Msg{"Executable commit-msg hook?"}
    Msg -- Yes --> GitEach
    Msg -- No --> Pre{"Executable pre-commit hook?"}
    Pre -- No --> Disabled
    Pre -- custom --> GitEach
    Pre -- generated by pre-commit --> Scan["scan safe changed files"]
    Scan --> Run["pre-commit run --files ..."]
    Run --> Changed{"Hook modified files?"}
    Changed -- Yes, fewer than 3 attempts --> Run
    Changed -- No, passed --> Reuse["set no_verify; reuse result for atomic commits"]
    Changed -- Failed --> Error(["preflight error"])
```

## Rewrite lifecycle

`--amend` and `--squash` are explicit history-rewriting paths:

```mermaid
flowchart TD
    Start(["atc --amend / --squash"]) --> Check["require HEAD with parent"]
    Check --> Confirm["confirm unless --yes"]
    Confirm --> Save["remember HEAD and original subject"]
    Save --> Reset["git reset --mixed HEAD~1"]
    Reset --> Plan["plan the restored diff"]
    Plan --> Safe{"Any safe groups?"}
    Safe -- No --> Restore["git reset --soft original HEAD"]
    Safe -- Yes --> Mode{"--squash?"}
    Mode -- No --> Atomic["keep planned groups"]
    Mode -- Yes --> One["collapse hunks into one group<br/>reuse original subject"]
    Atomic --> Apply["apply temporary plan"]
    One --> Apply
```

The committer date is nudged when necessary so recreating identical content
and metadata in the same second does not reproduce the old SHA.

## Failure boundaries and invariants

- Provider output is data, not executable instructions.
- Unsafe files never become safe merely because the provider references them.
- Plans cannot omit, duplicate, invent, or reorder dependent safe hunks.
- Saved plans cannot apply to a changed whole-worktree fingerprint.
- Resume can only apply remaining hunks whose content still matches.
- Staging cannot silently expand from selected hunks to a whole file.
- Apply stops at the first failed group and removes touched index entries.
- `atc` never pushes or performs a broad fallback commit.
