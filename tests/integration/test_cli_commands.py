"""Integration tests for the Typer CLI command surface.

Exercises the command paths that do not require a real API provider: ``--help``
for the app and each subcommand, ``doctor`` (no provider), and the default
``--json`` plan path with the provider monkeypatched to a mock (the same pattern
used by ``test_dry_run.py`` / ``test_json_output.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from atomic_commits import flows
from atomic_commits.cli import app

from .helpers import git

runner = CliRunner()


# -- help / discovery ------------------------------------------------------


def test_help_exits_zero_and_lists_commands():
    """`atc --help` exits 0 and lists the subcommands."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("doctor", "resume", "sessions", "show-plan", "init", "undo", "selftest", "explain"):
        assert command in result.stdout


def test_help_lists_new_flags():
    """`atc --help` advertises the newer planning/commit flags."""
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    import re

    clean = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    for flag in (
        "--max-parallel",
        "--retry-attempts",
        "--message-template",
        "--trailer",
        "--no-instructions",
        "--amend",
        "--squash",
    ):
        assert flag in clean


def test_init_help_exits_zero():
    """`atc init --help` exits 0 (init itself is interactive; only test --help)."""
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0


def test_selftest_help_exits_zero():
    """`atc selftest --help` exits 0."""
    result = runner.invoke(app, ["selftest", "--help"])
    assert result.exit_code == 0


def test_undo_help_exits_zero():
    """`atc undo --help` exits 0."""
    result = runner.invoke(app, ["undo", "--help"])
    assert result.exit_code == 0


def test_explain_help_exits_zero():
    """`atc explain --help` exits 0."""
    result = runner.invoke(app, ["explain", "--help"])
    assert result.exit_code == 0


def test_amend_flag_help_exits_zero():
    """`atc --amend --help` exits 0 (the --amend flag exists on the default command)."""
    result = runner.invoke(app, ["--amend", "--help"])
    assert result.exit_code == 0


# -- doctor (no provider needed) ------------------------------------------


def test_doctor_exits_zero(git_repo):
    """`atc doctor` exits 0 without a real provider."""
    result = runner.invoke(app, ["--repo", str(git_repo), "doctor"])
    assert result.exit_code == 0


def test_doctor_json_emits_valid_checks_object(git_repo):
    """`atc --json doctor` emits a {"checks": [...]} JSON object."""
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


# -- default plan path with a mocked provider ------------------------------


def _dirty_repo(repo: Path) -> None:
    """Seed a repo with one committed file and one unstaged modification."""
    (repo / "app.py").write_text("def f():\n    return 1\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-q", "-m", "add app")
    (repo / "app.py").write_text("def f():\n    return 2\n")


def test_json_default_plan_emits_groups(git_repo, mock_provider, monkeypatch):
    """`atc --json` (default dry-run plan) emits JSON with a 'groups' list."""
    _dirty_repo(git_repo)
    monkeypatch.setattr(flows, "build_provider", lambda cfg: mock_provider)

    result = runner.invoke(app, ["--json", "--repo", str(git_repo)])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert "groups" in payload
    assert isinstance(payload["groups"], list)
    assert len(payload["groups"]) >= 1
    for group in payload["groups"]:
        assert "group_id" in group
        assert "message" in group
        assert "hunk_ids" in group


def test_commit_command_plans_and_commits_in_one_run(git_repo, mock_provider, monkeypatch):
    _dirty_repo(git_repo)
    monkeypatch.setattr(flows, "build_provider", lambda cfg: mock_provider)

    result = runner.invoke(app, ["--repo", str(git_repo), "commit", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert git(git_repo, "status", "--porcelain").stdout == ""


def test_commit_command_includes_existing_staged_changes(git_repo, mock_provider, monkeypatch):
    _dirty_repo(git_repo)
    git(git_repo, "add", "app.py")
    monkeypatch.setattr(flows, "build_provider", lambda cfg: mock_provider)

    result = runner.invoke(app, ["--repo", str(git_repo), "commit", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert git(git_repo, "status", "--porcelain").stdout == ""


def test_squash_rewrites_last_commit(git_repo, mock_provider, monkeypatch):
    """`atc --squash --yes` rewrites the last commit as a single squash commit.

    Regression: ``git reset --mixed HEAD~1`` + re-commit within the same
    wall-clock second produces an identical SHA (same tree/parents/author),
    silently making the rewrite a no-op. The squash path bumps the committer
    date so the new commit always differs.
    """
    # Seed one commit on top of the fixture's initial commit, then squash it.
    (git_repo / "README.md").write_text("# Project\n\n## Section\n")
    git(git_repo, "add", "README.md")
    git(git_repo, "commit", "-q", "-m", "update readme")
    original_head = git(git_repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(flows, "build_provider", lambda cfg: mock_provider)

    result = runner.invoke(app, ["--squash", "--yes", "--repo", str(git_repo)])

    assert result.exit_code == 0
    # The last commit was rewritten: same subject, different sha.
    new_head = git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert new_head != original_head
    subject = git(git_repo, "log", "-1", "--pretty=%s").stdout.strip()
    assert subject == "update readme"
    # The worktree is clean after the squash.
    assert git(git_repo, "status", "--porcelain").stdout == ""


def test_amend_rewrites_last_commit(git_repo, mock_provider, monkeypatch):
    """`atc --amend --yes` rewrites the last commit's diff as atomic commits.

    Shares the same reset+recommit SHA-collision concern as ``--squash``;
    verifies the amend path also produces a distinct HEAD.
    """
    (git_repo / "README.md").write_text("# Project\n\n## Section\n")
    git(git_repo, "add", "README.md")
    git(git_repo, "commit", "-q", "-m", "update readme")
    original_head = git(git_repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(flows, "build_provider", lambda cfg: mock_provider)

    result = runner.invoke(app, ["--amend", "--yes", "--repo", str(git_repo)])

    assert result.exit_code == 0
    # HEAD advances: the amend produced a new commit, not a same-second no-op.
    new_head = git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert new_head != original_head
    # The worktree is clean after the amend.
    assert git(git_repo, "status", "--porcelain").stdout == ""


def test_init_runs_doctor_with_written_config(git_repo, tmp_path, monkeypatch):
    """`atc init` runs doctor against the just-written config, not stale flags.

    Regression: init printed 'model: NOT SET' / 'base_url: NOT SET' (red) right
    after 'config written' (green) because it ran doctor against the CLI RunConfig
    (model/base_url None from flags) instead of the prompted+written values.
    """
    # Redirect the config file into the tmp_path so we don't touch the user's
    # real ~/.config/atc/config.toml.
    cfg_path = tmp_path / "atc-config.toml"
    import atomic_commits.config as config_mod

    monkeypatch.setattr(config_mod, "init_config", lambda **kw: cfg_path)
    # Drive the prompts: provider, model (accept default), api-key-env (default),
    # base_url (default).
    result = runner.invoke(
        app,
        ["--repo", str(git_repo), "init"],
        input="\n".join(["openai-compatible", "", "", ""]) + "\n",
    )

    assert result.exit_code == 0, result.stdout
    # The doctor output must NOT show NOT SET for model (the bug printed it red).
    assert "model: NOT SET" not in result.stdout
    assert "config written" in result.stdout
