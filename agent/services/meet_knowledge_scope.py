"""Which knowledge indices the Meet companion may search for a project.

Knowledge indices carry no project binding, so an unscoped companion lookup
searched every completed index on the Hub, including synthetic test
fixtures. ``ANANTA_MEET_COMPANION_KNOWLEDGE_SOURCES`` binds projects to index
sources as a JSON object::

    {"<project_id>": ["ananta-project", "source-control:conn_…"], "*": [...]}

A selector matches an index id, its ``source_id`` or its
``connection_source_id``. A bound project (or the ``"*"`` default) is strict:
only matching indices are searched, and an empty match searches nothing.
Without any binding the previous unscoped behaviour stays (``None``).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Iterable, Mapping

ENV_NAME = "ANANTA_MEET_COMPANION_KNOWLEDGE_SOURCES"
DEFAULT_KEY = "*"
_MAX_SELECTORS = 32


def load_bindings(environ: Mapping[str, str] | None = None) -> dict[str, frozenset[str]]:
    """Parsed bindings; a malformed value binds nothing and is logged once per call."""
    raw = (environ if environ is not None else os.environ).get(ENV_NAME, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logging.warning("%s is not valid JSON; companion knowledge scope stays unbound", ENV_NAME)
        return {}
    if not isinstance(parsed, dict):
        logging.warning("%s must be a JSON object; companion knowledge scope stays unbound", ENV_NAME)
        return {}
    bindings: dict[str, frozenset[str]] = {}
    for project, selectors in parsed.items():
        if isinstance(project, str) and isinstance(selectors, list):
            bindings[project] = frozenset(
                str(selector).strip() for selector in selectors[:_MAX_SELECTORS] if str(selector).strip()
            )
    return bindings


def index_selectors(knowledge_index: Any) -> set[str]:
    """The identities a binding may name for one index."""
    metadata = getattr(knowledge_index, "index_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    values = (
        getattr(knowledge_index, "id", ""),
        metadata.get("source_id"),
        metadata.get("connection_source_id"),
    )
    return {str(value).strip() for value in values if str(value or "").strip()}


def allowed_index_ids(
    project_id: str | None,
    completed_indices: Callable[[], Iterable[Any]],
    *,
    environ: Mapping[str, str] | None = None,
) -> set[str] | None:
    """Index ids the companion may search for ``project_id``; ``None`` means unscoped."""
    bindings = load_bindings(environ)
    selectors = bindings.get(str(project_id or "")) if project_id else None
    if selectors is None:
        selectors = bindings.get(DEFAULT_KEY)
    if selectors is None:
        return None
    return {
        str(knowledge_index.id)
        for knowledge_index in completed_indices()
        if index_selectors(knowledge_index) & selectors
    }
