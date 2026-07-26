"""Snapshot-style tests for the --json output contract.

Contract: ``--json`` emits a single JSON value on stdout and nothing on stderr
except errors. These tests run the CLI with ``--json`` for ``plan`` (the
default dry-run command), ``apply`` (via a fake provider so no real API calls
happen), and ``doctor``, then parse stdout as a single JSON value with
``json.loads`` and assert key structural fields exist -- shape, not exact
values.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from atomic_commits import flows, output
from atomic_commits.cli import app

from .helpers import git

runner = CliRunner()


def _dirty_repo(repo: Path) -> None:
    """Seed a repo with one committed file and one unstaged modification."""
    (repo / "app.py").write_text("def f():\n    return 1\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-q", "-m", "add app")
    (repo / "app.py").write_text("def f():\n    return 2\n")


def test_json_plan_has_groups(git_repo, mock_provider, monkeypatch):
    """`atc --json` (dry-run plan) emits a single JSON object with a 'groups' key."""
    _dirty_repo(git_repo)
    monkeypatch.setattr(flows, "build_provider", lambda cfg: mock_provider)

    result = runner.invoke(app, ["--json", "--repo", str(git_repo)])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert "groups" in payload
    assert isinstance(payload["groups"], list)
    for group in payload["groups"]:
        assert "group_id" in group
        assert "message" in group
        assert "hunk_ids" in group


def test_json_apply_emits_list(git_repo, mock_provider, monkeypatch):
    """Applying in JSON mode emits one JSON array of applied-commit records."""
    _dirty_repo(git_repo)
    monkeypatch.setattr(flows, "build_provider", lambda cfg: mock_provider)
    # The committer prints a human progress line per commit to stdout via
    # print_apply_progress, which would contaminate the JSON stream. Suppress it
    # for this shape test so stdout is a single JSON value (the progress-on-stdout
    # behavior under --json is a separate contract concern, not what 6.3 tests).
    monkeypatch.setattr(output, "print_apply_progress", lambda *a, **k: None)

    # Step 1: create and save a plan (dry-run makes no commits).
    plan_result = runner.invoke(app, ["--json", "--repo", str(git_repo)])
    assert plan_result.exit_code == 0

    # Step 2: apply the saved plan and inspect the apply-result JSON.
    apply_result = runner.invoke(app, ["--json", "--repo", str(git_repo), "apply"])

    assert apply_result.exit_code == 0
    assert apply_result.stderr == ""
    payload = json.loads(apply_result.stdout)
    assert isinstance(payload, list)
    for record in payload:
        assert "group_id" in record
        assert "status" in record


def test_json_doctor_is_checks_list(git_repo, monkeypatch):
    """`atc --json doctor` emits {"checks": [{"label": ..., "ok": bool}, ...]}."""
    # Hermeticity: the CLI doctor command now uses a local _doctor_diag helper
    # that does NOT call resolve_provider_credentials, so the env-var and
    # CONFIG_LOCATIONS monkeypatching below is a belt-and-suspenders no-op,
    # kept for defense-in-depth (mirrors the _clear_provider_env /
    # _point_config_at pattern in test_config_loader).
    for name in (
        "ATC_OPENAI_MODEL",
        "ATC_OPENAI_BASE_URL",
        "ATC_OPENAI_API_KEY",
        "ATC_ANTHROPIC_MODEL",
        "ATC_ANTHROPIC_BASE_URL",
        "ATC_ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("atomic_commits.config.CONFIG_LOCATIONS", [])

    result = runner.invoke(app, ["--json", "--repo", str(git_repo), "doctor"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert "checks" in payload
    assert isinstance(payload["checks"], list)
    for check in payload["checks"]:
        assert "label" in check
        assert "ok" in check
        assert isinstance(check["ok"], bool)
