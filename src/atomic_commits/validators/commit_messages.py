"""Commit-message validation (implementation.md section 16).

Rule-based first. The planner is responsible for AI retry when a message is
rejected; this module only judges a single message and returns reasons.
"""

from __future__ import annotations

import re

MAX_LEN = 700

# Generic terms that are forbidden as the leading verb when no specific object
# follows (section 16: reject "update/change/misc/..." without a specific
# object). Used precisely so legitimate messages like
# "backend(auth): change token expiry window" are not falsely rejected, while
# "chore: cleanup" and "backend: update files" are.
GENERIC_TERMS = {
    "update", "change", "changes", "misc", "cleanup", "wip",
    "remaining", "stuff", "various", "things", "tweak", "tweaks", "updates",
}
# Objects that are too vague to count as "specific" after a generic verb.
VAGUE_OBJECTS = {
    "file", "files", "code", "stuff", "things", "core", "misc",
    "everything", "some", "various", "logic", "this", "that",
}

# "scope: verb object" where scope may carry a parenthetical, e.g. backend(auth).
_FORMAT_RE = re.compile(r"^[a-z0-9]+(?:\([a-z0-9_\-/]+\))?:\s+\S+(?:\s+\S+)+")
_FILENAME_ONLY_RE = re.compile(r"^[\w./-]+\.\w+$")
_VERB_RE = re.compile(r":\s+([a-zA-Z]+)")


def validate_commit_message(message: str) -> list[str]:
    """Return a list of rejection reasons. Empty list means the message is valid."""
    reasons: list[str] = []
    msg = message.strip()
    subject = msg.splitlines()[0] if msg else ""

    if not subject:
        return ["empty commit message"]
    if len(subject) > MAX_LEN:
        reasons.append(f"subject longer than {MAX_LEN} characters")
    if _FILENAME_ONLY_RE.match(subject.split(":")[-1].strip()):
        reasons.append("message only names a file")

    after_colon = subject.split(":", 1)[-1].strip() if ":" in subject else subject
    tokens = re.findall(r"[a-zA-Z]+", after_colon.lower())
    verb = tokens[0] if tokens else ""
    objects = set(tokens[1:])

    # A generic verb is only rejected when the object is vague or absent
    # (section 16: generic words "without specific object").
    if verb in GENERIC_TERMS:
        if not objects or objects.issubset(VAGUE_OBJECTS):
            reasons.append(
                f"generic verb '{verb}' without a specific object"
            )
    # Standalone generic terms anywhere with no real content are also rejected.
    if tokens and set(tokens).issubset(GENERIC_TERMS | VAGUE_OBJECTS):
        reasons.append("message is entirely generic")

    if not _VERB_RE.search(subject):
        reasons.append("missing a concrete verb after the scope")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for r in reasons:
        if r not in seen:
            unique.append(r)
            seen.add(r)
    return unique
