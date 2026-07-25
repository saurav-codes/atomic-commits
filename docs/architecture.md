# ATC — How the Project Works (Architecture & Flow)

This document explains how `atc` (**A**tomic **C**ommits CLI) works end to end,
backed by Mermaid diagrams. Every node below maps to a real module, function,
or data model in `src/atomic_commits/`.

> `atc` turns **one dirty Git worktree** into the **greatest useful number of
> meaningful, atomic commits**. It does local, token-free analysis first
> (symbols, tests, imports, links), then asks a strong AI model to make the
> final commit plan. It stages exact hunks, commits each group in dependency
> order, and rescans the worktree after every commit. Safety first: it never
> commits `.env`, ignored/generated/secret files, or (by default) binaries.

## Tech stack

| Concern        | Choice                                              |
|----------------|-----------------------------------------------------|
| Language       | Python >= 3.11                                      |
| CLI framework  | [Typer](https://typer.tiangolo.com/)                |
| Terminal UI    | [Rich](https://rich.readthedocs.io/)                |
| Data models    | [Pydantic](https://docs.pydantic.dev/) v2           |
| Provider HTTP  | stdlib `urllib` (no SDK dependency)                |
| Build backend  | Hatchling                                           |
| Entry point    | `atc = "atomic_commits.cli:app"` (in `pyproject.toml`) |

---

## 1. Module map (component layers)

The codebase is organized into clear layers. The CLI stays thin; `flows.py`
orchestrates everything; the heavy logic lives in the planning, scanning, and
apply modules.

```mermaid
flowchart LR
    subgraph CLI["CLI layer"]
        cli["cli.py<br/>Typer app + command parsing<br/>builds RunConfig"]
    end

    subgraph Orch["Orchestration"]
        flows["flows.py<br/>preflight - hooks - _build_plan<br/>dry_run - apply_now - apply_saved - resume"]
        config["config.py<br/>resolve_provider_credentials<br/>(CLI > env > file > error)"]
        output["output.py<br/>Rich rendering + progress"]
        errors["errors.py<br/>typed AtcError hierarchy"]
    end

    subgraph Input["Freeze the worktree"]
        gitclient["git_client.py<br/>wraps the git CLI"]
        scanner["scanner.py<br/>scan() -> WorktreeSnapshot"]
        diffparser["diff_parser.py<br/>parse_patch - build_patch_for_hunks"]
        fingerprints["fingerprints.py<br/>stable content hashes"]
        safety["safety.py<br/>exclude secrets/binaries/caches<br/>+ .atcallow allowlist"]
    end

    subgraph Analysis["Local token-free facts"]
        change["change_analysis.py<br/>build_change_graph() -> ChangeGraph<br/>symbols - imports - tests - links"]
    end

    subgraph Plan["AI planning"]
        planner["planner.py<br/>plan() direct vs large path<br/>+ split review + validation"]
        prompts["prompts.py<br/>system/user prompt templates"]
        providers["providers/<br/>openai_compatible.py<br/>anthropic.py - base.py"]
    end

    subgraph Valid["Validation"]
        vschema["validators/plan_schema.py<br/>exact coverage - unique IDs<br/>dependency order"]
        vmsg["validators/commit_messages.py<br/>message format checks"]
    end

    subgraph Apply["Exact Git execution"]
        stager["stager.py<br/>stage_group() rematch + stage"]
        committer["committer.py<br/>apply() per-group commit loop"]
    end

    subgraph Store["Session storage"]
        session["session.py<br/>SessionStore under .git/atc/sessions/"]
    end

    subgraph Models["Data (Pydantic)"]
        models["models.py<br/>WorktreeSnapshot - Hunk - ChangeGraph<br/>CommitPlan - CommitGroup - AppliedCommit"]
    end

    cli --> flows
    flows --> config
    flows --> scanner
    flows --> planner
    flows --> committer
    flows --> session
    flows --> output

    scanner --> diffparser
    scanner --> fingerprints
    scanner --> safety
    scanner --> gitclient

    planner --> change
    planner --> prompts
    planner --> providers
    planner --> vschema
    planner --> vmsg

    committer --> stager
    committer --> scanner
    committer --> gitclient
    stager --> diffparser
    stager --> gitclient

    scanner --> models
    change --> models
    planner --> models
    committer --> models
    session --> models
    committer --> errors
    planner --> errors
```

---

## 2. Command surface

`atc` exposes one default command (no subcommand) plus several explicit
subcommands. The default command and `atc commit` are the main entry points.

```mermaid
flowchart TD
    Start([User runs atc ...]) --> Decision{Subcommand?}

    Decision -- "none (default)" --> Default["atc (default)<br/>dry-run plan<br/>--apply / --now / --yes to commit<br/>--amend / --squash to rewrite last commit"]
    Decision -- "commit" --> CommitCmd["atc commit<br/>one-command plan + apply<br/>(--hooks once|each|skip)"]
    Decision -- "doctor" --> Doctor["atc doctor<br/>validate git + provider config"]
    Decision -- "resume" --> Resume["atc resume<br/>resume interrupted session"]
    Decision -- "sessions" --> Sessions["atc sessions<br/>list saved sessions"]
    Decision -- "show-plan" --> ShowPlan["atc show-plan<br/>pretty-print a saved plan"]
    Decision -- "explain" --> Explain["atc explain &lt;group-id&gt;<br/>rationale + hunk diffs"]
    Decision -- "init" --> Init["atc init<br/>interactive config wizard"]
    Decision -- "undo" --> Undo["atc undo &lt;session-id&gt;<br/>reverse backup.patch"]
    Decision -- "selftest" --> Selftest["atc selftest<br/>deterministic install check<br/>(--live to hit the provider)"]

    Default --> MainFlow[Planning + Apply pipeline]
    CommitCmd --> MainFlow
    Resume --> ApplyOnly[Apply remaining groups]
    Doctor --> LocalCheck[Local preflight only]
    Sessions --> LocalCheck
    ShowPlan --> LocalCheck
    Explain --> LocalCheck
    Init --> LocalCheck
    Undo --> LocalCheck
    Selftest --> LocalCheck
```

---

## 3. Main flow: `atc commit` (end to end)

This is the heart of the tool — `atc commit` (or the default command with
`--apply --now`). It plans, shows the plan, asks for confirmation, stages exact
changed regions, and creates the commits.

```mermaid
flowchart TD
    Run([atc commit --yes]) --> Args["cli.py: parse flags -> RunConfig<br/>(mode, provider, model, paths, limits)"]
    Args --> Hooks["flows.prepare_hooks()<br/>run pre-commit ONCE if installed<br/>(--hooks once|each|skip)"]
    Hooks --> Preflight["flows.preflight()<br/>git repo? no in-progress ops?<br/>has commits? staged policy?"]
    Preflight -->|fail| ErrAtc([AtcError -> red message / JSON error])
    Preflight -->|ok| BuildPlan["flows.dry_run() -> _build_plan()"]

    subgraph BP["_build_plan()"]
        direction TB
        Resolve["config.resolve_provider_credentials()<br/>CLI > env > config file > error"]
        Scan["scanner.scan() -> WorktreeSnapshot<br/>freeze HEAD/branch/status<br/>parse diffs - safety filter - fingerprint"]
        WriteSnap["session.write_snapshot()"]
        BuildProv["providers.build_provider()<br/>openai-compatible OR anthropic"]
        Plan["planner.plan() -> CommitPlan<br/>(see diagram 4)"]
        WritePlan["session.write_plan()<br/>.git/atc/sessions/&lt;id&gt;/plan.json"]
        PrintPlan["output.print_plan()"]
        Resolve --> Scan --> WriteSnap --> BuildProv --> Plan --> WritePlan --> PrintPlan
    end

    BuildPlan --> BP
    PrintPlan --> Confirm{User confirms?<br/>(skipped by --yes)}
    Confirm -->|no| Abort([aborted])
    Confirm -->|yes| Apply["flows.apply_saved()"]

    subgraph AP["apply_saved() / committer.apply() (see diagram 5)"]
        direction TB
        ReScan["rescan worktree<br/>fingerprint must match plan"]
        Backup["git.backup_patch() -> write backup.patch"]
        Loop["for each CommitGroup in dependency order:<br/>stage_group -> verify only -> commit -> rescan"]
        Log["session.write_apply_log()"]
        ReScan --> Backup --> Loop --> Log
    end

    Apply --> AP
    Log --> Done([commits created - worktree clean])
```

---

## 4. The planning pipeline (direct vs large path)

`planner.plan()` is the adaptive semantic planner. It picks one of two paths
based on whether the *entire* change fits under `--full-change-limit`
(default **120,000** estimated tokens). Both paths converge on the same review
and validation steps. Completed work is cached under
`.git/atc/sessions/cache/` keyed by snapshot + model + prompt version, so an
unchanged retry resumes.

```mermaid
flowchart TD
    PStart([planner.plan]) --> Ctx["build_context_pack()<br/>repo name - branch - recent subjects<br/>diffstat - file list - hunk inventory<br/>instruction files: AGENTS.md/.cursorrules/CLAUDE.md/README.md<br/>(<= 4000 chars total, untrusted)"]
    Ctx --> Graph["change_analysis.build_change_graph()<br/>ChangeGraph: ChangeUnits + ChangeLinks<br/>(same_file - same_symbol - test - import)"]
    Graph --> Diff["_full_diff(snapshot)"]
    Diff --> Estimate["estimate tokens for direct request<br/>(~4 chars/token heuristic)"]

    Estimate --> CacheCheck{cached plan<br/>for this snapshot?}
    CacheCheck -->|yes| ReusePlan["reuse cached CommitPlan"]
    CacheCheck -->|no| SizeCheck{direct tokens<br/><= --full-change-limit?}

    SizeCheck -->|yes| Direct["DIRECT PATH<br/>one full-change request<br/>(prompts.DIRECT_SYSTEM + direct_user)"]
    SizeCheck -->|no| Large["LARGE PATH"]

    subgraph L["Large path (map/reduce)"]
        direction TB
        Chunk["chunk_hunks()<br/>pack related regions up to<br/>--max-chunk-tokens (<= 4000)"]
        Map["run_map(): parallel evidence requests<br/>(bounded, faster) -> list[ChunkReview]"]
        Reduce["run_reduce(): ONE lead planner<br/>sees ALL evidence -> CommitPlan"]
        Chunk --> Map --> Reduce
    end

    Direct --> StoreCache["_store_plan_cache(initial)"]
    Reduce --> StoreCache
    ReusePlan --> Review

    StoreCache --> Review{--review enabled?<br/>(default: yes)}
    Review -->|yes| ReviewReq["split-review request<br/>(prompts.REVIEW_SYSTEM)<br/>tries to find more useful splits"]
    ReviewReq --> ReviewValid{reviewed plan<br/>valid?}
    ReviewValid -->|yes| KeepReview["use reviewed plan"]
    ReviewValid -->|no, but lead valid| KeepLead["keep the valid lead plan"]
    ReviewValid -->|lead was invalid| Repair["repair request<br/>then local _repair_plan_locally()"]
    Repair --> KeepReview
    Review -->|no| SkipReview["skip review"]

    KeepReview --> Tmpl
    KeepLead --> Tmpl
    SkipReview --> Tmpl
    Tmpl["_apply_template_to_plan()<br/>fill ${scope} ${verb} ${object} if --message-template"]
    Tmpl --> Validate["_validate()<br/>validators/plan_schema + commit_messages<br/>exact coverage - unique IDs - valid msgs - order"]
    Validate -->|invalid & unrepairable| PlanErr([PlanValidationError])
    Validate -->|valid| Final([CommitPlan<br/>N meaningful atomic commits])
```

---

## 5. The apply / commit loop

`committer.apply()` runs once per group, in dependency order. Crucially, it
**rescans the worktree after every commit** and stops on the first unsafe
mismatch — it never creates a broad fallback commit.

```mermaid
flowchart TD
    AStart([committer.apply]) --> FirstScan["scan() worktree<br/>(plus restore+rescan if --include-staged)"]
    FirstScan --> Head["record head_before"]
    Head --> GroupLoop{"for each group<br/>idx/total"}

    GroupLoop --> Stage["stager.stage_group(group, snapshot)<br/>rematch fingerprints + removed/added lines<br/>whole-file add/delete/rename/mode<br/>else patch-stage via git apply --cached"]
    Stage --> StageOK{staged hunks?}
    StageOK -->|none| StageFail["PatchApplyError"]
    StageOK -->|ok| Verify["stager._verify_only()<br/>only planned paths may be staged"]
    Verify --> Commit["git.commit(message, no_verify?)"]
    Commit --> HeadChk{"HEAD advanced?"}
    HeadChk -->|no| HeadFail["record: did not advance HEAD"]
    HeadChk -->|yes| Rescan["scan() worktree again<br/>(line numbers changed)"]
    Rescan --> Next{"more groups?"}
    Next -->|yes| GroupLoop
    Next -->|no| Result([return list[AppliedCommit]<br/>all committed])

    StageFail --> Cleanup["git.restore_staged(touched paths)<br/>NEVER leave staged changes behind"]
    HeadFail --> Cleanup
    Verify -- "unplanned paths" --> Cleanup
    Cleanup --> Partial([return results - stop on first failure])
```

---

## 6. Config resolution priority

Provider settings are resolved in a strict order. The first source that
provides a value wins; a missing required value raises a `ProviderError` with a
setup hint.

```mermaid
flowchart TD
    Cfg([RunConfig from CLI]) --> L1{"1. CLI flags set?<br/>(--model --base-url --api-key-env --provider)"}
    L1 -->|yes| Use1["use CLI value"]
    L1 -->|no| L2{"2. Environment variables?<br/>ATC_OPENAI_* / ATC_ANTHROPIC_*"}
    L2 -->|yes| Use2["use env value"]
    L2 -->|no| L3{"3. Config file?<br/>.atc.toml OR ~/.config/atc/config.toml<br/>(one table per provider)"}
    L3 -->|yes| Use3["use file value"]
    L3 -->|no| L4{"4. Built-in defaults"}
    L4 -->|value exists| Use4["use default (base URL, etc.)"]
    L4 -->|required missing| ProvErr([ProviderError<br/>+ setup hint])
    Use1 --> Build["providers.build_provider()"]
    Use2 --> Build
    Use3 --> Build
    Use4 --> Build
    Build --> Out([AIProvider<br/>OpenAI-compatible | Anthropic])
```

---

## 7. Safety model (what never gets committed)

`safety.py` runs **before** the AI sees any content and **before** staging. A
`.atcallow` file in the repo root can override denylist matches with globs.

```mermaid
flowchart TD
    Path(["candidate file path"]) --> Dir{"path component in<br/>EXCLUDED_DIR_COMPONENTS?<br/>.git .hg venv node_modules<br/>__pycache__ dist build target ..."}
    Dir -->|yes| AllowChk1{"matches .atcallow?"}
    AllowChk1 -->|yes| Safe([safe])
    AllowChk1 -->|no| Exclude([excluded: denylisted path])

    Dir -->|no| Sample{"sample env?<br/>.env.example/.sample/.template/.dist"}
    Sample -->|yes| Safe
    Sample -->|no| Fn{"filename matches denylist?<br/>.env* *.pem *.key id_rsa<br/>*.log *.sqlite *.db ..."}
    Fn -->|yes| AllowChk2{"matches .atcallow?"}
    AllowChk2 -->|yes| Safe
    AllowChk2 -->|no| Exclude
    Fn -->|no| Bin{"content looks binary?<br/>(NUL byte in first 8KB)"}
    Bin -->|yes| BinPolicy{"--allow-binary AND<br/>safe binary ext?<br/>(png jpg pdf ...)"}
    BinPolicy -->|yes| Safe
    BinPolicy -->|no| Refuse(["refused: binary file"])
    Bin -->|no| Safe
```

---

## 8. Session, resume, and recovery

Every run creates a session under `.git/atc/sessions/<id>/` containing the
snapshot, plan, backup patch, and apply log. This is what makes `atc resume`
and `atc undo` possible. A `backup.patch` is written **before** any commit, so
a failed or interrupted run can be reversed.

```mermaid
flowchart TD
    Sess[".git/atc/sessions/&lt;timestamp-hash&gt;/"]
    Sess --> F1["snapshot.json<br/>(frozen WorktreeSnapshot)"]
    Sess --> F2["plan.json<br/>(CommitPlan)"]
    Sess --> F3["backup.patch<br/>(full pre-apply backup)"]
    Sess --> F4["apply_log.json<br/>(per-group status + SHA)"]
    Sess --> F5["cache/<br/>(reusable plan + evidence)"]

    RootP[".git/atc/plan.json -> latest plan"]
    RootP --> Sess

    ResumeCmd([atc resume]) --> FindLatest["session.latest_incomplete_session()"]
    FindLatest --> LoadPlan["load plan + planned snapshot"]
    LoadPlan --> DoneGroups["groups already committed (from apply_log)"]
    DoneGroups --> Remain["remaining = groups not yet committed"]
    Remain --> FpChk["verify each remaining hunk's<br/>fingerprint still present in worktree"]
    FpChk -->|missing| FpErr([FingerprintMismatchError])
    FpChk -->|ok| Reapply["committer.apply(remaining)<br/>merge new + prior results"]

    UndoCmd([atc undo &lt;id&gt;]) --> ConfirmUndo{"user confirms?<br/>(--yes skips)"}
    ConfirmUndo -->|no| AbortU([aborted])
    ConfirmUndo -->|yes| ApplyRev["git apply --reverse backup.patch"]
    ApplyRev --> Reversed([changes reverted in worktree])
```

---

## 9. Key data models

These Pydantic models (in `models.py`) flow through the pipeline. They are the
contract between modules.

```mermaid
flowchart LR
    Snap["WorktreeSnapshot<br/>repo_root - branch - head_sha<br/>status_entries - files[] - fingerprint"]
    FC["FileChange<br/>path - status - hunks[] - safety"]
    Hunk["Hunk<br/>hunk_id - file_path - header<br/>removed[] - added[] - patch - fingerprint"]
    Safe["SafetyResult<br/>safe - reasons[] - binary - excluded_path"]

    Snap --> FC --> Hunk
    FC --> Safe

    CG["ChangeGraph<br/>units[] - links[]"]
    CU["ChangeUnit<br/>unit_id - file_path - status<br/>language - symbol - change_kind"]
    CL["ChangeLink<br/>source - target - kind - reason"]
    CG --> CU
    CG --> CL

    Plan["CommitPlan<br/>version - repo_fingerprint - base_head<br/>groups[] - excluded[] - warnings[]"]
    Group["CommitGroup<br/>group_id - message - rationale<br/>hunk_ids[] - file_paths[] - risk - depends_on[]"]
    Excl["ExcludedChange<br/>path - reason - hunk_ids[]"]
    Plan --> Group
    Plan --> Excl

    Applied["AppliedCommit<br/>group_id - message - sha<br/>status - detail"]
    Review["ChunkReview<br/>(large-path evidence)"]
```

---

## 10. Summary of guarantees

- **Safety first** — never commits `.env`, ignored/generated/secret files, or
  binaries by default; `.atcallow` can override.
- **Every safe region assigned exactly once** — local validation enforces
  exact coverage, unique group IDs, valid messages, and dependency order.
- **No AI while editing** — all planning happens on a frozen snapshot; no AI
  request runs while files are being staged/committed.
- **Provider failure never falls back** — no low-quality catch-all commits; an
  invalid plan is repaired or rejected.
- **Rescan after every commit** — the committer stops on the first mismatch and
  unstages anything it touched.
- **Resumable** — plans, evidence, and backups are cached; `atc resume` and
  `atc undo` recover from interruption.

## Further reading

- `docs/implementation.md` — full product spec and design (sections 6-24).
- `docs/operation.md` — build progress log.
- `README.md` — end-user usage and config reference.
- `CHANGELOG.md` — notable behavior changes.
