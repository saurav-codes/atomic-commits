# Atomic Commits

[![Install with skills](https://img.shields.io/badge/skills-atomic--commits-black)](https://github.com/saurav-codes/atomic-commits)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://conventionalcommits.org)

**One dirty working tree in. One clean, reviewable commit history out.**

An AI agent skill that splits your uncommitted changes into granular, conventional atomic commits — in the right dependency order. Your agent does it with Git alone, instantly.

## Install

**Universal (works everywhere):**
```bash
npx skills add saurav-codes/atomic-commits
```

**Manual:** clone into your agent's skills folder (`npx skills add` already handles the right path):
```bash
git clone https://github.com/saurav-codes/atomic-commits.git <skills-dir>/atomic-commits
```

## Use

Tell your agent:
> "Commit my changes atomically."

That's it. The agent inspects the diff, groups changes, and commits each group.

## How it commits

Groups are committed in this order, so dependencies land first:

1. `build` / `chore(deps)` — dependencies & config
2. `refactor` — pure moves, no behavior change
3. `feat` / `fix` — features & fixes
4. `test` — tests & fixtures
5. `docs` — documentation

Every message follows `type(scope): imperative verb` — no "misc fixes".

## Safety

Secrets, `.env` files, and build artifacts are never committed.

## Examples

See [skills/atomic-commits/references/examples.md](skills/atomic-commits/references/examples.md).

## License

[MIT](LICENSE)
