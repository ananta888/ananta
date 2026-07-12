from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    AuditLogDB,
    TaskDB,
    VoiceConfigurationDeltaDB,
    VoiceConsentDB,
    VoiceDeletionTombstoneDB,
    VoiceFeedbackDB,
    VoiceGovernanceIdempotencyDB,
    VoicePersonalizationProfileDB,
    VoiceResultArtifactDB,
    VoiceReviewDB,
    VoiceRuntimeCleanupDB,
)
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_result_artifact_service import get_voice_result_artifact_service
from agent.services.voice_runtime_cleanup_service import get_voice_runtime_cleanup_service
from agent.services.voice_stream_session_service import get_voice_stream_session_service

PROFILE_ID = "privacy-complete"
OTHER_PROFILE_ID = "privacy-other"
SUBJECT = "testuser"
TENANT_ID = "testuser"


@pytest.fixture(autouse=True)
def _privacy_cache_gc():
    with patch.object(
        get_voice_runtime_cleanup_service(),
        "_restricted_cache_gc",
        return_value=None,
    ) as cache_gc:
        yield cache_gc


def _headers(auth: dict[str, str], key: str) -> dict[str, str]:
    return {**auth, "Idempotency-Key": key}


def _put_configuration(client, auth, *, scope: str, scope_id: str, key: str) -> None:
    response = client.put(
        "/v1/voice/configuration",
        headers=_headers(auth, key),
        json={
            "scope": scope,
            "scope_id": scope_id,
            "delta": {"confidence_threshold": 0.81},
        },
    )
    assert response.status_code == 200


def _save_voice_task(
    task_id: str,
    *,
    profile_id: str | None,
    parent_task_id: str | None = None,
    source_task_id: str | None = None,
    configuration_session_id: str | None = None,
    task_kind: str | None = None,
    scope_tenant_id: str = TENANT_ID,
    scope_subject: str = SUBJECT,
    scoped: bool = True,
    last_output: str | None = None,
    verification_status: dict | None = None,
) -> None:
    from agent.repository import task_repo

    normalized_task_kind = task_kind or ("restricted_inference" if parent_task_id else "voice_transcription")
    context_key = {
        "restricted_inference": "restricted_inference",
        "voice_generative_judge": "voice_generative_judge",
        "voice_transcription": "voice_transcription",
    }[normalized_task_kind]
    voice_context = {}
    if profile_id is not None:
        voice_context["profile_id"] = profile_id
    if scoped:
        voice_context.update(
            {
                "tenant_scope_hash": hashlib.sha256(scope_tenant_id.encode()).hexdigest(),
                "owner_subject_hash": hashlib.sha256(scope_subject.encode()).hexdigest(),
            }
        )
    if configuration_session_id is not None:
        voice_context["configuration_session_id"] = configuration_session_id
    voice_context["persistence_owner"] = "hub"
    task_repo.save(
        TaskDB(
            id=task_id,
            title="Privacy-scoped voice metadata",
            description="Content-free voice task metadata.",
            status="in_progress",
            task_kind=normalized_task_kind,
            parent_task_id=parent_task_id,
            source_task_id=source_task_id,
            last_output=last_output,
            verification_status=verification_status or {},
            worker_execution_context={context_key: voice_context},
        )
    )


def test_profile_delete_removes_all_hub_voice_references_and_replay_cleans_new_residue(
    client,
    user_auth_header,
    _privacy_cache_gc,
) -> None:
    secret = "DELETE_ME_SPOKEN_SECRET"
    principal = VoicePrincipal(tenant_id=TENANT_ID, subject=SUBJECT)
    stream_service = get_voice_stream_session_service()

    consent = client.put(
        f"/v1/voice/consents/{PROFILE_ID}",
        headers=_headers(user_auth_header, "privacy-consent"),
        json={
            "granted": True,
            "categories": ["preferences", "text_corrections", "vocabulary"],
            "retention_days": 30,
        },
    )
    assert consent.status_code == 200
    imported = client.post(
        f"/v1/voice/personalization/{PROFILE_ID}/import",
        headers=_headers(user_auth_header, "privacy-import"),
        json={
            "schema_version": "voice-personalization.v1",
            "profile_id": PROFILE_ID,
            "items": [
                {
                    "kind": "vocabulary",
                    "source_text": None,
                    "target_text": secret,
                    "metadata": {"language": "de"},
                }
            ],
        },
    )
    assert imported.status_code == 200

    review_session_id = "privacy-review-session"
    review_artifact = get_voice_result_artifact_service().create(
        principal,
        request_hash="privacy-review-result",
        profile_id=PROFILE_ID,
        result={
            "text": secret,
            "candidates": [{"candidate_id": "privacy-candidate", "text": secret}],
        },
    )
    review_response = client.post(
        "/v1/voice/reviews",
        headers=_headers(user_auth_header, "privacy-review"),
        json={
            "profile_id": PROFILE_ID,
            "session_id": review_session_id,
            "result_ref": review_artifact["id"],
            "candidate_ids": ["privacy-candidate"],
        },
    )
    review_id = review_response.get_json()["data"]["review"]["id"]
    decided = client.post(
        f"/v1/voice/reviews/{review_id}/decision",
        headers=_headers(user_auth_header, "privacy-review-decision"),
        json={
            "decision": "correct",
            "expected_version": 1,
            "selected_candidate_id": "privacy-candidate",
            "correction_text": secret,
        },
    )
    assert decided.status_code == 200

    configuration_session_id = "privacy-active-configuration"
    batch_configuration_session_id = "privacy-batch-configuration"
    _put_configuration(
        client,
        user_auth_header,
        scope="profile",
        scope_id=PROFILE_ID,
        key="privacy-profile-config",
    )
    _put_configuration(
        client,
        user_auth_header,
        scope="session",
        scope_id=review_session_id,
        key="privacy-review-session-config",
    )
    _put_configuration(
        client,
        user_auth_header,
        scope="session",
        scope_id=configuration_session_id,
        key="privacy-active-session-config",
    )
    _put_configuration(
        client,
        user_auth_header,
        scope="session",
        scope_id=batch_configuration_session_id,
        key="privacy-batch-session-config",
    )
    _put_configuration(
        client,
        user_auth_header,
        scope="profile",
        scope_id=OTHER_PROFILE_ID,
        key="privacy-other-config",
    )

    export_response = client.post(
        f"/v1/voice/personalization/{PROFILE_ID}/fine-tuning-export-tasks",
        headers=_headers(user_auth_header, "privacy-training-export"),
        json={"confirmed": True, "purpose": "private voice", "license": "private"},
    )
    assert export_response.status_code == 201
    export_data = export_response.get_json()["data"]
    training_task_id = export_data["task_id"]
    training_artifact_id = export_data["artifact_ref"]

    active_task_id = "voice-privacy-active-task"
    restricted_child_id = "voice-privacy-restricted-child"
    generative_child_id = "voice-privacy-generative-child"
    batch_task_id = "voice-privacy-batch-task"
    other_task_id = "voice-privacy-other-task"
    _save_voice_task(active_task_id, profile_id=PROFILE_ID)
    _save_voice_task(restricted_child_id, profile_id=PROFILE_ID, parent_task_id=active_task_id)
    _save_voice_task(
        generative_child_id,
        profile_id=PROFILE_ID,
        parent_task_id=active_task_id,
        task_kind="voice_generative_judge",
    )
    _save_voice_task(
        batch_task_id,
        profile_id=PROFILE_ID,
        configuration_session_id=batch_configuration_session_id,
    )
    _save_voice_task(other_task_id, profile_id=OTHER_PROFILE_ID)
    active_stream = stream_service.create(
        principal,
        runtime_session_id="runtime-privacy-active",
        deadline_seconds=60,
        profile_id=PROFILE_ID,
        configuration_session_id=configuration_session_id,
        task_id=active_task_id,
    )

    delete_headers = _headers(user_auth_header, "privacy-complete-delete")
    with patch("agent.services.voice_provider.get_voice_provider_service") as provider_factory:
        deleted = client.delete(
            f"/v1/voice/privacy/{PROFILE_ID}",
            headers=delete_headers,
            json={"confirmed": True},
        )

        assert deleted.status_code == 200
        deletion = deleted.get_json()["data"]["deletion"]
        assert deletion["revoked_stream_count"] == 1
        assert deletion["runtime_cleanup_pending"] is False
        assert deletion["deleted_by_store"]["tasks"] >= 5
        assert deletion["deleted_by_store"]["voice_configuration_deltas"] == 4
        provider_factory.return_value.delete_stream.assert_called_once_with(
            runtime_session_id="runtime-privacy-active",
            request_id=f"privacy-delete-{active_stream.session_id}",
        )

        with pytest.raises(VoiceGovernanceError) as missing_stream:
            stream_service.require(principal, active_stream.session_id)
        assert missing_stream.value.code == "voice_stream.not_found"

        with Session(engine) as session:
            for model in (
                VoiceConsentDB,
                VoiceFeedbackDB,
                VoicePersonalizationProfileDB,
                VoiceResultArtifactDB,
                VoiceReviewDB,
            ):
                assert not session.exec(
                    select(model).where(
                        model.tenant_id == TENANT_ID,
                        model.owner_subject == SUBJECT,
                        model.profile_id == PROFILE_ID,
                    )
                ).all()
            deleted_configs = session.exec(
                select(VoiceConfigurationDeltaDB).where(
                    VoiceConfigurationDeltaDB.tenant_id == TENANT_ID,
                    VoiceConfigurationDeltaDB.owner_subject == SUBJECT,
                    VoiceConfigurationDeltaDB.scope_id.in_(
                        {
                            PROFILE_ID,
                            review_session_id,
                            configuration_session_id,
                            batch_configuration_session_id,
                        }
                    ),
                )
            ).all()
            assert deleted_configs == []
            assert session.exec(
                select(VoiceConfigurationDeltaDB).where(
                    VoiceConfigurationDeltaDB.scope == "profile",
                    VoiceConfigurationDeltaDB.scope_id == OTHER_PROFILE_ID,
                )
            ).first()
            assert session.get(TaskDB, active_task_id) is None
            assert session.get(TaskDB, restricted_child_id) is None
            assert session.get(TaskDB, generative_child_id) is None
            assert session.get(TaskDB, batch_task_id) is None
            assert session.get(TaskDB, training_task_id) is None
            assert session.get(TaskDB, other_task_id) is not None
            surviving_idempotency = session.exec(
                select(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.tenant_id == TENANT_ID,
                    VoiceGovernanceIdempotencyDB.owner_subject == SUBJECT,
                )
            ).all()
            assert all(
                record.operation.startswith("voice_privacy.delete:")
                or PROFILE_ID not in str(record.result_metadata)
                for record in surviving_idempotency
            )
            assert not any(
                record.operation.startswith("voice_privacy.delete:")
                for record in surviving_idempotency
            )
            tombstone = session.exec(select(VoiceDeletionTombstoneDB)).one()
            assert not hasattr(tombstone, "tenant_id")
            assert not hasattr(tombstone, "owner_subject")
            assert not hasattr(tombstone, "profile_id")
            assert PROFILE_ID not in str(tombstone.model_dump())
            assert SUBJECT not in str(tombstone.model_dump())
            audit_rows = session.exec(select(AuditLogDB)).all()
            assert secret not in str([row.details for row in audit_rows])
            deletion_audit = next(row for row in reversed(audit_rows) if row.action == "voice_profile_deleted")
            assert set(deletion_audit.details) >= {
                "deleted_count",
                "scope_digest",
                "revoked_stream_count",
                "runtime_cleanup_pending",
                "status",
            }
            assert PROFILE_ID not in str(deletion_audit.details)
            privacy_details = {
                key: value for key, value in deletion_audit.details.items() if key != "_event"
            }
            assert SUBJECT not in str(privacy_details)

        assert _privacy_cache_gc.call_count >= 1

        assert client.get(f"/v1/voice/reviews/{review_id}", headers=user_auth_header).status_code == 404
        assert (
            client.get(
                (f"/v1/voice/personalization/{PROFILE_ID}/fine-tuning-exports/{training_artifact_id}"),
                headers=user_auth_header,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/voice/personalization/{PROFILE_ID}/export",
                headers=user_auth_header,
            ).get_json()["data"]["personalization"]["items"]
            == []
        )
        assert (
            client.get(
                f"/v1/voice/consents/{PROFILE_ID}",
                headers=user_auth_header,
            ).get_json()["data"]["consent"]["granted"]
            is False
        )

        replay_configuration_session = "privacy-replay-session"
        replay_task_id = "voice-privacy-replay-task"
        _put_configuration(
            client,
            user_auth_header,
            scope="session",
            scope_id=replay_configuration_session,
            key="privacy-replay-config",
        )
        _save_voice_task(replay_task_id, profile_id=PROFILE_ID)
        replay_stream = stream_service.create(
            principal,
            runtime_session_id="runtime-privacy-replay",
            deadline_seconds=60,
            profile_id=PROFILE_ID,
            configuration_session_id=replay_configuration_session,
            task_id=replay_task_id,
        )
        with Session(engine) as session:
            tombstone = session.exec(select(VoiceDeletionTombstoneDB)).one()
            restored_at = tombstone.deleted_at - 1
            replay_stream.created_at = restored_at
            replay_task = session.get(TaskDB, replay_task_id)
            assert replay_task is not None
            replay_task.created_at = restored_at
            session.add(replay_task)
            replay_configuration = session.exec(
                select(VoiceConfigurationDeltaDB).where(
                    VoiceConfigurationDeltaDB.scope_id == replay_configuration_session
                )
            ).one()
            replay_configuration.created_at = restored_at
            session.add(replay_configuration)
            session.commit()
        replay = client.delete(
            f"/v1/voice/privacy/{PROFILE_ID}",
            headers=delete_headers,
            json={"confirmed": True},
        )

        assert replay.status_code == 200
        replay_data = replay.get_json()["data"]["deletion"]
        assert replay_data["idempotent_replay"] is True
        assert replay_data["replay_cleanup_stream_count"] == 1
        assert replay_data["replay_cleanup_deleted_count"] >= 3
        provider_factory.return_value.delete_stream.assert_called_with(
            runtime_session_id="runtime-privacy-replay",
            request_id=f"privacy-delete-{replay_stream.session_id}",
        )
        with Session(engine) as session:
            assert session.get(TaskDB, replay_task_id) is None
            assert (
                session.exec(
                    select(VoiceConfigurationDeltaDB).where(
                        VoiceConfigurationDeltaDB.scope_id == replay_configuration_session
                    )
                ).first()
                is None
            )


def test_privacy_delete_requires_confirmation_and_retries_runtime_cleanup(
    client,
    user_auth_header,
) -> None:
    profile_id = "privacy-runtime-retry"
    principal = VoicePrincipal(tenant_id=TENANT_ID, subject=SUBJECT)
    stream = get_voice_stream_session_service().create(
        principal,
        runtime_session_id="runtime-privacy-retry",
        deadline_seconds=60,
        profile_id=profile_id,
    )
    headers = _headers(user_auth_header, "privacy-runtime-retry-delete")

    unconfirmed = client.delete(
        f"/v1/voice/privacy/{profile_id}",
        headers=_headers(user_auth_header, "privacy-unconfirmed"),
        json={"confirmed": False},
    )
    assert unconfirmed.status_code == 403
    assert get_voice_stream_session_service().require(principal, stream.session_id) is stream

    with patch("agent.services.voice_provider.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.delete_stream.side_effect = RuntimeError("runtime unavailable")
        first = client.delete(
            f"/v1/voice/privacy/{profile_id}",
            headers=headers,
            json={"confirmed": True},
        )
        assert first.status_code == 200
        first_result = first.get_json()["data"]["deletion"]
        assert first_result["runtime_cleanup_pending"] is True
        assert first_result["runtime_cleanup_failed_count"] == 1
        with Session(engine) as session:
            pending_cleanup = session.exec(select(VoiceRuntimeCleanupDB)).all()
        assert pending_cleanup
        serialized_cleanup = str([item.model_dump() for item in pending_cleanup])
        assert TENANT_ID not in serialized_cleanup
        assert SUBJECT not in serialized_cleanup
        assert profile_id not in serialized_cleanup
        assert all(item.tenant_id.startswith("voice-cleanup-") for item in pending_cleanup)
        assert all(item.owner_subject == "hub-privacy-cleanup" for item in pending_cleanup)

        provider_factory.return_value.delete_stream.side_effect = None
        replay = client.delete(
            f"/v1/voice/privacy/{profile_id}",
            headers=headers,
            json={"confirmed": True},
        )
        replay_result = replay.get_json()["data"]["deletion"]
        assert replay_result["idempotent_replay"] is True
        assert replay_result["runtime_cleanup_pending"] is False
        assert replay_result["runtime_cleanup_failed_count"] == 0
        provider_factory.return_value.delete_stream.assert_called_with(
            runtime_session_id="runtime-privacy-retry",
            request_id=f"privacy-delete-{stream.session_id}",
        )


def test_profile_delete_does_not_follow_foreign_or_unscoped_task_references(
    client,
    user_auth_header,
) -> None:
    principal = VoicePrincipal(tenant_id=TENANT_ID, subject=SUBJECT)
    artifact = get_voice_result_artifact_service().create(
        principal,
        request_hash="privacy-isolation-result",
        profile_id=PROFILE_ID,
        result={
            "text": "isolated result",
            "candidates": [{"candidate_id": "privacy-isolation-candidate"}],
        },
    )
    target_task_id = "voice-privacy-isolation-target"
    scoped_child_id = "voice-privacy-isolation-scoped-child"
    scoped_result_id = "voice-privacy-isolation-scoped-result"
    foreign_child_id = "voice-privacy-isolation-foreign-child"
    foreign_source_id = "voice-privacy-isolation-foreign-source"
    foreign_result_id = "voice-privacy-isolation-foreign-result"
    foreign_explicit_id = "voice-privacy-isolation-foreign-explicit"
    other_profile_child_id = "voice-privacy-isolation-other-profile-child"
    unscoped_child_id = "voice-privacy-isolation-unscoped-child"

    _save_voice_task(target_task_id, profile_id=PROFILE_ID)
    _save_voice_task(
        scoped_child_id,
        profile_id=None,
        parent_task_id=target_task_id,
        task_kind="restricted_inference",
    )
    _save_voice_task(
        scoped_result_id,
        profile_id=None,
        task_kind="voice_generative_judge",
        last_output=artifact["id"],
    )
    _save_voice_task(
        foreign_child_id,
        profile_id=None,
        parent_task_id=target_task_id,
        task_kind="restricted_inference",
        scope_tenant_id="foreign-tenant",
        scope_subject="foreign-user",
    )
    _save_voice_task(
        foreign_source_id,
        profile_id=None,
        source_task_id=target_task_id,
        task_kind="voice_generative_judge",
        scope_tenant_id="foreign-tenant",
        scope_subject="foreign-user",
    )
    _save_voice_task(
        foreign_result_id,
        profile_id=None,
        task_kind="voice_transcription",
        scope_tenant_id="foreign-tenant",
        scope_subject="foreign-user",
        verification_status={"voice_transcription": {"result_ref": artifact["id"]}},
    )
    _save_voice_task(
        foreign_explicit_id,
        profile_id=PROFILE_ID,
        scope_tenant_id="foreign-tenant",
        scope_subject="foreign-user",
    )
    _save_voice_task(
        other_profile_child_id,
        profile_id=OTHER_PROFILE_ID,
        parent_task_id=target_task_id,
        task_kind="restricted_inference",
    )
    _save_voice_task(
        unscoped_child_id,
        profile_id=PROFILE_ID,
        parent_task_id=target_task_id,
        task_kind="restricted_inference",
        scoped=False,
    )
    get_voice_stream_session_service().create(
        principal,
        runtime_session_id="runtime-privacy-isolation-explicit",
        deadline_seconds=60,
        profile_id=PROFILE_ID,
        task_id=foreign_explicit_id,
    )

    with patch("agent.services.voice_provider.get_voice_provider_service"):
        deleted = client.delete(
            f"/v1/voice/privacy/{PROFILE_ID}",
            headers=_headers(user_auth_header, "privacy-isolation-delete"),
            json={"confirmed": True},
        )

    assert deleted.status_code == 200
    with Session(engine) as session:
        assert session.get(TaskDB, target_task_id) is None
        assert session.get(TaskDB, scoped_child_id) is None
        assert session.get(TaskDB, scoped_result_id) is None
        for surviving_task_id in (
            foreign_child_id,
            foreign_source_id,
            foreign_result_id,
            foreign_explicit_id,
            other_profile_child_id,
            unscoped_child_id,
        ):
            assert session.get(TaskDB, surviving_task_id) is not None
