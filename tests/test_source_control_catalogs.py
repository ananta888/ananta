from __future__ import annotations

from types import SimpleNamespace

from agent.services.source_control_catalogs import (
    ScopedRegisteredWorkspaceCatalog,
    SourceControlReadCatalogService,
)


_TENANT = "tenant-catalog"
_PROJECT = "project-catalog"


class _Remotes:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_authorizations(self, *args, **kwargs):
        del args
        self.calls.append(dict(kwargs))
        return (
            SimpleNamespace(
                connection_ref="remote_github_main",
                authorization_kind="github",
                repository="ananta/example",
                authorization_state="active",
                granted_scopes=("contents:read",),
                remote_url="https://secret.example/repository.git",
                credential="never-return-this",
            ),
            SimpleNamespace(
                connection_ref="remote_git_archive",
                authorization_kind="git",
                repository=None,
                authorization_state="disabled",
                granted_scopes=(),
                remote_url="ssh://secret.example/archive.git",
                credential="never-return-this-either",
            ),
        )


class _Profiles:
    def list_profiles(self, *args, **kwargs):
        del args, kwargs
        return (
            {
                "profile_id": "profile_code_default",
                "label": "Code default",
                "description": "Incremental code indexing",
                "is_default": True,
                "source_types": ["git", "registered_workspace"],
                "task_kinds": ["code_analysis"],
                "retrieval_intents": ["code"],
                "incremental": True,
                "resume": True,
                "progress": True,
                "config_path": "/etc/ananta/private-profile.json",
                "provider_api_key": "never-return-this",
            },
            {
                "profile_id": "profile_notebook",
                "label": "Notebook",
                "description": "Notebook indexing",
                "is_default": False,
                "source_types": ["notebook"],
                "task_kinds": ["analysis"],
                "retrieval_intents": ["notebook"],
                "incremental": False,
                "resume": False,
                "progress": True,
                "config_path": "/etc/ananta/private-notebook.json",
            },
        )


def _service():
    workspaces = ScopedRegisteredWorkspaceCatalog(
        (
            SimpleNamespace(
                workspace_id="workspace_main",
                tenant_id=_TENANT,
                project_id=_PROJECT,
                root="/srv/private/main",
                enabled=True,
                read_only=True,
            ),
            SimpleNamespace(
                workspace_id="workspace_foreign",
                tenant_id="other-tenant",
                project_id=_PROJECT,
                root="/srv/private/foreign",
                enabled=True,
                read_only=True,
            ),
        )
    )
    remotes = _Remotes()
    profiles = _Profiles()
    return (
        SourceControlReadCatalogService(
            workspaces=workspaces,
            remotes=remotes,
            index_profiles=profiles,
        ),
        remotes,
    )


def test_workspace_catalog_is_scoped_read_only_and_hides_roots() -> None:
    service, _ = _service()

    page = service.list_workspaces(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        cursor=None,
        limit=20,
        filters={},
    )

    assert [item["workspace_id"] for item in page["items"]] == [
        "workspace_main"
    ]
    assert page["capabilities"] == {
        "read_only": True,
        "selection_mode": "server_ids_only",
        "project_id": _PROJECT,
    }
    assert page["items"][0]["capabilities"]["raw_path_exposed"] is False
    assert "/srv/private" not in str(page)
    assert "workspace_foreign" not in str(page)


def test_registered_remote_catalog_hides_urls_credentials_and_is_bounded() -> None:
    service, remotes = _service()

    first = service.list_registered_remotes(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        owner_id="catalog-owner",
        is_admin=False,
        cursor=None,
        limit=1,
        filters={"kind": "github"},
    )

    assert len(first["items"]) == 1
    assert first["items"][0]["remote_id"] == "remote_github_main"
    assert first["items"][0]["capabilities"]["remote_url_exposed"] is False
    assert first["items"][0]["capabilities"]["credential_exposed"] is False
    assert "secret.example" not in str(first)
    assert "never-return-this" not in str(first)
    assert remotes.calls == [
        {
            "tenant_id": _TENANT,
            "project_id": _PROJECT,
            "owner_id": "catalog-owner",
        }
    ]


def test_index_profile_catalog_exposes_only_server_profiles() -> None:
    service, _ = _service()

    page = service.list_index_profiles(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        cursor=None,
        limit=20,
        filters={"source": "notebook"},
    )

    assert [item["profile_id"] for item in page["items"]] == [
        "profile_notebook"
    ]
    item = page["items"][0]
    assert item["capabilities"]["selection_only"] is True
    assert item["capabilities"]["config_path_exposed"] is False
    assert item["capabilities"]["task_kinds"] == ["analysis"]
    assert "/etc/ananta" not in str(page)
    assert "provider_api_key" not in str(page)
