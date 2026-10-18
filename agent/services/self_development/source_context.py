"""Bounded repository observations for Hub-owned development assignments."""
from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, TypedDict, assert_never

from agent.services.self_development.catalog import JSONValue
from agent.services.self_development.contracts import source_path
from agent.services.self_development.workspace import git, read_source

MAX_FILES: Final = 8
MAX_FILE_CHARS: Final = 4000
MAX_TOTAL_CHARS: Final = 20000
MAX_CANDIDATES: Final = 64
MAX_SEARCH_BYTES: Final = 2_000_000
_TOKENS: Final = re.compile(r'[a-zA-Z][a-zA-Z0-9_]{2,63}')
_STOP_WORDS: Final = frozenset({'the', 'and', 'for', 'with', 'from', 'that', 'this',
                              'und', 'der', 'die', 'das', 'mit', 'eine', 'einen'})


class SourceObservation(TypedDict):
    path: str
    revision: str
    sha256: str
    content: str
    start_line: int
    truncated: bool


class UnavailableSource(TypedDict):
    path: str
    reason: str


class ResearchContext(TypedDict):
    revision: str
    files: list[SourceObservation]
    unavailable: list[UnavailableSource]


class SourceContextError(ValueError):
    """Repository context cannot be bound to the requested revision."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


def _reference_paths(value: JSONValue) -> list[str]:
    """Only literal repository path references are candidates, never commands."""
    match value:
        case str():
            return [value]
        case list():
            return [path for entry in value for path in _reference_paths(entry)]
        case dict():
            path = value.get('path')
            return [path] if isinstance(path, str) else []
        case None | bool() | int() | float():
            return []
        case unreachable:
            assert_never(unreachable)


def _search_terms(item: Mapping[str, JSONValue], references: Sequence[str]) -> set[str]:
    texts = [str(item.get(key, '')) for key in
             ('todo_id', 'title', 'description', 'acceptance_criteria', 'goal',
              'document_goal', 'document_purpose')]
    texts.extend(Path(path).stem for path in references)
    return {token.lower() for text in texts for token in _TOKENS.findall(text)
            if token.lower() not in _STOP_WORDS}


def _revision_bytes(root: Path, revision: str, path: str) -> bytes:
    """Git content is compared byte-for-byte; decoded snippets are observations."""
    result = subprocess.run(
        ['git', '-c', 'safe.directory=' + str(root), 'show', revision + ':' + path],
        cwd=root, capture_output=True, timeout=10, check=False,
    )
    if result.returncode:
        raise SourceContextError('source_not_in_revision')
    return result.stdout


def build_source_context(root: Path, *, item: Mapping[str, JSONValue],
                         repository: Sequence[str], revision: str) -> ResearchContext:
    """Select real sources without granting write scope or issuing evidence IDs.

    Explicit references lead; remaining paths must match task or reference terms.
    Selection ranks the whole tracked path catalog before bounded content reads.
    """
    if git(root, 'rev-parse', 'HEAD') != revision:
        raise SourceContextError('source_context_revision_mismatch')
    tracked = set(repository)
    references = list(dict.fromkeys(_reference_paths(item.get('references', []))))
    terms = _search_terms(item, references)
    ranked = sorted(tracked, key=lambda path: (
        -sum(term in path.lower() for term in terms), path))
    candidates = references + [path for path in ranked if path not in references
                              and any(term in path.lower() for term in terms)]
    context: ResearchContext = {'revision': revision, 'files': [], 'unavailable': []}
    remaining = MAX_TOTAL_CHARS
    searched = 0
    for path in candidates[:MAX_CANDIDATES]:
        if len(context['files']) >= MAX_FILES or remaining <= 0:
            break
        try:
            source_path(path)
            if path not in tracked:
                raise SourceContextError('source_not_tracked')
            raw = read_source(root, path)
            searched += len(raw)
            if searched > MAX_SEARCH_BYTES:
                raise SourceContextError('source_search_budget_exhausted')
            if b'\x00' in raw:
                raise SourceContextError('source_binary')
            text = raw.decode('utf-8', errors='strict')
            if raw != _revision_bytes(root, revision, path):
                raise SourceContextError('source_revision_content_mismatch')
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            context['unavailable'].append({'path': path, 'reason': reason[:160]})
            if searched > MAX_SEARCH_BYTES:
                break
            continue
        limit = min(MAX_FILE_CHARS, remaining)
        positions = [text.lower().find(term) for term in terms if term in text.lower()]
        start = max(0, min(positions, default=0) - limit // 4)
        start = text.rfind('\n', 0, start) + 1
        content = text[start:start + limit]
        context['files'].append({
            'path': path, 'revision': revision,
            'sha256': hashlib.sha256(raw).hexdigest(), 'content': content,
            'start_line': text.count('\n', 0, start) + 1,
            'truncated': start > 0 or start + len(content) < len(text),
        })
        remaining -= len(content)
    if git(root, 'rev-parse', 'HEAD') != revision:
        raise SourceContextError('source_context_revision_mismatch')
    return context
