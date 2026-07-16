"""Shared text redaction for benchmark queries and reusable profile evidence."""
from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd

_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
_MENTION_RE = re.compile(r"(?<![\w@])@[a-zA-Z0-9][\w.-]*")
_JIRA_WIKI_MENTION_RE = re.compile(r"(?i)\[(?:~|user:)[^\]\r\n]+\]")
_IDENTIFIER_COLUMNS = (
    "person_id",
    "person_name",
    "evidence_person_id",
    "evidence_person_name",
)


def roster_identifiers(people: pd.DataFrame) -> list[str]:
    """Return project-qualified IDs and explicit pseudonyms found in a frame.

    Numeric ID suffixes are not stripped on their own because doing so would
    corrupt ordinary issue numbers and versions.
    """
    identifiers: set[str] = set()
    for column in _IDENTIFIER_COLUMNS:
        if column not in people:
            continue
        for value in people[column].dropna():
            candidate = str(value).strip()
            if len(candidate) >= 3:
                identifiers.add(candidate)
    return sorted(identifiers, key=lambda value: (-len(value), value.casefold()))


def _identifier_pattern(identifiers: Iterable[str]) -> re.Pattern[str] | None:
    identifiers = tuple(identifiers)
    if not identifiers:
        return None
    alternatives = "|".join(re.escape(value) for value in identifiers)
    # ``\b`` does not protect Jira/user identifiers ending in punctuation. These
    # look-arounds prevent matching inside a longer alphanumeric identifier.
    return re.compile(rf"(?i)(?<!\w)(?:{alternatives})(?!\w)")


class LeakageSanitizer:
    """Compile identity redaction once, then apply it to many text fields."""

    def __init__(self, identifiers: Iterable[str] = ()) -> None:
        self._identifier_pattern = _identifier_pattern(identifiers)

    def strip(self, text: str) -> str:
        """Remove roster IDs plus e-mail, modern, and Jira-wiki mentions."""
        # Normalize first: a Jira wiki mention can span a source newline, and a
        # later whitespace collapse would otherwise expose it after sanitization.
        cleaned = re.sub(r"\s+", " ", text).strip()
        # Repeat to a fixed point because one replacement can close a previously
        # unterminated Jira token. For example, replacing a later ``@mention``
        # with ``[MENTION]`` can make an earlier ``[~ ...`` newly matchable.
        while True:
            previous = cleaned
            cleaned = _EMAIL_RE.sub("[EMAIL]", cleaned)
            cleaned = _JIRA_WIKI_MENTION_RE.sub("[MENTION]", cleaned)
            cleaned = _MENTION_RE.sub("[MENTION]", cleaned)
            if self._identifier_pattern is not None:
                cleaned = self._identifier_pattern.sub("[PERSON]", cleaned)
            if cleaned == previous:
                return cleaned

    def contains(self, text: str) -> bool:
        """Return whether sanitized text still contains a protected pattern."""
        return bool(
            _EMAIL_RE.search(text)
            or _JIRA_WIKI_MENTION_RE.search(text)
            or _MENTION_RE.search(text)
            or (
                self._identifier_pattern
                and self._identifier_pattern.search(text)
            )
        )


def strip_leakage(text: str, identifiers: list[str]) -> str:
    """Compatibility wrapper for one-off sanitization."""
    return LeakageSanitizer(identifiers).strip(text)


def contains_leakage(text: str, identifiers: list[str]) -> bool:
    """Compatibility wrapper for one-off postcondition checks."""
    return LeakageSanitizer(identifiers).contains(text)
