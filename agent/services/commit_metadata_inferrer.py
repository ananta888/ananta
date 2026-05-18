from __future__ import annotations

import re
from typing import Any, Optional

from agent.models import CommitMetadata
from agent.services.commit_scope_resolver import get_commit_scope_resolver

_FIX_WORDS = re.compile(r"\b(fix|bug|repair|correct|resolve|patch)\b", re.IGNORECASE)
_FEAT_WORDS = re.compile(r"\b(add|implement|create|introduce|new|extend|feat)\b", re.IGNORECASE)
_REFACTOR_WORDS = re.compile(r"\b(refactor|restructure|reorganize|clean|move|rename)\b", re.IGNORECASE)
_SECURITY_WORDS = re.compile(r"\b(security|auth|redact|secret|token|credential|permission)\b", re.IGNORECASE)
_TEST_WORDS = re.compile(r"\b(test|spec|coverage)\b", re.IGNORECASE)
_DOCS_WORDS = re.compile(r"\b(doc|readme|comment|document|explain)\b", re.IGNORECASE)
_PERF_WORDS = re.compile(r"\b(perf|performance|speed|optimize|latency)\b", re.IGNORECASE)


def _infer_type(task_kind: Optional[str], text: str) -> str:
    kind = str(task_kind or "").strip().lower()
    if kind == "test":
        return "test"
    if kind == "doc":
        return "docs"
    if _SECURITY_WORDS.search(text):
        return "security"
    if _FIX_WORDS.search(text):
        return "fix"
    if _REFACTOR_WORDS.search(text):
        return "refactor"
    if _TEST_WORDS.search(text):
        return "test"
    if _DOCS_WORDS.search(text):
        return "docs"
    if _PERF_WORDS.search(text):
        return "perf"
    if _FEAT_WORDS.search(text):
        return "feat"
    if kind == "coding":
        return "feat"
    if kind in ("ops", "ci"):
        return "chore"
    return "chore"


def _extract_file_refs(text: str) -> list[str]:
    return re.findall(r"[\w./]+\.py|[\w./]+\.sh|[\w./]+\.md|[\w./]+\.Modelfile", text)


def _subject_hint(description: str, max_len: int = 60) -> str:
    text = str(description or "").strip()
    first_line = text.split("\n")[0].strip()
    if len(first_line) <= max_len:
        return first_line
    return first_line[:max_len].rsplit(" ", 1)[0].strip() or first_line[:max_len]


class CommitMetadataInferrer:
    def infer(
        self,
        *,
        description: str,
        task_kind: Optional[str] = None,
        title: Optional[str] = None,
    ) -> CommitMetadata:
        text = " ".join(filter(None, [str(title or ""), str(description or "")]))
        commit_type = _infer_type(task_kind, text)

        file_refs = _extract_file_refs(text)
        commit_scope: Optional[str] = None
        if file_refs:
            resolution = get_commit_scope_resolver().resolve(file_refs)
            if resolution.primary_scope:
                commit_scope = resolution.primary_scope

        subject = _subject_hint(str(title or description or ""))

        return CommitMetadata(
            commit_type=commit_type,
            commit_scope=commit_scope,
            commit_subject_hint=subject or None,
        )


_INFERRER = CommitMetadataInferrer()


def get_commit_metadata_inferrer() -> CommitMetadataInferrer:
    return _INFERRER
