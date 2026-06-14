from atomic_commits.models import CommitGroup, CommitPlan, ExcludedChange
from atomic_commits.validators.plan_schema import validate_plan


def _plan(groups, excluded=None):
    return CommitPlan(
        repo_fingerprint="fp",
        base_head="head",
        mode="compact",
        groups=groups,
        excluded=excluded or [],
    )


HUNK_TO_PATH = {"a.py::hunk::1": "a.py", "b.py::hunk::1": "b.py"}
SAFE = ["a.py::hunk::1", "b.py::hunk::1"]


def test_valid_plan_passes():
    plan = _plan(
        [
            CommitGroup(group_id="g1", message="backend(a): add foo handler", hunk_ids=["a.py::hunk::1"], file_paths=["a.py"]),
            CommitGroup(group_id="g2", message="backend(b): add bar guard", hunk_ids=["b.py::hunk::1"], file_paths=["b.py"]),
        ]
    )
    errors = validate_plan(plan, safe_hunk_ids=SAFE, hunk_to_path=HUNK_TO_PATH, mode="compact")
    assert errors == []


def test_missing_hunk_flagged():
    plan = _plan(
        [CommitGroup(group_id="g1", message="backend(a): add foo handler", hunk_ids=["a.py::hunk::1"], file_paths=["a.py"])]
    )
    errors = validate_plan(plan, safe_hunk_ids=SAFE, hunk_to_path=HUNK_TO_PATH, mode="compact")
    assert any("not assigned" in e for e in errors)


def test_duplicate_hunk_flagged():
    plan = _plan(
        [
            CommitGroup(group_id="g1", message="backend(a): add foo handler", hunk_ids=["a.py::hunk::1"], file_paths=["a.py"]),
            CommitGroup(group_id="g2", message="backend(a): add bar guard", hunk_ids=["a.py::hunk::1", "b.py::hunk::1"], file_paths=["a.py", "b.py"]),
        ]
    )
    errors = validate_plan(plan, safe_hunk_ids=SAFE, hunk_to_path=HUNK_TO_PATH, mode="compact")
    assert any("more than one group" in e for e in errors)


def test_unknown_hunk_flagged():
    plan = _plan(
        [CommitGroup(group_id="g1", message="backend(a): add foo handler", hunk_ids=["ghost::hunk::1"], file_paths=["a.py"])]
    )
    errors = validate_plan(plan, safe_hunk_ids=[], hunk_to_path=HUNK_TO_PATH, mode="compact")
    assert any("unknown hunk" in e for e in errors)


def test_excluded_requires_reason():
    plan = _plan(
        [
            CommitGroup(group_id="g1", message="backend(a): add foo handler", hunk_ids=["a.py::hunk::1"], file_paths=["a.py"]),
            CommitGroup(group_id="g2", message="backend(b): add bar guard", hunk_ids=["b.py::hunk::1"], file_paths=["b.py"]),
        ],
        excluded=[ExcludedChange(path=".env", reason="")],
    )
    errors = validate_plan(plan, safe_hunk_ids=SAFE, hunk_to_path=HUNK_TO_PATH, mode="compact")
    assert any("no reason" in e for e in errors)


def test_mismatched_file_paths_flagged():
    # Declared file_paths claim a path the group's hunks don't touch.
    plan = _plan(
        [
            CommitGroup(group_id="g1", message="backend(a): add foo handler", hunk_ids=["a.py::hunk::1"], file_paths=["a.py", "c.py"]),
            CommitGroup(group_id="g2", message="backend(b): add bar guard", hunk_ids=["b.py::hunk::1"], file_paths=["b.py"]),
        ]
    )
    errors = validate_plan(plan, safe_hunk_ids=SAFE, hunk_to_path=HUNK_TO_PATH, mode="compact")
    assert any("do not match" in e for e in errors)


def test_empty_file_paths_allowed_when_hunks_present():
    plan = _plan(
        [
            CommitGroup(group_id="g1", message="backend(a): add foo handler", hunk_ids=["a.py::hunk::1"]),
            CommitGroup(group_id="g2", message="backend(b): add bar guard", hunk_ids=["b.py::hunk::1"]),
        ]
    )
    errors = validate_plan(plan, safe_hunk_ids=SAFE, hunk_to_path=HUNK_TO_PATH, mode="compact")
    assert errors == []
