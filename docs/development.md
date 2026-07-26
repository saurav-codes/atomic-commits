# Development

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Python 3.11+, Typer, Rich, Pydantic 2, `tomli_w`, and the OpenAI SDK. Provider
HTTP for Anthropic uses the standard library.

## Checks

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest
```

`pytest` runs with coverage and fails below 60%. For a fast loop:

```bash
.venv/bin/pytest -o addopts='-q' tests/unit/test_planner.py
```

After CLI changes, verify the surface:

```bash
.venv/bin/atc --version
.venv/bin/atc --help
.venv/bin/atc commit --help
.venv/bin/atc selftest
```

`selftest` builds throwaway repos. Add `--live` only to call a real provider.

## Tests

```text
tests/unit/        parsers, safety, config, planning, providers, hooks
tests/integration/ real temporary Git repos and CLI/flow behavior
```

Unit tests cover deterministic policy. Integration tests cover Git's index,
renames, modes, untracked files, staging, and sessions.

## Extension points

- **CLI option**: parse it in `cli.main()` or the command; carry shared state in
  `RunConfig`; put real logic in `flows.py` or the owning module; keep `--json`
  output one object on stdout and non-zero on error; update `--help` and tests.
- **Provider**: implement `AIProvider.complete_json()` in `providers/`; reuse
  the shared HTTP/retry and secret redaction in `base.py`; add credential
  resolution in `config.py` and construction in `providers/__init__.py`; test
  JSON extraction and retries without the network.
- **Planning**: keep `validate_plan()` invariants — exact safe-hunk coverage,
  unique groups, valid dependency order, matching paths, exclusions with
  reasons, valid messages. Test both direct and large-change paths.
- **Scanning/staging**: use real temporary Git repos. Cover staged plus
  unstaged, untracked, partial hunks, rename/delete/mode, binary policy, path
  filters, and subdirectory invocations. No whole-file fallback on a failed
  match.

## Docs

Code, generated `--help`, and tests are authoritative. Keep docs short and
current with behavior:

- `README.md` — user setup, commands, safety.
- `docs/architecture.md` — modules and invariants.
- `docs/development.md` — this file.
- `CHANGELOG.md` — released changes only.
