from __future__ import annotations

from worker.retrieval.qdrant_client_port import FilterCondition, ServerFilter
from worker.retrieval.qdrant_collection_schema import (
    RECORD_TYPE_KEY,
    RECORD_TYPE_RECORD,
    QdrantSchemaError,
    canonical_scope,
)
from worker.retrieval.vector_store_contract import VectorScope, VectorStoreFilters


class QdrantFilterBuilder:
    def build(
        self,
        *,
        scope: VectorScope | None,
        filters: VectorStoreFilters | None = None,
    ) -> ServerFilter:
        if scope is None:
            raise QdrantSchemaError("vector_scope_required")
        canonical_scope(scope)
        requested = filters or VectorStoreFilters()
        if requested.profile_name and str(requested.profile_name) != str(scope.profile_name):
            raise QdrantSchemaError("vector_scope_conflict")
        must = [
            FilterCondition(RECORD_TYPE_KEY, (RECORD_TYPE_RECORD,)),
            FilterCondition("workspace_id", (str(scope.workspace_id),)),
            FilterCondition("repository_id", (str(scope.repository_id),)),
            FilterCondition("profile_name", (str(scope.profile_name),)),
            FilterCondition("domain", (str(scope.domain),)),
        ]
        if requested.source_scope:
            must.append(FilterCondition("source_scope", (str(requested.source_scope),)))
        if requested.kinds:
            must.append(FilterCondition("kind", tuple(str(value) for value in requested.kinds), "any"))
        if requested.file_prefix:
            prefix = str(requested.file_prefix).replace("\\", "/").strip("/")
            if not prefix:
                raise QdrantSchemaError("vector_filter_invalid")
            must.append(FilterCondition("file_prefixes", (prefix,)))
        for role_label in dict.fromkeys(
            str(value) for value in requested.role_labels
        ):
            must.append(
                FilterCondition(
                    "role_labels",
                    (role_label,),
                )
            )
        return ServerFilter(must=tuple(must))

    def scope_only(self, scope: VectorScope | None) -> ServerFilter:
        return self.build(scope=scope)
