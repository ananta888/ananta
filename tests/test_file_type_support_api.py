from __future__ import annotations

from pathlib import Path

from agent.services.file_type_support_service import (
    FileTypeSupportFilter,
    FileTypeSupportService,
)
from ananta_contracts.file_type_support import load_file_type_support_registry

ROOT = Path(__file__).resolve().parents[1]


def _service(*, runtime_available: bool = True) -> FileTypeSupportService:
    registry = load_file_type_support_registry(ROOT)
    requirements = {
        requirement
        for descriptor in registry.descriptors
        for pipeline in registry.pipelines
        for dimension in ("indexed", "symbols", "relationships")
        for requirement in descriptor.support_for(pipeline).capability(dimension).runtime_requirements
    }
    return FileTypeSupportService(
        ROOT,
        registry=registry,
        runtime_availability={requirement: runtime_available for requirement in requirements},
    )


def test_support_service_projects_truth_and_separate_selectors() -> None:
    service = _service()
    payload = service.support_matrix(FileTypeSupportFilter.build(pipelines=["rag_helper"]))

    assert payload["registry_version"] == service.registry.registry_version
    assert len(payload["registry_digest"]) == 64
    assert payload["runtime_scope"] == "current_process"
    assert payload["runtime_notice"] == "worker_pipeline_runtime_must_be_reported_by_the_executing_worker"
    assert payload["authorization_notice"] == "registry_support_does_not_grant_file_access_or_execution"
    markdown = next(row for row in payload["rows"] if row["format_id"] == "markdown")
    assert markdown["selectors"] == {
        "extensions": [".md"],
        "exact_filenames": [],
        "patterns": [],
        "compound_suffixes": [],
        "shebangs": [],
        "text_fallback": False,
    }
    assert markdown["parser_strategy"]
    assert markdown["fallback_strategy"]
    assert isinstance(markdown["known_limits"], list)
    assert markdown["security_class"] == "untrusted_text"
    assert markdown["enabled"] is True
    assert set(markdown["capabilities"]) == {"indexed", "symbols", "relationships"}
    for capability in markdown["capabilities"].values():
        assert set(("configured", "runtime_available", "verified", "effective")) <= set(capability)

    all_rows = service.support_matrix()["rows"]
    compose = next(
        row
        for row in all_rows
        if row["format_id"] == "docker_compose" and row["pipeline"] == "setup_index"
    )
    shell = next(
        row
        for row in all_rows
        if row["format_id"] == "shell" and row["pipeline"] == "repository_map"
    )
    assert "compose.yaml" in compose["selectors"]["exact_filenames"]
    assert "docker-compose*.yml" in compose["selectors"]["patterns"]
    assert compose["selectors"]["extensions"] == []
    assert shell["selectors"]["shebangs"]

    xml = next(
        row
        for row in payload["rows"]
        if row["format_id"] == "xml" and row["pipeline"] == "rag_helper"
    )
    assert xml["support_level"] == "domain_parser"
    markdown = next(row for row in payload["rows"] if row["format_id"] == "markdown")
    dockerfile = next(row for row in payload["rows"] if row["format_id"] == "dockerfile")
    assert markdown["support_level"] == "domain_parser"
    assert dockerfile["support_level"] == "domain_parser"


def test_support_service_combines_priority_dimension_pipeline_and_runtime_filters() -> None:
    payload = _service(runtime_available=False).support_matrix(
        FileTypeSupportFilter.build(
            priorities=["P0"],
            dimensions=["relationships"],
            pipelines=["rag_helper"],
            missing_runtime=True,
            enabled=True,
        )
    )

    assert payload["rows"]
    assert all(row["priority"] == "P0" for row in payload["rows"])
    assert all(row["pipeline"] == "rag_helper" for row in payload["rows"])
    assert all(row["capabilities"]["relationships"]["configured"] is True for row in payload["rows"])
    assert all(row["capabilities"]["relationships"]["effective"] is False for row in payload["rows"])
    assert all(row["missing_runtime"] is True for row in payload["rows"])


def test_support_matrix_regression_snapshot_keeps_verified_deep_support() -> None:
    rows = {
        (row["format_id"], row["pipeline"]): (
            row["support_level"],
            row["capabilities"]["indexed"]["verified"],
            row["capabilities"]["symbols"]["verified"],
            row["capabilities"]["relationships"]["verified"],
        )
        for row in _service().support_matrix()["rows"]
    }

    assert rows[("python", "semantic_translation")] == (
        "semantic_graph",
        True,
        True,
        True,
    )
    assert rows[("java", "rag_helper")] == (
        "semantic_graph",
        True,
        True,
        True,
    )


def test_file_type_support_api_is_authenticated_and_filterable(client, admin_auth_header) -> None:
    unauthorized = client.get("/knowledge/file-type-support")
    assert unauthorized.status_code == 401

    response = client.get(
        "/knowledge/file-type-support?priority=P2&pipeline=repository_map&missing_parser=true",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["runtime_scope"] == "hub_process"
    assert payload["filters"]["priority"] == ["P2"]
    assert payload["filters"]["pipeline"] == ["repository_map"]
    assert payload["filters"]["missing_parser"] is True
    assert payload["rows"]
    assert all(row["priority"] == "P2" for row in payload["rows"])
    assert all(row["pipeline"] == "repository_map" for row in payload["rows"])
    assert all(row["missing_parser"] is True for row in payload["rows"])

    capability_response = client.get(
        "/knowledge/file-type-support"
        "?support_level=semantic_graph&dimension=relationships"
        "&pipeline=rag_helper&missing_runtime=false&enabled=true",
        headers=admin_auth_header,
    )
    assert capability_response.status_code == 200
    capability_payload = capability_response.get_json()["data"]
    assert capability_payload["rows"]
    assert all(row["support_level"] == "semantic_graph" for row in capability_payload["rows"])
    assert all(row["capabilities"]["relationships"]["configured"] for row in capability_payload["rows"])
    assert all(row["enabled"] is True for row in capability_payload["rows"])


def test_file_type_support_api_rejects_invalid_and_unknown_filters(client, admin_auth_header) -> None:
    invalid_boolean = client.get(
        "/knowledge/file-type-support?missing_runtime=sometimes",
        headers=admin_auth_header,
    )
    unknown_filter = client.get(
        "/knowledge/file-type-support?authorization=allow",
        headers=admin_auth_header,
    )
    invalid_priority = client.get(
        "/knowledge/file-type-support?priority=P999",
        headers=admin_auth_header,
    )

    assert invalid_boolean.status_code == 400
    assert unknown_filter.status_code == 400
    assert invalid_priority.status_code == 400
