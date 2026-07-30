from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.services.source_control_artifact_download import (
    SourceControlArtifactDownloadError,
    _parse_range,
)
from agent.services.source_control_catalogs import (
    SourceControlReadCatalogService,
    SourceRegistryRegisteredWorkspaceCatalog,
)


class _Registry:
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def list_sources(self, *, include_disabled):
        assert include_disabled is True
        return [self.descriptor]


class _WorkspaceRegistration:
    def __init__(self, root: Path):
        self.root = root


class _WorkspaceRegistrations:
    def __init__(self, root: Path):
        self.root = root

    def resolve_workspace(self, workspace_id):
        return (
            _WorkspaceRegistration(self.root)
            if workspace_id == "workspace-example"
            else None
        )


class _Profiles:
    def list_profiles(self):
        return []


class _Remotes:
    def list_authorizations(self, **kwargs):
        return ()


def _descriptor(owner_id: str, **binding_overrides):
    binding = {
        "connector_type": "registered_workspace",
        "tenant_id": "tenant-example",
        "project_id": "project-example",
        "owner_id": owner_id,
        "workspace_id": "workspace-example",
        "read_only": True,
        **binding_overrides,
    }
    return {
        "source_id": "source-example",
        "source_type": "local_dump",
        "enabled": True,
        "extensions": {"source_control": binding},
    }


def test_workspace_catalog_is_owner_scoped_and_never_projects_root(
    tmp_path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    catalog = SourceRegistryRegisteredWorkspaceCatalog(
        registry=_Registry(_descriptor("owner-example")),
        registrations=_WorkspaceRegistrations(root),
    )
    service = SourceControlReadCatalogService(
        workspaces=catalog,
        remotes=_Remotes(),
        index_profiles=_Profiles(),
    )

    denied = service.list_workspaces(
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="other-owner",
        roles=frozenset(),
        cursor=None,
        limit=50,
        filters={},
    )
    allowed = service.list_workspaces(
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="owner-example",
        roles=frozenset(),
        cursor=None,
        limit=50,
        filters={},
    )

    assert denied["items"] == []
    assert allowed["items"][0]["workspace_id"] == "workspace-example"
    assert str(root) not in repr(allowed)


def test_workspace_catalog_rejects_path_bearing_registry_binding(
    tmp_path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    catalog = SourceRegistryRegisteredWorkspaceCatalog(
        registry=_Registry(
            _descriptor("owner-example", root=str(root))
        ),
        registrations=_WorkspaceRegistrations(root),
    )

    assert catalog.list_registered(
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
    ) == ()


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    (
        (None, 10, (0, 9, 200, None)),
        ("bytes=2-5", 10, (2, 5, 206, "bytes 2-5/10")),
        ("bytes=-3", 10, (7, 9, 206, "bytes 7-9/10")),
        ("bytes=8-", 10, (8, 9, 206, "bytes 8-9/10")),
    ),
)
def test_artifact_range_is_single_and_bounded(header, size, expected):
    assert _parse_range(header, size) == expected


@pytest.mark.parametrize(
    "header",
    ("bytes=1-2,4-5", "bytes=20-30", "items=0-1", "bytes=-0"),
)
def test_artifact_range_tamper_is_denied(header):
    with pytest.raises(
        SourceControlArtifactDownloadError,
        match="range_not_satisfiable",
    ):
        _parse_range(header, 10)


class _Index:
    def __init__(self, output_dir: Path):
        self.output_dir = str(output_dir)


def _artifact_service(root: Path):
    from agent.services.source_control_artifact_download import (
        SourceControlArtifactDownloadService,
    )

    return SourceControlArtifactDownloadService(
        engine=object(),
        artifact_root=root,
        destinations=object(),
        effective_access=object(),
    )


def test_artifact_output_must_remain_under_configured_root(tmp_path) -> None:
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    service = _artifact_service(root)

    with pytest.raises(
        SourceControlArtifactDownloadError,
        match="artifact_outside_root",
    ):
        service._contained_output(_Index(outside))


def test_artifact_symlink_is_denied_even_when_target_is_inside_root(
    tmp_path,
) -> None:
    root = tmp_path / "artifacts"
    output = root / "index"
    root.mkdir()
    output.mkdir()
    target = output / "target.json"
    target.write_bytes(b"trusted")
    link = output / "index.json"
    link.symlink_to(target)
    service = _artifact_service(root)

    with pytest.raises(
        SourceControlArtifactDownloadError,
        match="artifact_symlink_forbidden",
    ):
        service._contained_file(output, link.name)


def test_artifact_snapshot_rejects_hash_drift(tmp_path) -> None:
    root = tmp_path / "artifacts"
    output = root / "index"
    root.mkdir()
    output.mkdir()
    artifact = output / "index.json"
    artifact.write_bytes(b"trusted")
    service = _artifact_service(root)

    with pytest.raises(
        SourceControlArtifactDownloadError,
        match="artifact_hash_drift",
    ):
        service._verified_snapshot(
            artifact,
            expected_size=len(b"trusted"),
            expected_digest=hashlib.sha256(b"tampered").hexdigest(),
        )
