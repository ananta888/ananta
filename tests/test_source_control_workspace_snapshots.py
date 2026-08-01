from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from werkzeug.datastructures import FileStorage

from agent.routes.source_control_workspace_snapshots import (
    WORKSPACE_SNAPSHOT_ROUTE_MATRIX,
    create_source_control_workspace_snapshots_blueprint,
)
from agent.services.project_access_authority import (
    AuthorizedProjectScope,
    ProjectCapability,
)
from agent.services.source_control_workspace_catalog import (
    SecureWorkspaceFolderCatalog,
)
from agent.services.source_control_workspace_snapshot_contracts import (
    BrowserSnapshotRelativePath,
    MAX_SNAPSHOT_FILES,
    MAX_SNAPSHOT_FILE_BYTES,
    MAX_SNAPSHOT_RELATIVE_PATH_BYTES,
    MAX_SNAPSHOT_TOTAL_BYTES,
    WorkspaceSnapshotContractError,
    WorkspaceSnapshotLimits,
)
from agent.services.source_control_workspace_snapshot_service import (
    WorkspaceSnapshotUploadError,
    WorkspaceSnapshotUploadService,
)
from agent.services.user_session_tokens import issue_user_access_token


class _ProjectAccess:
    def __init__(self) -> None:
        self.calls = []

    def require(self, **kwargs):
        self.calls.append(dict(kwargs))
        return AuthorizedProjectScope(
            tenant_id=kwargs["tenant_id"],
            project_id=kwargs["project_id"],
            team_id=kwargs["project_id"],
            subject_id=kwargs["subject_id"],
            role="owner",
            status="active",
            capability=kwargs.get("capability", ProjectCapability.READ),
            lock_version=1,
        )


class _PersistentIdempotency:
    def __init__(self) -> None:
        self.rows = {}

    def claim(self, *, idempotency_key, plan_digest):
        row = self.rows.get(idempotency_key)
        if row is None:
            self.rows[idempotency_key] = {
                "digest": plan_digest,
                "state": "claimed",
            }
            return SimpleNamespace(state="claimed", claim_token="claim-token")
        if row["digest"] != plan_digest:
            error = RuntimeError("idempotency_key_conflict")
            error.reason_code = "idempotency_key_conflict"
            error.status_code = 409
            raise error
        if row["state"] == "completed":
            return SimpleNamespace(state="completed", result=row["result"])
        return SimpleNamespace(state="in_progress")

    def complete(
        self,
        *,
        idempotency_key,
        plan_digest,
        claim_token,
        result,
    ):
        assert claim_token == "claim-token"
        assert self.rows[idempotency_key]["digest"] == plan_digest
        self.rows[idempotency_key] = {
            "digest": plan_digest,
            "state": "completed",
            "result": dict(result),
        }


class _WorkspaceRegistrations:
    def __init__(self) -> None:
        self.validations = []
        self.creations = []

    def validate(self, *, principal, payload):
        self.validations.append((principal, dict(payload)))
        return {"validation_handle": "wsv1_" + "x" * 43}

    def create(self, *, principal, payload, idempotency_key):
        self.creations.append(
            (principal, dict(payload), idempotency_key)
        )
        return {
            "workspace_id": "ws_" + "w" * 43,
            "state": "active",
        }


class _RouteService:
    def __init__(self) -> None:
        self.call = None

    def upload(self, **kwargs):
        self.call = kwargs
        return {
            "workspace_id": "ws_" + "w" * 43,
            "state": "active",
            "file_count": len(tuple(kwargs["files"])),
            "total_bytes": 7,
            "replayed": False,
        }


def _principal():
    return SimpleNamespace(
        tenant_id="tenant-example",
        project_id="project-example",
        subject_id="owner-example",
        roles=frozenset({"project_owner"}),
    )


def _upload(filename: str, content: bytes) -> FileStorage:
    return FileStorage(stream=io.BytesIO(content), filename=filename)


def _project_root(root: Path) -> Path:
    tenant = hashlib.sha256(b"tenant-example").hexdigest()
    project = hashlib.sha256(b"project-example").hexdigest()
    return root / tenant / project


def _service(tmp_path: Path, *, limits=None):
    authority = _ProjectAccess()
    folders = SecureWorkspaceFolderCatalog(workspace_root=tmp_path)
    registrations = _WorkspaceRegistrations()
    idempotency = _PersistentIdempotency()
    audit = []
    service = WorkspaceSnapshotUploadService(
        workspace_root=tmp_path,
        project_access=authority,
        folders=folders,
        workspace_registrations=registrations,
        idempotency=idempotency,
        limits=limits,
        audit_sink=lambda action, event: audit.append((action, event)),
        token_factory=lambda: "stage-token",
    )
    return service, authority, registrations, idempotency, audit


def test_default_upload_limits_match_browser_contract() -> None:
    limits = WorkspaceSnapshotLimits()
    assert limits.max_files == MAX_SNAPSHOT_FILES == 2_000
    assert limits.max_file_bytes == MAX_SNAPSHOT_FILE_BYTES == 20 * 1024 * 1024
    assert limits.max_total_bytes == MAX_SNAPSHOT_TOTAL_BYTES == 200 * 1024 * 1024
    assert limits.max_relative_path_bytes == MAX_SNAPSHOT_RELATIVE_PATH_BYTES == 512


@pytest.mark.parametrize(
    "filename",
    (
        "root/../secret.txt",
        "root\\secret.txt",
        "/root/secret.txt",
        "root//secret.txt",
        "root/con",
        "root/.git/config",
        "root/danger\u202efile.txt",
        "single-file.txt",
    ),
)
def test_browser_relative_path_rejects_unsafe_or_ambiguous_names(
    filename: str,
) -> None:
    with pytest.raises(
        WorkspaceSnapshotContractError,
        match="workspace_snapshot_(?:relative_path_invalid|reserved_path_denied)",
    ):
        BrowserSnapshotRelativePath.parse(
            filename,
            limits=WorkspaceSnapshotLimits(),
        )


def test_snapshot_upload_streams_registers_and_returns_path_free_contract(
    tmp_path: Path,
) -> None:
    service, authority, registrations, _idempotency, audit = _service(tmp_path)

    result = service.upload(
        principal=_principal(),
        display_name="Browser Folder",
        files=(
            _upload("selected/src/main.py", b"print('safe')\n"),
            _upload("selected/README.md", b"documentation"),
        ),
        idempotency_key="snapshot-upload-example",
    )

    assert result == {
        "workspace_id": "ws_" + "w" * 43,
        "state": "active",
        "file_count": 2,
        "total_bytes": 27,
        "replayed": False,
    }
    assert authority.calls[-1]["capability"] is ProjectCapability.WRITE
    assert len(registrations.validations) == 1
    assert len(registrations.creations) == 1
    rendered = repr((result, audit))
    assert str(tmp_path) not in rendered
    assert "main.py" not in rendered
    assert "documentation" not in rendered
    assert set(audit[-1][1]) == {
        "tenant_id",
        "project_id",
        "actor_id",
        "workspace_id_digest",
        "decision",
        "reason_code",
        "file_count",
        "total_bytes",
        "replayed",
    }


def test_completed_idempotency_replay_does_not_register_twice(
    tmp_path: Path,
) -> None:
    service, _authority, registrations, _idempotency, _audit = _service(tmp_path)
    arguments = {
        "principal": _principal(),
        "display_name": "Replayable Folder",
        "idempotency_key": "snapshot-replay-example",
    }

    first = service.upload(
        **arguments,
        files=(_upload("folder/source.txt", b"same"),),
    )
    replay = service.upload(
        **arguments,
        files=(_upload("folder/source.txt", b"same"),),
    )

    assert first["replayed"] is False
    assert replay == {**first, "replayed": True}
    assert len(registrations.creations) == 1
    project_entries = tuple(_project_root(tmp_path).iterdir())
    assert not any(item.name.endswith(".partial") for item in project_entries)


def test_idempotency_key_rejects_changed_content_and_cleans_staging(
    tmp_path: Path,
) -> None:
    service, _authority, _registrations, _idempotency, audit = _service(tmp_path)
    service.upload(
        principal=_principal(),
        display_name="Conflict Folder",
        files=(_upload("folder/source.txt", b"first"),),
        idempotency_key="snapshot-conflict-example",
    )

    with pytest.raises(WorkspaceSnapshotUploadError, match="idempotency_key_conflict"):
        service.upload(
            principal=_principal(),
            display_name="Conflict Folder",
            files=(_upload("folder/source.txt", b"second"),),
            idempotency_key="snapshot-conflict-example",
        )

    assert audit[-1][1]["decision"] == "deny"
    assert not any(
        item.name.endswith(".partial")
        for item in _project_root(tmp_path).iterdir()
    )


@pytest.mark.parametrize(
    ("uploads", "reason_code"),
    (
        ((), "workspace_snapshot_files_required"),
        (("root/empty.txt", b""), "workspace_snapshot_empty_file_denied"),
        (
            ("root/a.txt", b"12"),
            "workspace_snapshot_file_bytes_exceeded",
        ),
    ),
)
def test_strict_upload_limits_reject_and_cleanup(
    tmp_path: Path,
    uploads,
    reason_code: str,
) -> None:
    limits = WorkspaceSnapshotLimits(
        max_files=2,
        max_file_bytes=1,
        max_total_bytes=2,
        max_relative_path_bytes=32,
        max_depth=3,
    )
    service, _authority, _registrations, _idempotency, _audit = _service(
        tmp_path,
        limits=limits,
    )
    values = () if uploads == () else (_upload(*uploads),)

    with pytest.raises(WorkspaceSnapshotUploadError, match=reason_code):
        service.upload(
            principal=_principal(),
            display_name="Bounded Folder",
            files=values,
            idempotency_key="snapshot-limits-example",
        )

    assert not any(
        item.name.endswith(".partial")
        for item in _project_root(tmp_path).iterdir()
    )


def test_case_collisions_and_file_directory_collisions_are_rejected(
    tmp_path: Path,
) -> None:
    for files in (
        (
            _upload("root/Source.py", b"one"),
            _upload("root/source.py", b"two"),
        ),
        (
            _upload("root/pkg/module.py", b"one"),
            _upload("root/pkg", b"two"),
        ),
    ):
        service, _authority, _registrations, _idempotency, _audit = _service(
            tmp_path
        )
        with pytest.raises(
            WorkspaceSnapshotUploadError,
            match="workspace_snapshot_case_collision",
        ):
            service.upload(
                principal=_principal(),
                display_name="Collision Folder",
                files=files,
                idempotency_key="snapshot-collision-example",
            )


def test_catalog_never_projects_in_progress_snapshot_staging(
    tmp_path: Path,
) -> None:
    project_root = _project_root(tmp_path)
    project_root.mkdir(parents=True)
    staging = project_root / ".ananta-snapshot-private.partial"
    staging.mkdir()
    (staging / "private.txt").write_text("private", encoding="utf-8")

    listing = SecureWorkspaceFolderCatalog(workspace_root=tmp_path).list_folders(
        tenant_id="tenant-example",
        project_id="project-example",
    )

    assert listing == ()


def test_workspace_snapshot_blueprint_accepts_exact_multipart_contract() -> None:
    app = Flask(__name__)
    authority = _ProjectAccess()
    app.extensions["project_access_authority"] = authority
    app.config["TESTING"] = True
    service = _RouteService()
    app.register_blueprint(
        create_source_control_workspace_snapshots_blueprint(service)
    )
    token = issue_user_access_token(username="admin", role="admin")

    response = app.test_client().post(
        "/api/source-control/v1/workspace-snapshots"
        "?project_id=project-example",
        data={
            "display_name": "Browser Folder",
            "files": [
                (io.BytesIO(b"content"), "folder/source.txt"),
            ],
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "snapshot-route-example",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "workspace_id": "ws_" + "w" * 43,
        "state": "active",
        "file_count": 1,
        "total_bytes": 7,
        "replayed": False,
    }
    assert service.call["display_name"] == "Browser Folder"
    assert service.call["idempotency_key"] == "snapshot-route-example"
    assert service.call["principal"].project_id == "project-example"
    assert WORKSPACE_SNAPSHOT_ROUTE_MATRIX[0].rule == "/workspace-snapshots"


def test_workspace_snapshot_blueprint_rejects_json_and_extra_query_fields() -> None:
    app = Flask(__name__)
    app.extensions["project_access_authority"] = _ProjectAccess()
    app.config["TESTING"] = True
    service = _RouteService()
    app.register_blueprint(
        create_source_control_workspace_snapshots_blueprint(service)
    )
    token = issue_user_access_token(username="admin", role="admin")
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}

    json_response = client.post(
        "/api/source-control/v1/workspace-snapshots"
        "?project_id=project-example",
        json={"display_name": "Browser Folder"},
        headers=headers,
    )
    query_response = client.post(
        "/api/source-control/v1/workspace-snapshots"
        "?project_id=project-example&path=/private",
        data={
            "display_name": "Browser Folder",
            "files": (io.BytesIO(b"x"), "folder/source.txt"),
        },
        headers={**headers, "Idempotency-Key": "snapshot-route-example"},
        content_type="multipart/form-data",
    )

    assert json_response.status_code == 415
    assert json_response.get_json()["error"]["code"] == (
        "workspace_snapshot_multipart_required"
    )
    assert query_response.status_code == 400
    assert query_response.get_json()["error"]["code"] == "query_fields_forbidden"
    assert service.call is None
