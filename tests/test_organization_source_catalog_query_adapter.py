from __future__ import annotations

from typing import Any

import pytest

from agent.services.organization_source_catalog_query_adapter import (
    OrganizationSourceCatalogQueryError,
    OrganizationSourceCatalogQueryPrincipal,
    ProductionOrganizationSourceCatalogQueryAdapter,
)


class _Runtime:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.result


def _principal() -> OrganizationSourceCatalogQueryPrincipal:
    return OrganizationSourceCatalogQueryPrincipal(
        subject_id="operator-1",
        tenant_id="tenant-1",
        project_id="project-1",
        roles=frozenset({"member"}),
        project_role="owner",
    )


def test_query_adapter_uses_authorized_active_index_runtime() -> None:
    runtime = _Runtime(
        {
            "matches": [{"content": "evidence", "metadata": {}}],
            "artifact_status": {
                "state": "available",
                "knowledge_index_id": "index-1",
            },
        }
    )
    adapter = ProductionOrganizationSourceCatalogQueryAdapter(runtime)

    batch = adapter.query(
        principal=_principal(),
        connection_id="connection-1",
        query="bounded intent",
        limit=7,
    )

    assert batch.knowledge_index_id == "index-1"
    assert batch.matches == ({"content": "evidence", "metadata": {}},)
    call = runtime.calls[0]
    assert call["connection_id"] == "connection-1"
    assert call["payload"] == {"query": "bounded intent", "limit": 7}
    assert call["principal"].subject_id == "operator-1"
    assert call["principal"].tenant_id == "tenant-1"
    assert call["principal"].project_id == "project-1"
    assert call["principal"].roles == frozenset({"member", "project_owner"})


@pytest.mark.parametrize(
    "result",
    [
        {"matches": [], "artifact_status": {"state": "unavailable"}},
        {
            "matches": [{"content": "one"}, {"content": "two"}],
            "artifact_status": {
                "state": "available",
                "knowledge_index_id": "index-1",
            },
        },
    ],
)
def test_query_adapter_rejects_unavailable_or_unbounded_results(result: Any) -> None:
    adapter = ProductionOrganizationSourceCatalogQueryAdapter(_Runtime(result))

    with pytest.raises(
        OrganizationSourceCatalogQueryError,
        match="organization_source_catalog_query_result_invalid",
    ):
        adapter.query(
            principal=_principal(),
            connection_id="connection-1",
            query="bounded intent",
            limit=1,
        )
