from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

import agent.services.source_control_content_admission as content_admission
from agent.db_models.source_control import (
    SourceConnectionDB,
    SourceControlContentDB,
    SourceRevisionDB,
)
from agent.services.source_control_api_runtime import (
    SQLSourceControlOperationStore,
)
from agent.services.source_control_content_admission import (
    SourceControlContentAdmissionError,
    SourceControlContentAdmissionService,
)


_TENANT = "tenant-content"
_PROJECT = "project-content"
_ACTOR = "content-owner"


def _service() -> tuple[object, SourceControlContentAdmissionService]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    service = SourceControlContentAdmissionService(
        engine=engine,
        idempotency=SQLSourceControlOperationStore(
            engine, clock=lambda: 1_800_000_000.0
        ),
        clock=lambda: 1_800_000_000.0,
    )
    return engine, service


def _direct_payload(*, dry_run: bool) -> dict[str, object]:
    return {
        "project_id": _PROJECT,
        "source_type": "direct_text",
        "display_name": "Release notes",
        "sensitivity": "internal",
        "content": "# Release\n\nBounded source content.",
        "media_type": "text/markdown",
        "dry_run": dry_run,
    }


def test_direct_text_preview_and_admission_are_bounded_and_idempotent() -> None:
    engine, service = _service()

    preview = service.validate(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        actor_id=_ACTOR,
        payload=_direct_payload(dry_run=True),
    )
    created = service.admit(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        actor_id=_ACTOR,
        payload=_direct_payload(dry_run=False),
        idempotency_key="admit-release-notes",
    )
    replay = service.admit(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        actor_id=_ACTOR,
        payload=_direct_payload(dry_run=False),
        idempotency_key="admit-release-notes",
    )

    assert preview["valid"] is True
    assert preview["preview"]["source_type"] == "direct_text"
    assert preview["preview"]["capabilities"] == {
        "immutable_revision": True,
        "raw_location_inputs_accepted": False,
        "browser_ids_accepted": False,
        "binary_content_accepted": False,
        "secret_content_accepted": False,
    }
    assert created == replay
    assert created["connection"]["project_id"] == _PROJECT
    assert created["connection"]["tenant_id"] == _TENANT
    assert created["revision"]["admission_state"] == "admitted"
    assert created["revision"]["source_revision_id"].startswith("srev_")
    assert "path" not in str(created).lower()
    with Session(engine) as db:
        assert len(list(db.exec(select(SourceConnectionDB)).all())) == 1
        assert len(list(db.exec(select(SourceRevisionDB)).all())) == 1
        rows = list(db.exec(select(SourceControlContentDB)).all())
        assert len(rows) == 1
        assert rows[0].project_id == _PROJECT


def test_content_admission_rejects_client_ids_paths_binary_and_secrets() -> None:
    _, service = _service()
    invalid_payloads = [
        {
            **_direct_payload(dry_run=True),
            "source_revision_id": "srev_client_supplied",
        },
        {
            **_direct_payload(dry_run=True),
            "path": "/home/user/private.txt",
        },
        {
            **_direct_payload(dry_run=True),
            "content": "prefix\u0000suffix",
        },
        {
            **_direct_payload(dry_run=True),
            "content": "AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF",
        },
    ]

    for payload in invalid_payloads:
        try:
            service.validate(
                tenant_id=_TENANT,
                project_id=_PROJECT,
                actor_id=_ACTOR,
                payload=payload,
            )
        except SourceControlContentAdmissionError:
            continue
        raise AssertionError(f"payload unexpectedly admitted: {payload!r}")


def test_notebook_accepts_text_outputs_and_rejects_rich_outputs(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        content_admission.settings,
        "codecompass_max_notebook_output_bytes",
        1_024,
    )
    _, service = _service()
    valid = {
        "project_id": _PROJECT,
        "source_type": "notebook",
        "display_name": "Analysis",
        "sensitivity": "internal",
        "notebook": {
            "cells": [
                {
                    "cell_type": "markdown",
                    "source": "# Analysis",
                    "outputs": [],
                },
                {
                    "cell_type": "code",
                    "source": "print('bounded')",
                    "outputs": [
                        {
                            "output_type": "stream",
                            "text": "bounded\n",
                        }
                    ],
                },
            ]
        },
        "dry_run": True,
    }

    preview = service.validate(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        actor_id=_ACTOR,
        payload=valid,
    )
    assert preview["preview"]["cell_count"] == 2
    assert preview["preview"]["output_bytes"] == len("bounded\n")

    invalid = {
        **valid,
        "notebook": {
            "cells": [
                {
                    "cell_type": "code",
                    "source": "display(image)",
                    "outputs": [
                        {
                            "output_type": "display_data",
                            "text": "iVBORw0KGgo=",
                        }
                    ],
                }
            ]
        },
    }
    try:
        service.validate(
            tenant_id=_TENANT,
            project_id=_PROJECT,
            actor_id=_ACTOR,
            payload=invalid,
        )
    except SourceControlContentAdmissionError as exc:
        assert exc.reason_code in {
            "notebook_binary_output_forbidden",
            "notebook_output_invalid",
        }
    else:
        raise AssertionError("rich notebook output unexpectedly admitted")


def test_content_project_scope_cannot_be_asserted_by_the_browser() -> None:
    _, service = _service()
    payload = _direct_payload(dry_run=True)
    payload["project_id"] = "other-project"

    try:
        service.validate(
            tenant_id=_TENANT,
            project_id=_PROJECT,
            actor_id=_ACTOR,
            payload=payload,
        )
    except SourceControlContentAdmissionError as exc:
        assert exc.status_code in {400, 403}
    else:
        raise AssertionError("cross-project content admission accepted")
