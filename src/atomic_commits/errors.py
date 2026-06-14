"""Typed errors for atc.

Every error must be actionable. Where possible, include a `hint` describing
what the user should do next. See implementation.md section 21.
"""

from __future__ import annotations


class AtcError(Exception):
    """Base class for all atc errors."""

    exit_code: int = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.hint:
            return f"{self.message}\nHint: {self.hint}"
        return self.message


class PreflightError(AtcError):
    """Repo or environment is not in a state where atc can run."""

    exit_code = 2


class UnsafeFileError(AtcError):
    """A file was rejected by safety filtering."""

    exit_code = 3


class ProviderError(AtcError):
    """AI provider transport/auth/config failure."""

    exit_code = 4


class InvalidAIResponseError(AtcError):
    """AI returned content that could not be parsed/validated."""

    exit_code = 4


class PlanValidationError(AtcError):
    """A commit plan failed schema or semantic validation."""

    exit_code = 5


class PatchApplyError(AtcError):
    """A patch could not be staged cleanly."""

    exit_code = 6


class FingerprintMismatchError(AtcError):
    """Worktree fingerprint no longer matches the saved plan."""

    exit_code = 7


class CommitError(AtcError):
    """`git commit` failed."""

    exit_code = 8
