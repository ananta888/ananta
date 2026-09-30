"""Hub-owned port for the repository CodeCompass graph export of rag-helper indices.

``RagHelperIndexService`` builds ``repo_path`` knowledge indices and, when a
profile enables ``graph_export_mode``, needs graph/semantic outputs for the
source records. Producing them is Worker execution (the CodeCompass bridge
lives in ``worker.retrieval``); the Hub must not import that implementation.
The service therefore depends on this port, and the Worker composition
(``build_knowledge_index_task_handler``) injects its implementation (DIP).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

REPOSITORY_GRAPH_BUILDER_UNAVAILABLE = "repository_graph_builder_unavailable"


class RepositoryGraphExecutionDeadline(Protocol):
    def checkpoint(self) -> None: ...


class RepositoryGraphOutputBuilderPort(Protocol):
    """Writes graph/semantic outputs for repository records and returns their manifest."""

    def build_outputs(
        self,
        *,
        source_id: str,
        records: Sequence[Mapping[str, Any]],
        output_dir: Path,
        execution_deadline: RepositoryGraphExecutionDeadline | None = None,
    ) -> Mapping[str, Any]: ...


class RepositoryGraphBuilderUnavailableError(RuntimeError):
    """Raised when a graph export is requested but no builder was composed."""

    def __init__(self) -> None:
        super().__init__(REPOSITORY_GRAPH_BUILDER_UNAVAILABLE)


__all__ = [
    "REPOSITORY_GRAPH_BUILDER_UNAVAILABLE",
    "RepositoryGraphBuilderUnavailableError",
    "RepositoryGraphExecutionDeadline",
    "RepositoryGraphOutputBuilderPort",
]
