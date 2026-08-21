# Atomic Commits (AI Agent Skill)

[![Install with skills](https://img.shields.io/badge/skills-atomic--commits-black)](https://github.com/saurav-codes/atomic-commits)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://conventionalcommits.org)

An **AI Agent Skill** that teaches coding assistants (Antigravity, Claude Code, Cursor, Codex, Grok) how to turn dirty Git working trees into clean, granular, conventional **atomic commits** in topological dependency order.

---

## Why an AI Skill?

Traditional atomic commit tools rely on heavy CLI wrappers that chunk diffs and send dozens of Map-Reduce API requests to external LLMs. This causes:
- 💸 **High token bills**: Paying for repeated diff contexts and JSON schemas.
- ⏳ **Slow commits**: 5–10 minute wait times for multi-stage network roundtrips.
- 💥 **Flaky patch staging**: `git apply --cached` errors from line-number drift.

**The Skill approach solves this completely:**
- ⚡ **Zero extra API cost & instant execution**: Leverages the agent's existing workspace context.
- 🎯 **Topological flow**: Automatically stages dependencies first, then refactors, core features, tests, and documentation.
- 🛡️ **Built-in safety**: Excludes secrets, `.env` files, credentials, and transient build artifacts.
- 📝 **Strict conventional commits**: Enforces `<type>(<scope>): <imperative verb> <behavior>` with zero generic filler messages.

---

## Installation

### 1. Universal Install via `npx skills` (Vercel Skills)
```bash
npx skills add saurav-codes/atomic-commits
```

### 2. Antigravity / Gemini CLI
Add directly to your workspace:
```bash
mkdir -p .agents/skills
git clone https://github.com/saurav-codes/atomic-commits.git .agents/skills/atomic-commits
```
Or globally for all your projects:
```bash
git clone https://github.com/saurav-codes/atomic-commits.git ~/.gemini/config/skills/atomic-commits
```

### 3. Claude Code / Cursor
Add to your project's `.claude/skills/` or `.cursor/skills/` directory:
```bash
git clone https://github.com/saurav-codes/atomic-commits.git .claude/skills/atomic-commits
```

---

## How It Works

Once installed, simply instruct your AI agent:

> *"Commit my changes atomically."*
> or
> *"Split my working tree into conventional commits."*

The agent will:
1. **Inspect** `git status` and `git diff --stat`.
2. **Filter** out sensitive/untracked files.
3. **Partition** changes following topological dependency order:
   - `build` / `chore(deps)` ➔ `refactor` ➔ `feat`/`fix` ➔ `test` ➔ `docs`
4. **Stage & Commit** each group sequentially.
5. **Report** a clean summary table of created commits.

---

## Commit Ordering Rules

```text
1. Dependencies & Config   ➔ build(deps): / chore(config):
2. Pure Refactors          ➔ refactor(scope): (zero behavior change)
3. Core Feature / Fixes    ➔ feat(scope): / fix(scope):
4. Tests & Fixtures        ➔ test(scope):
5. Documentation           ➔ docs(scope):
```

---

## Reference Examples

See [skills/atomic-commits/references/examples.md](skills/atomic-commits/references/examples.md) for detailed breakdown scenarios, including refactor/feature separation and multi-package handling.

---

## License

[MIT](LICENSE)
