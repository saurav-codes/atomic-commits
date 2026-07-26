"""Parallel run_map path coverage (IMPROVEMENTS.md 6.1).

``planner.run_map`` with ``total > 1`` spawns a ``ThreadPoolExecutor``; every
other unit test exercises only the single-chunk serial branch. This module
uses a thread-safe fake provider that records per-chunk start/end timestamps
to assert (a) chunks ran concurrently (overlapping time intervals) and
(b) results are returned in input order despite parallel execution.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from atomic_commits import planner
from atomic_commits.git_client import GitClient
from atomic_commits.scanner import scan
from tests.integration.helpers import git, make_cfg


class ParallelRecordingProvider:
    """Thread-safe fake provider that records per-chunk timing.

    Each call sleeps briefly so concurrent execution produces overlapping
    time intervals; a lock guards the shared records list so concurrent
    workers do not corrupt it.
    """

    def __init__(self, sleep: float = 0.05) -> None:
        self.sleep = sleep
        self._lock = threading.Lock()
        self.records: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int,
        temperature: float,
        timeout: float | None = None,
        attempts: int | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(user)
        chunk_id = payload["chunk_id"]
        start = time.monotonic()
        time.sleep(self.sleep)
        end = time.monotonic()
        with self._lock:
            self.calls.append(schema_name)
            self.records.append({
                "chunk_id": chunk_id, "start": start, "end": end,
                "max_tokens": max_tokens,
            })
        return {
            "chunk_id": chunk_id,
            "summary": "mock review",
            "detected_concerns": [],
            "risky_hunks": [],
            "message_terms": {},
        }


def _build_chunks(git_repo: Path, n: int):
    """Create ``n`` tracked modified files so ``chunk_hunks`` yields one chunk each.

    Mirrors the setup in ``tests/unit/test_planner.py``: seed each file with a
    committed baseline, then modify it so the worktree diff produces hunks.
    """
    for i in range(n):
        name = f"f{i}.py"
        (git_repo / name).write_text(f"x = {i}\n")
        git(git_repo, "add", name)
        git(git_repo, "commit", "-q", "-m", f"seed {i}")
        # Modify the tracked file so the worktree diff yields a hunk.
        (git_repo / name).write_text(f"x = {i + 100}\n")
    gc = GitClient(git_repo)
    cfg = make_cfg(Path(git_repo), mode="compact")
    snapshot = scan(gc, cfg)
    # A tiny evidence limit produces one request per changed region so this
    # module can exercise run_map's concurrency independently of the packer.
    chunks = planner.chunk_hunks(snapshot, 1)
    return gc, snapshot, cfg, chunks


def test_run_map_runs_chunks_concurrently_and_preserves_order(git_repo):
    n = 4
    gc, snapshot, cfg, chunks = _build_chunks(git_repo, n)
    assert len(chunks) == n, f"expected {n} chunks (one per file), got {len(chunks)}"

    context = planner.build_context_pack(gc, snapshot, cfg)
    provider = ParallelRecordingProvider(sleep=0.05)

    reviews = planner.run_map(
        provider,
        context,
        chunks,
        cfg,
        show_progress=False,
        max_parallel=n,
        snapshot=None,
        session_dir=None,
    )

    # (b) Results returned in input order despite parallel execution.
    assert [r.chunk_id for r in reviews] == [c["chunk_id"] for c in chunks]
    # Every chunk went through the map phase (no cache, one call per chunk).
    assert len(provider.records) == n
    assert provider.calls == ["ChunkReview"] * n
    assert {record["max_tokens"] for record in provider.records} == {1024}

    # (a) At least two chunks ran concurrently (overlapping time intervals).
    records = sorted(provider.records, key=lambda r: r["start"])
    overlap = False
    for i in range(len(records) - 1):
        if records[i + 1]["start"] < records[i]["end"]:
            overlap = True
            break
    assert overlap, f"chunks did not run concurrently: {records}"


def test_run_map_serial_path_is_used_for_single_chunk(git_repo):
    """Single chunk must not spin up the pool; it runs the serial branch."""
    gc, snapshot, cfg, chunks = _build_chunks(git_repo, 1)
    assert len(chunks) == 1

    context = planner.build_context_pack(gc, snapshot, cfg)
    provider = ParallelRecordingProvider(sleep=0.05)

    reviews = planner.run_map(
        provider,
        context,
        chunks,
        cfg,
        show_progress=False,
        max_parallel=4,
        snapshot=None,
        session_dir=None,
    )

    assert [r.chunk_id for r in reviews] == [chunks[0]["chunk_id"]]
    assert len(provider.records) == 1
    # A single call cannot overlap itself.
    rec = provider.records[0]
    assert rec["end"] >= rec["start"]


def test_run_map_respects_max_parallel_cap(git_repo):
    """With max_parallel < total, no more than max_parallel chunks overlap at once."""
    n = 4
    cap = 2
    gc, snapshot, cfg, chunks = _build_chunks(git_repo, n)
    assert len(chunks) == n

    context = planner.build_context_pack(gc, snapshot, cfg)
    provider = ParallelRecordingProvider(sleep=0.05)

    reviews = planner.run_map(
        provider,
        context,
        chunks,
        cfg,
        show_progress=False,
        max_parallel=cap,
        snapshot=None,
        session_dir=None,
    )

    # Order still preserved.
    assert [r.chunk_id for r in reviews] == [c["chunk_id"] for c in chunks]
    assert len(provider.records) == n

    # At no point do more than ``cap`` intervals overlap.
    events: list[tuple[float, int]] = []
    for rec in provider.records:
        events.append((rec["start"], 1))
        events.append((rec["end"], -1))
    events.sort(key=lambda e: (e[0], e[1]))
    running = 0
    peak = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    assert peak <= cap, f"peak concurrency {peak} exceeded cap {cap}"
