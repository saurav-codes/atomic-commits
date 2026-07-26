import json
from pathlib import Path
from typing import Any

from atomic_commits import planner, prompts
from atomic_commits.git_client import GitClient
from atomic_commits.models import ChunkReview, CommitPlan
from atomic_commits.planner import _apply_message_template
from atomic_commits.scanner import scan
from tests.integration.helpers import make_cfg


class InvalidPlanProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(
        self, *, system: str, user: str, schema_name: str, max_tokens: int, temperature: float,
        timeout: float | None = None, attempts: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(schema_name)
        payload = json.loads(user)
        if schema_name == "ChunkReview":
            return {
                "chunk_id": payload["chunk_id"],
                "summary": "mock review",
                "detected_concerns": [],
                "risky_hunks": [],
                "message_terms": {},
            }
        return {
            "version": "1",
            "mode": payload["mode"],
            "repo_fingerprint": payload["repo_fingerprint"],
            "base_head": payload["base_head"],
            "groups": [
                {
                    "group_id": "g1",
                    "message": "feat(core): apply one change",
                    "rationale": "mock grouping",
                    "hunk_ids": ["missing::hunk::1"],
                    "file_paths": [payload["hunk_inventory"][0]["file_path"]],
                    "risk": "low",
                }
            ],
            "excluded": [],
            "warnings": [],
        }


def test_planner_repairs_invalid_plan_once(git_repo):
    (git_repo / "a.py").write_text("x = 1\n")
    from tests.integration.helpers import git

    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("x = 2\n")

    cfg = make_cfg(Path(git_repo), mode="compact")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    provider = InvalidPlanProvider()

    plan = planner.plan(provider, gc, snapshot, cfg)

    planner._validate(plan, snapshot, cfg)
    assert provider.calls == ["CommitPlan", "CommitPlan"]


def test_apply_message_template_strips_leading_whitespace_subject():
    """Leading whitespace on the AI message must not corrupt the body.

    Regression: subject was derived from the stripped message but the body was
    sliced from the unstripped message with len(subject) as the offset, so the
    subject's tail leaked into the body when the message had leading whitespace.
    """
    msg = "  feat: add login\n\nbody line one\nbody line two\n"
    out = _apply_message_template(msg, "[${scope}] ${verb} ${object}")
    assert out == "[feat] add login\n\nbody line one\nbody line two"


def test_apply_message_template_no_scope_returns_unchanged():
    """A message without 'scope: verb object' shape is returned unchanged."""
    msg = "  just a subject\n\nbody\n"
    assert _apply_message_template(msg, "[${scope}]") == msg


def test_apply_message_template_no_template_returns_unchanged():
    """No template means the message passes through untouched."""
    msg = "feat: add login\n\nbody\n"
    assert _apply_message_template(msg, None) == msg


def test_normal_change_uses_global_plan_and_split_review(git_repo, mock_provider):
    from tests.integration.helpers import git

    (git_repo / "a.py").write_text("value = 1\n")
    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("value = 2\n")
    cfg = make_cfg(git_repo, mode="compact")

    planner.plan(mock_provider, GitClient(git_repo), scan(GitClient(git_repo), cfg), cfg)

    assert mock_provider.calls == ["CommitPlan", "CommitPlan"]


def test_large_change_uses_evidence_lead_and_review(git_repo, mock_provider):
    from tests.integration.helpers import git

    (git_repo / "a.py").write_text("value = 1\n")
    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("value = 2\n")
    cfg = make_cfg(git_repo, mode="compact", direct_max_tokens=1)
    gc = GitClient(git_repo)

    planner.plan(mock_provider, gc, scan(gc, cfg), cfg)

    assert "ChunkReview" in mock_provider.calls
    assert mock_provider.calls[-2:] == ["CommitPlan", "CommitPlan"]


def test_unchanged_retry_reuses_planner_cache(git_repo, mock_provider):
    from tests.integration.helpers import git

    (git_repo / "a.py").write_text("value = 1\n")
    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("value = 2\n")
    cfg = make_cfg(git_repo, mode="compact")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)

    planner.plan(mock_provider, gc, snapshot, cfg)
    calls = list(mock_provider.calls)
    planner.plan(mock_provider, gc, snapshot, cfg)

    assert mock_provider.calls == calls


def test_reduce_once_uses_remaining_run_budget(git_repo):
    """Lead planning uses the budget supplied by the run coordinator."""
    from atomic_commits import planner
    from atomic_commits.config import RunConfig

    class TimeoutRecordingProvider:
        def __init__(self) -> None:
            self.last_timeout: float | None = None

        def complete_json(
                self, *, system: str, user: str, schema_name: str, max_tokens: int,
            temperature: float,
            timeout: float | None = None, attempts: int | None = None,
        ) -> dict[str, Any]:
            self.last_timeout = timeout
            import json as _json
            payload = _json.loads(user)
            return {
                "version": "1",
                "mode": payload["mode"],
                "repo_fingerprint": payload["repo_fingerprint"],
                "base_head": payload["base_head"],
                "groups": [],
                "excluded": [],
                "warnings": [],
            }

    # A large reduce prompt: many reviews inflate the user message well past
    # the point where the 180s base would be the sole timeout.
    big_reviews = [{"chunk_id": f"chunk-{i}", "summary": "x" * 200} for i in range(200)]
    hunk_inventory = [{"hunk_id": f"h{i}", "file_path": "a.py"} for i in range(200)]
    cfg = RunConfig(repo=git_repo, mode="compact")
    cfg.provider_timeout = 180.0

    provider = TimeoutRecordingProvider()
    planner._reduce_once(
        provider,
        ctx={"repo_name": "r", "branch": "main", "recent_subjects": [], "mode": "compact",
             "safety_exclusions": [], "file_list": [], "diffstat": "", "instructions": ""},
        reviews=big_reviews,
        hunk_inventory=hunk_inventory,
        cfg=cfg,
        mode="compact",
        repo_fingerprint="fp",
        base_head="sha",
        show_progress=False,
    )

    assert provider.last_timeout is not None
    assert provider.last_timeout == 180.0


def test_reduce_once_respects_custom_provider_timeout(git_repo):
    """A user-set --provider-timeout above the 600s floor is honored."""
    from atomic_commits import planner
    from atomic_commits.config import RunConfig

    class TimeoutRecordingProvider:
        def __init__(self) -> None:
            self.last_timeout: float | None = None

        def complete_json(
                self, *, system: str, user: str, schema_name: str, max_tokens: int,
            temperature: float,
            timeout: float | None = None, attempts: int | None = None,
        ) -> dict[str, Any]:
            self.last_timeout = timeout
            import json as _json
            payload = _json.loads(user)
            return {
                "version": "1", "mode": payload["mode"],
                "repo_fingerprint": payload["repo_fingerprint"],
                "base_head": payload["base_head"],
                "groups": [], "excluded": [], "warnings": [],
            }

    cfg = RunConfig(repo=git_repo, mode="compact")
    cfg.provider_timeout = 1200.0  # user raised the floor above 600s
    provider = TimeoutRecordingProvider()
    planner._reduce_once(
        provider,
        ctx={"repo_name": "r", "branch": "main", "recent_subjects": [], "mode": "compact",
             "safety_exclusions": [], "file_list": [], "diffstat": "", "instructions": ""},
        reviews=[{"chunk_id": "chunk-0", "summary": "x" * 200}],
        hunk_inventory=[{"hunk_id": "h0", "file_path": "a.py"}],
        cfg=cfg, mode="compact", repo_fingerprint="fp", base_head="sha",
        show_progress=False,
    )
    # A user floor above 600s must be honored (plus gen_time for the output).
    assert provider.last_timeout is not None
    assert provider.last_timeout >= 1200.0


def test_reduce_prompt_does_not_repeat_hunk_inventory_inside_context():
    payload = json.loads(prompts.reduce_user(
        {"repo_name": "repo", "hunk_inventory": [{"hunk_id": "h1"}]},
        [], [{"hunk_id": "h1"}], "compact", "fp", "sha",
    ))

    assert "hunk_inventory" not in payload["context"]
    assert payload["hunk_inventory"] == [{"hunk_id": "h1"}]


def test_invalid_split_review_keeps_valid_lead_plan(git_repo, mock_provider):
    from tests.integration.helpers import git

    (git_repo / "a.py").write_text("value = 1\n")
    git(git_repo, "add", "a.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    (git_repo / "a.py").write_text("value = 2\n")

    class BrokenReviewProvider:
        commit_plan_calls = 0

        def complete_json(self, **kwargs):
            raw = mock_provider.complete_json(**kwargs)
            if kwargs["schema_name"] == "CommitPlan":
                self.commit_plan_calls += 1
                if self.commit_plan_calls == 2:
                    raw["groups"] = []
            return raw

    cfg = make_cfg(git_repo, mode="compact")
    gc = GitClient(git_repo)
    plan = planner.plan(BrokenReviewProvider(), gc, scan(gc, cfg), cfg, show_progress=False)

    assert len(plan.groups) == 1


def test_local_repair_assigns_every_missing_hunk(git_repo):
    from atomic_commits.change_analysis import build_change_graph
    from tests.integration.helpers import git

    for name in ("a.py", "b.py"):
        (git_repo / name).write_text("value = 1\n")
    git(git_repo, "add", "a.py", "b.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    for name in ("a.py", "b.py"):
        (git_repo / name).write_text("value = 2\n")

    cfg = make_cfg(git_repo, mode="compact")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    broken = CommitPlan(
        repo_fingerprint=snapshot.fingerprint,
        base_head=snapshot.head_sha,
        mode=cfg.mode,
        groups=[],
    )

    repaired = planner._repair_plan_locally(
        broken, snapshot, build_change_graph(snapshot), [],
    )
    planner._validate(repaired, snapshot, cfg)

    assert len(repaired.groups) == 2


def test_local_repair_adds_verbose_group_rationale(git_repo):
    from atomic_commits.change_analysis import build_change_graph
    from atomic_commits.models import CommitGroup
    from tests.integration.helpers import git

    for name in ("a.py", "b.py"):
        (git_repo / name).write_text("value = 1\n")
    git(git_repo, "add", "a.py", "b.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    for name in ("a.py", "b.py"):
        (git_repo / name).write_text("value = 2\n")

    cfg = make_cfg(git_repo, mode="verbose")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    hunk_ids = [hunk.hunk_id for file_change in snapshot.files for hunk in file_change.hunks]
    broken = CommitPlan(
        repo_fingerprint=snapshot.fingerprint,
        base_head=snapshot.head_sha,
        mode=cfg.mode,
        groups=[CommitGroup(
            group_id="g1",
            message="feat(core): implement shared behavior",
            hunk_ids=hunk_ids,
        )],
    )

    repaired = planner._repair_plan_locally(
        broken, snapshot, build_change_graph(snapshot), [],
    )
    planner._validate(repaired, snapshot, cfg)

    assert repaired.groups[0].rationale
    assert repaired.groups[0].unsplittable_reason


def test_large_reduce_uses_bounded_parallel_batches(git_repo, monkeypatch):
    from atomic_commits.change_analysis import build_change_graph
    from tests.integration.helpers import git

    for name in ("a.py", "b.py"):
        (git_repo / name).write_text("value = 1\n")
    git(git_repo, "add", "a.py", "b.py")
    git(git_repo, "commit", "-q", "-m", "seed")
    for name in ("a.py", "b.py"):
        (git_repo / name).write_text("value = 2\n")

    cfg = make_cfg(git_repo, mode="compact")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    context = planner.build_context_pack(gc, snapshot, cfg)
    graph = build_change_graph(snapshot)
    reviews = [
        ChunkReview(chunk_id=f"chunk-{index}", hunk_ids=[hunk.hunk_id], summary="intent")
        for index, file_change in enumerate(snapshot.files)
        for hunk in file_change.hunks
    ]

    class BatchProvider:
        calls = 0

        def complete_json(self, **kwargs):
            self.calls += 1
            payload = json.loads(kwargs["user"])
            return {
                "version": "1",
                "mode": payload["mode"],
                "repo_fingerprint": payload["repo_fingerprint"],
                "base_head": payload["base_head"],
                "groups": [
                    {
                        "group_id": f"g{index}",
                        "message": "feat(batch): implement region behavior",
                        "hunk_ids": [item["hunk_id"]],
                        "file_paths": [item["file_path"]],
                    }
                    for index, item in enumerate(payload["hunk_inventory"])
                ],
            }

    monkeypatch.setattr(planner, "_LEAD_INPUT_TOKENS", 1)
    provider = BatchProvider()
    plan = planner.run_reduce(
        provider, context, reviews, snapshot, graph, cfg, show_progress=False,
    )
    planner._validate(plan, snapshot, cfg)

    assert provider.calls == 2
    assert planner._BOUNDED_PLAN_WARNING in plan.warnings


def test_failed_large_batch_is_split_and_retried(git_repo, monkeypatch):
    from atomic_commits.change_analysis import build_change_graph
    from atomic_commits.errors import ProviderError
    from tests.integration.helpers import git

    for index in range(4):
        (git_repo / f"f{index}.py").write_text("value = 1\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-q", "-m", "seed")
    for index in range(4):
        (git_repo / f"f{index}.py").write_text("value = 2\n")

    cfg = make_cfg(git_repo, mode="compact")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    context = planner.build_context_pack(gc, snapshot, cfg)
    graph = build_change_graph(snapshot)
    reviews = [
        ChunkReview(chunk_id=f"chunk-{index}", hunk_ids=[hunk.hunk_id], summary="intent")
        for index, file_change in enumerate(snapshot.files)
        for hunk in file_change.hunks
    ]

    class SplitProvider:
        calls = 0

        def complete_json(self, **kwargs):
            self.calls += 1
            payload = json.loads(kwargs["user"])
            if len(payload["hunk_inventory"]) > 1:
                raise ProviderError("provider returned empty content (finish_reason: stop)")
            item = payload["hunk_inventory"][0]
            return {
                "version": "1",
                "mode": payload["mode"],
                "repo_fingerprint": payload["repo_fingerprint"],
                "base_head": payload["base_head"],
                "groups": [{
                    "group_id": "g1",
                    "message": "feat(batch): implement region behavior",
                    "hunk_ids": [item["hunk_id"]],
                    "file_paths": [item["file_path"]],
                }],
            }

    monkeypatch.setattr(
        planner,
        "_estimate_reduce_tokens",
        lambda _ctx, review_items, *_args: len(review_items),
    )
    monkeypatch.setattr(planner, "_LEAD_INPUT_TOKENS", 2)
    provider = SplitProvider()
    plan = planner.run_reduce(
        provider, context, reviews, snapshot, graph, cfg, show_progress=False,
    )
    planner._validate(plan, snapshot, cfg)

    assert provider.calls == 6
    assert len(plan.groups) == 4


def test_incomplete_large_batch_is_split_and_completed(git_repo, monkeypatch):
    from atomic_commits.change_analysis import build_change_graph
    from tests.integration.helpers import git

    for index in range(4):
        (git_repo / f"f{index}.py").write_text("value = 1\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-q", "-m", "seed")
    for index in range(4):
        (git_repo / f"f{index}.py").write_text("value = 2\n")

    cfg = make_cfg(git_repo, mode="compact")
    gc = GitClient(git_repo)
    snapshot = scan(gc, cfg)
    context = planner.build_context_pack(gc, snapshot, cfg)
    graph = build_change_graph(snapshot)
    reviews = [
        ChunkReview(chunk_id=f"chunk-{index}", hunk_ids=[hunk.hunk_id], summary="intent")
        for index, file_change in enumerate(snapshot.files)
        for hunk in file_change.hunks
    ]

    class IncompleteProvider:
        calls = 0

        def complete_json(self, **kwargs):
            self.calls += 1
            payload = json.loads(kwargs["user"])
            items = payload["hunk_inventory"]
            planned_items = items if len(items) == 1 else []
            return {
                "version": "1",
                "mode": payload["mode"],
                "repo_fingerprint": payload["repo_fingerprint"],
                "base_head": payload["base_head"],
                "groups": [
                    {
                        "group_id": f"g{index}",
                        "message": "feat(batch): implement region behavior",
                        "hunk_ids": [item["hunk_id"]],
                        "file_paths": [item["file_path"]],
                    }
                    for index, item in enumerate(planned_items)
                ],
            }

    monkeypatch.setattr(
        planner,
        "_estimate_reduce_tokens",
        lambda _ctx, review_items, *_args: len(review_items),
    )
    monkeypatch.setattr(planner, "_LEAD_INPUT_TOKENS", 2)
    provider = IncompleteProvider()
    plan = planner.run_reduce(
        provider, context, reviews, snapshot, graph, cfg, show_progress=False,
    )
    planner._validate(plan, snapshot, cfg)

    assert provider.calls == 6
    assert len(plan.groups) == 4
