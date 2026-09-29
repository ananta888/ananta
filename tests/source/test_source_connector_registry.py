from __future__ import annotations

import pytest

from agent.sources.source_connectors import (
    ConnectorRefreshRequest,
    ConnectorRegistry,
    DescriptorConnectorAdapter,
    SourceConnectorError,
)


def test_registry_selects_by_canonical_connector_type() -> None:
    connector = DescriptorConnectorAdapter("text").connector()
    registry = ConnectorRegistry([connector])

    assert registry.get(" text ") is connector
    assert registry.list_types() == ("text",)


@pytest.mark.parametrize(
    "connector_type",
    ("", "../text", "Text/Other", "te\u2215xt", "TEXT"),
)
def test_registry_rejects_noncanonical_connector_types(
    connector_type: str,
) -> None:
    registry = ConnectorRegistry()

    with pytest.raises(SourceConnectorError):
        registry.get(connector_type)


def test_passive_connector_is_explicitly_non_refreshable() -> None:
    connector = DescriptorConnectorAdapter("open_notebook").connector()
    descriptor = {
        "source_id": "source-example",
        "source_type": "open_notebook",
    }

    result = connector.refresher.refresh(
        descriptor,
        ConnectorRefreshRequest(dry_run=False),
    )
    revision = connector.revision_resolver.resolve_revision(descriptor)

    assert result["status"] == "skipped"
    assert result["reason_code"] == "connector_does_not_support_remote_refresh"
    assert len(revision.revision_digest) == 64


def test_connector_interfaces_are_independently_substitutable() -> None:
    adapter = DescriptorConnectorAdapter("wiki")
    connector = adapter.connector()
    descriptor = {"source_id": "source-example", "source_type": "wiki"}

    assert connector.validator.validate(descriptor) == ()
    assert connector.inventory_provider.inventory(descriptor).item_count == 0
    assert connector.health_provider.health(descriptor).status == "healthy"
