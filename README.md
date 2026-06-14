# atc — Atomic Commits CLI

`atc` turns a dirty Git worktree into meaningful, atomic commits using AI. It scans your changes, reviews the full change set, and proposes specific, behavior-grouped commits. It never creates generic `update file` commits.

> Status: under active construction. See `implementation.md` for the full spec and `operation.md` for build progress.

## Why atomic commits

Small, behavior-scoped commits make history reviewable, bisectable, and revertible. `atc` automates the tedious staging/splitting while keeping you in control: it plans first, applies only on request, and stops rather than committing junk.

## Install

```bash
pipx install .
atc --version
```

## Provider setup

OpenAI-compatible:

```bash
export ATC_OPENAI_API_KEY=sk-...
export ATC_OPENAI_BASE_URL=https://api.openai.com/v1
export ATC_OPENAI_MODEL=gpt-4o-mini
```

Anthropic:

```bash
export ATC_ANTHROPIC_API_KEY=sk-ant-...
export ATC_ANTHROPIC_MODEL=claude-3-5-sonnet-latest
```

Validate setup:

```bash
atc doctor
```

## Quickstart

```bash
atc              # compact dry-run plan (no commits)
atc --verbose    # ultra-specific dry-run plan
atc --apply      # apply the saved plan
atc --apply --now --verbose   # plan and commit in one run
```

## Compact vs verbose

- `--compact` (default): fewer commits, still atomic by behavior.
- `--verbose`: prefer one contextual hunk per commit.

## Safety model

`atc` never commits `.env`, ignored files, caches, or runtime artifacts. Binary files are refused by default. It uses no zero-context patches and makes no broad fallback commits.

## Recovery / resume

```bash
atc resume       # resume latest interrupted session
atc sessions     # list sessions
```

A `backup.patch` is written before applying, for manual recovery.

## Important

`atc` commits locally only. It never pushes, never rewrites history, and never edits or formats your code.
