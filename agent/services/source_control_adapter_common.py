"""Shared error type and value projection of the Source Control production adapters.

``public_projection`` turns service results into JSON-safe public values; the
single-index/empty-link doubles let one active index be queried through the
regular retrieval service without widening its repository scope.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.db_models import KnowledgeIndexDB


class SourceControlProductionAdapterError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


def public_projection(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): public_projection(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [public_projection(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return public_projection(to_dict())
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return public_projection(model_dump(mode="json"))
    values = getattr(value, "__dict__", None)
    if isinstance(values, Mapping):
        return {
            str(key): public_projection(item)
            for key, item in values.items()
            if not str(key).startswith("_")
        }
    raise SourceControlProductionAdapterError(
        "source_operation_result_invalid", status_code=502
    )


class SingleIndexRepository:
    def __init__(self, index: KnowledgeIndexDB) -> None:
        self._index = index

    def list_completed(self):
        return [self._index] if self._index.status == "completed" else []


class EmptyKnowledgeLinks:
    def get_by_artifact(self, artifact_id: str):
        del artifact_id
        return []


__all__ = [
    "EmptyKnowledgeLinks",
    "SingleIndexRepository",
    "SourceControlProductionAdapterError",
    "public_projection",
]
