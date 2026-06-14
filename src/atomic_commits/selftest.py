"""Self-test harness for generating agent-friendly atc repro cases."""

from __future__ import annotations

import asyncio
import json
import random
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import planner
from .committer import Committer
from .config import RunConfig, resolve_provider_credentials
from .flows import preflight
from .git_client import GitClient
from .providers import build_provider
from .scanner import scan


@dataclass
class SelfTestCaseResult:
    case_id: str
    profile: str
    seed: int
    repo: str
    status: str
    planned_commits: int = 0
    committed: int = 0
    error: str = ""


@dataclass
class SelfTestSummary:
    cases_run: int
    failures: list[SelfTestCaseResult]
    log_path: Path


class DeterministicProvider:
    """Offline provider that creates one commit per safe hunk."""

    async def complete_json(
        self, *, system: str, user: str, schema_name: str, max_tokens: int, temperature: float
    ) -> dict[str, Any]:
        payload = json.loads(user)
        if schema_name == "ChunkReview":
            return {
                "chunk_id": payload["chunk_id"],
                "summary": "self-test review",
                "detected_concerns": [],
                "suggested_groups": [],
                "risky_hunks": [],
                "message_terms": {},
            }

        groups = []
        for idx, hunk in enumerate(payload["context"]["hunk_inventory"], start=1):
            path = hunk["file_path"]
            stem = Path(path).stem.replace(" ", "-")[:24] or "file"
            groups.append(
                {
                    "group_id": f"selftest-{idx}",
                    "message": f"selftest: record {stem} hunk {idx}",
                    "rationale": "deterministic self-test grouping",
                    "hunk_ids": [hunk["hunk_id"]],
                    "file_paths": [path],
                    "risk": "low",
                }
            )
        return {
            "version": "1",
            "mode": payload["mode"],
            "repo_fingerprint": payload["repo_fingerprint"],
            "base_head": payload["base_head"],
            "groups": groups,
            "excluded": [],
            "warnings": [],
        }


def run_selftest(
    *,
    work_dir: Path,
    cfg_template: RunConfig,
    cases: int = 1,
    seed: int = 0,
    profile: str = "broad",
    live: bool = False,
    log_path: Path | None = None,
) -> SelfTestSummary:
    work_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_path or work_dir / "failures.jsonl"
    failures: list[SelfTestCaseResult] = []

    for offset in range(cases):
        case_seed = seed + offset
        case_id = f"{profile}-{case_seed}"
        repo = work_dir / "repos" / case_id
        if repo.exists():
            shutil.rmtree(repo)
        repo.mkdir(parents=True)
        result = _run_case(repo, cfg_template, case_id, profile, case_seed, live)
        if result.status != "passed":
            failures.append(result)
            _append_failure(log_path, result, repo)

    return SelfTestSummary(cases_run=cases, failures=failures, log_path=log_path)


def _run_case(
    repo: Path, cfg_template: RunConfig, case_id: str, profile: str, seed: int, live: bool
) -> SelfTestCaseResult:
    rng = random.Random(seed)
    result = SelfTestCaseResult(
        case_id=case_id,
        profile=profile,
        seed=seed,
        repo=str(repo),
        status="failed",
    )
    try:
        _init_repo(repo)
        include_staged = _mutate_repo(repo, rng, profile)
        expected_tree = _tree_snapshot(repo)

        cfg = RunConfig(
            mode="verbose",
            repo=repo,
            include_staged=include_staged,
            provider=cfg_template.provider,
            model=cfg_template.model,
            base_url=cfg_template.base_url,
            api_key_env=cfg_template.api_key_env,
            api_key=cfg_template.api_key,
            max_chunk_tokens=cfg_template.max_chunk_tokens,
            max_reducer_tokens=cfg_template.max_reducer_tokens,
            temperature=cfg_template.temperature,
            no_verify=cfg_template.no_verify,
            json_output=cfg_template.json_output,
            debug=cfg_template.debug,
        )
        git = GitClient(repo)
        preflight(git, cfg)
        snapshot = scan(git, cfg)
        provider = build_provider(resolve_provider_credentials(cfg)) if live else DeterministicProvider()
        plan = asyncio.run(planner.plan(provider, git, snapshot, cfg))
        result.planned_commits = len(plan.groups)
        applied = Committer(git, cfg).apply(plan)
        result.committed = sum(1 for item in applied if item.status == "committed")

        failed = [item for item in applied if item.status != "committed"]
        if failed:
            raise RuntimeError(f"apply failed: {failed[0].detail}")
        if result.committed != result.planned_commits:
            raise RuntimeError("not every planned commit was applied")
        if _status(repo):
            raise RuntimeError(f"worktree not clean: {_status(repo)}")
        if _tree_snapshot(repo) != expected_tree:
            raise RuntimeError("final worktree does not match generated dirty tree")

        result.status = "passed"
        return result
    except Exception as exc:  # noqa: BLE001 - this is a repro harness.
        result.error = f"{type(exc).__name__}: {exc}"
        return result


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "selftest@example.com")
    _git(repo, "config", "user.name", "atc self-test")
    _git(repo, "config", "commit.gpgsign", "false")
    _write(repo / "README.md", "# Self Test\n\nInitial docs.\n")
    _write(repo / "src/app.py", _numbered_lines("value", 80))
    _write(repo / "docs/old.md", "old docs\n")
    _write(repo / "notes/keep.txt", "keep me\n")
    _write(repo / "script.sh", "#!/bin/sh\necho hi\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "seed repo")


def _mutate_repo(repo: Path, rng: random.Random, profile: str) -> bool:
    include_staged = False
    lines = (repo / "src/app.py").read_text().splitlines()
    for idx in sorted(rng.sample(range(10, 70), 3)):
        lines[idx] = f"value_{idx} = {rng.randint(1000, 9999)}"
    _write(repo / "src/app.py", "\n".join(lines) + "\n")
    _write(repo / "README.md", "# Self Test\n\nInitial docs.\n\nGenerated note.\n")
    (repo / "docs/old.md").unlink()
    _write(repo / f"new_{rng.randint(100, 999)}.txt", f"generated {rng.random()}\n")

    if profile == "broad":
        include_staged = True
        if rng.choice([True, False]):
            _git(repo, "add", "README.md")
        _write(repo / "dir name/file name.txt", "path with spaces\n")
        _write(repo / "eof.txt", "no newline")
        _write(repo / "crlf.txt", "a\r\nb\r\nc\r\n")
        _write(repo / "staged/new_file.txt", f"staged add {rng.randint(1000, 9999)}\n")
        _git(repo, "add", "staged/new_file.txt")
        _git(repo, "mv", "notes/keep.txt", "notes/renamed.txt")
        (repo / "script.sh").chmod(0o755)
        _git(repo, "update-index", "--chmod=+x", "script.sh")

    return include_staged


def _append_failure(path: Path, result: SelfTestCaseResult, repo: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        **asdict(result),
        "status_porcelain": _status(repo),
        "head": _git(repo, "rev-parse", "HEAD").stdout.strip(),
        "recent_log": _git(repo, "log", "--oneline", "-8").stdout.splitlines(),
        "worktree_diff": _git(repo, "diff", "--binary", check=False).stdout,
        "cached_diff": _git(repo, "diff", "--cached", "--binary", check=False).stdout,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _tree_snapshot(repo: Path) -> dict[str, tuple[int, str]]:
    snapshot: dict[str, tuple[int, str]] = {}
    for path in sorted(repo.rglob("*")):
        if ".git" in path.relative_to(repo).parts or not path.is_file():
            continue
        rel = path.relative_to(repo).as_posix()
        snapshot[rel] = (path.stat().st_mode & 0o777, sha256(path.read_bytes()).hexdigest())
    return snapshot


def _status(repo: Path) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all").stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _numbered_lines(prefix: str, count: int) -> str:
    return "".join(f"{prefix}_{idx} = {idx}\n" for idx in range(count))


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=check,
        capture_output=True,
        text=True,
    )
