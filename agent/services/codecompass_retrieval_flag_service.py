from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any


@dataclass(frozen=True)
class CodeCompassFlagState:
    enabled: bool
    status: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "status": str(self.status),
            "reason": str(self.reason),
        }


def _sqlite_fts5_available() -> bool:
    import sqlite3

    with sqlite3.connect(":memory:") as conn:
        try:
            conn.execute("CREATE VIRTUAL TABLE cc_fts USING fts5(content);")
            return True
        except sqlite3.DatabaseError:
            return False


def _vector_dependency_ready() -> bool:
    return find_spec("numpy") is not None


def _graph_dependency_ready() -> bool:
    return True


def _relation_dependency_ready(*, graph_enabled: bool) -> bool:
    return bool(graph_enabled)


def evaluate_codecompass_retrieval_flags(*, settings) -> dict[str, dict[str, Any]]:
    fts_enabled = bool(getattr(settings, "codecompass_fts_enabled", False))
    vector_enabled = bool(getattr(settings, "codecompass_vector_enabled", False))
    graph_enabled = bool(getattr(settings, "codecompass_graph_enabled", False))
    relation_enabled = bool(getattr(settings, "codecompass_relation_expansion_enabled", False))

    fts_dependency_ok = _sqlite_fts5_available()
    fts_state = CodeCompassFlagState(
        enabled=fts_enabled,
        status="disabled" if not fts_enabled else ("ready" if fts_dependency_ok else "missing_dependency"),
        reason="flag_disabled" if not fts_enabled else ("sqlite_fts5_available" if fts_dependency_ok else "sqlite_fts5_unavailable"),
    )
    vector_dependency_ok = _vector_dependency_ready()
    vector_state = CodeCompassFlagState(
        enabled=vector_enabled,
        status="disabled" if not vector_enabled else ("ready" if vector_dependency_ok else "missing_dependency"),
        reason="flag_disabled" if not vector_enabled else ("embedding_dependency_ready" if vector_dependency_ok else "embedding_dependency_missing"),
    )
    graph_dependency_ok = _graph_dependency_ready()
    graph_state = CodeCompassFlagState(
        enabled=graph_enabled,
        status="disabled" if not graph_enabled else ("ready" if graph_dependency_ok else "missing_dependency"),
        reason="flag_disabled" if not graph_enabled else ("graph_dependency_ready" if graph_dependency_ok else "graph_dependency_missing"),
    )
    relation_dependency_ok = _relation_dependency_ready(graph_enabled=graph_enabled)
    relation_state = CodeCompassFlagState(
        enabled=relation_enabled,
        status="disabled"
        if not relation_enabled
        else ("ready" if relation_dependency_ok else "degraded"),
        reason="flag_disabled"
        if not relation_enabled
        else ("relation_expansion_ready" if relation_dependency_ok else "requires_graph_channel"),
    )
    return {
        "codecompass_fts": fts_state.as_dict(),
        "codecompass_vector": vector_state.as_dict(),
        "codecompass_graph": graph_state.as_dict(),
        "codecompass_relation_expansion": relation_state.as_dict(),
    }
