"""Hub-delegated materialization of a DuckDB snapshot from CodeCompass records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from uuid import uuid4

from worker.retrieval.duckdb_output_importer import DuckDBOutputImporter
from worker.retrieval.duckdb_snapshot_manager import DuckDBSnapshotManager
from worker.retrieval.duckdb_vector_store_config import DuckDBVectorStoreConfig
from worker.retrieval.vector_store_contract import VectorScope


class CodeCompassDuckDBMaterializer:
    def __init__(self, config: DuckDBVectorStoreConfig) -> None:
        self._config = config
        self._snapshots = DuckDBSnapshotManager(config)
        self._importer = DuckDBOutputImporter(config)

    def materialize(
        self,
        *,
        records: Iterable[Mapping[str, Any]],
        scope: VectorScope,
        manifest_hash: str,
        compatibility_fingerprint: str,
        source_revision: str,
    ) -> dict[str, Any]:
        version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        staging = self._snapshots.create_staging(scope, compatibility_fingerprint, version)
        connection = self._snapshots.connect(staging, read_only=False)
        counts = self._importer.import_records(
            connection,
            records=records,
            scope=scope,
            manifest_hash=manifest_hash,
        )
        pointer = self._snapshots.publish(
            staging_path=staging,
            scope=scope,
            manifest_hash=manifest_hash,
            compatibility_fingerprint=compatibility_fingerprint,
            source_revision=source_revision,
        )
        self._snapshots.close_connections()
        return {"pointer": pointer, "counts": counts}
