from __future__ import annotations

import hashlib
import json
import time
import uuid
from io import BytesIO
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    TaskDB,
    VoiceGovernanceIdempotencyDB,
    VoiceResultArtifactDB,
    VoiceRuntimeCleanupDB,
)
from agent.repositories.voice_result_artifact import VoiceResultArtifactRepository
from agent.services.voice_admission_service import VoiceAdmissionLimits, get_voice_admission_service
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_runtime_cleanup_service import get_voice_runtime_cleanup_service
from agent.services.voice_stream_session_service import (
    VoiceStreamSessionService,
    get_voice_stream_session_service,
)


def _transcription_result():
    return {
        "schema_version": "2.0",
        "provider": "voice-runtime",
        "model": "vosk-de",
        "text": "Hallo Welt",
        "language": "de",
        "candidates": [
            {
                "candidate_id": "candidate-vosk-1",
                "backend": "vosk",
                "status": "succeeded",
                "text": "Hallo Welt",
            }
        ],
        "selected_candidate_id": "candidate-vosk-1",
        "provenance_valid": True,
    }


def _created_runtime_stream(**kwargs):
    return {
        "session_id": kwargs["requested_session_id"],
        "state": "created",
        "max_audio_seconds": kwargs.get("max_audio_seconds"),
    }


def test_batch_transcription_idempotency_replays_encrypted_result(client, admin_auth_header):
    headers = {**admin_auth_header, "Idempotency-Key": "voice-batch-result-1"}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _transcription_result()
        first = client.post(
            "/v1/voice/transcribe",
            headers=headers,
            data={"file": (BytesIO(b"audio"), "sample.webm"), "profile_id": "default"},
            content_type="multipart/form-data",
        )
        replay = client.post(
            "/v1/voice/transcribe",
            headers=headers,
            data={"file": (BytesIO(b"audio"), "sample.webm"), "profile_id": "default"},
            content_type="multipart/form-data",
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    first_data = first.get_json()["data"]
    replay_data = replay.get_json()["data"]
    assert replay_data["idempotent_replay"] is True
    assert replay_data["result_ref"] == first_data["result_ref"]
    assert replay_data["task_id"] == first_data["task_id"]
    assert provider_factory.return_value.transcribe.call_count == 1
    with Session(engine) as session:
        artifacts = session.exec(select(VoiceResultArtifactDB)).all()
        result_artifacts = [
            item
            for item in artifacts
            if item.id == first_data["result_ref"] or item.parent_artifact_id == first_data["result_ref"]
        ]
        assert {item.artifact_kind for item in result_artifacts} == {
            "raw_candidates",
            "fusion_result",
            "result_envelope",
        }
        assert all("Hallo Welt" not in item.payload_ciphertext for item in result_artifacts)
        assert all(item.candidate_ids == ["candidate-vosk-1"] for item in result_artifacts)
        envelope = next(item for item in result_artifacts if item.artifact_kind == "result_envelope")
        assert all(
            item.parent_artifact_id == envelope.id
            for item in result_artifacts
            if item.artifact_kind in {"raw_candidates", "fusion_result"}
        )
    with client.application.app_context():
        from agent.repository import task_repo

        task = task_repo.get_by_id(first_data["task_id"])
    assert task is not None
    assert task.status == "completed"
    assert task.task_kind == "voice_transcription"
    serialized_task = json.dumps(task.model_dump(), default=str)
    assert "Hallo Welt" not in serialized_task
    assert "audio" not in str(task.worker_execution_context).lower()


def test_transcription_recovers_artifact_after_crash_before_task_completion(
    client,
    admin_auth_header,
):
    idempotency_key = "voice-transcribe-artifact-crash"
    headers = {**admin_auth_header, "Idempotency-Key": idempotency_key}
    audio = b"opaque recovery audio"
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _transcription_result()
        with patch(
            "agent.services.voice_delegation_task_service.VoiceDelegationTaskService.complete",
            side_effect=SystemExit("simulated post-artifact crash"),
        ):
            with pytest.raises(SystemExit, match="post-artifact crash"):
                client.post(
                    "/v1/voice/transcribe",
                    headers=headers,
                    data={"file": (BytesIO(audio), "recovery.webm"), "profile_id": "default"},
                    content_type="multipart/form-data",
                )

        with Session(engine) as session:
            claim = session.exec(
                select(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.operation == "voice.transcribe"
                )
            ).one()
            envelope = session.exec(
                select(VoiceResultArtifactDB).where(
                    VoiceResultArtifactDB.artifact_kind == "result_envelope"
                )
            ).one()
            task = session.exec(select(TaskDB)).one()
            assert claim.state == "pending"
            assert task.status == "in_progress"
            assert envelope.request_hash.startswith("voice-request-")
            assert envelope.request_hash != hashlib.sha256(audio).hexdigest()
            claim_id = claim.id
            envelope_id = envelope.id
            task_id = task.id
            claim.lease_expires_at = time.time() - 1
            session.add(claim)
            session.commit()

        replay = client.post(
            "/v1/voice/transcribe",
            headers=headers,
            data={"file": (BytesIO(audio), "recovery.webm"), "profile_id": "default"},
            content_type="multipart/form-data",
        )

    assert replay.status_code == 200
    replay_data = replay.get_json()["data"]
    assert replay_data["idempotent_replay"] is True
    assert replay_data["result_ref"] == envelope_id
    assert replay_data["task_id"] == task_id
    assert provider_factory.return_value.transcribe.call_count == 1
    with Session(engine) as session:
        recovered_claim = session.get(VoiceGovernanceIdempotencyDB, claim_id)
        recovered_task = session.get(TaskDB, task_id)
    assert recovered_claim is not None
    assert recovered_claim.state == "completed"
    assert recovered_claim.result_metadata == {
        "result_ref": envelope_id,
        "task_id": task_id,
    }
    assert recovered_task is not None
    assert recovered_task.status == "completed"
    assert recovered_task.last_output == envelope_id


def test_batch_without_fingerprint_consent_persists_only_opaque_audio_lineage(
    client,
    admin_auth_header,
):
    audio = b"private-audio-fixture-that-must-not-be-fingerprinted"
    raw_digest = hashlib.sha256(audio).hexdigest()
    profile_id = f"no-fingerprint-{uuid.uuid4().hex}"
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _transcription_result()
        response = client.post(
            "/v1/voice/transcribe",
            headers=admin_auth_header,
            data={"file": (BytesIO(audio), "private.webm"), "profile_id": profile_id},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    with Session(engine) as session:
        artifacts = session.exec(
            select(VoiceResultArtifactDB).where(
                (VoiceResultArtifactDB.id == payload["result_ref"])
                | (VoiceResultArtifactDB.parent_artifact_id == payload["result_ref"])
            )
        ).all()
        task = session.get(TaskDB, payload["task_id"])

    assert task is not None
    persisted = json.dumps(
        {
            "task": task.model_dump(),
            "artifacts": [artifact.model_dump() for artifact in artifacts],
        },
        sort_keys=True,
        default=str,
    )
    assert raw_digest not in persisted
    request_refs = {artifact.request_hash for artifact in artifacts}
    assert len(request_refs) == 1
    assert next(iter(request_refs)).startswith("voice-request-")
    task_context = task.worker_execution_context["voice_transcription"]
    assert task_context["request_digest"].startswith("voice-request-")


def test_batch_idempotency_is_bound_to_effective_profile_configuration(client, admin_auth_header):
    profile_id = "idempotency-config-profile"
    first_configuration = client.put(
        "/v1/voice/configuration",
        headers={**admin_auth_header, "Idempotency-Key": "voice-idempotency-config-v1"},
        json={
            "scope": "profile",
            "scope_id": profile_id,
            "delta": {"confidence_threshold": 0.8},
        },
    )
    assert first_configuration.status_code == 200
    headers = {**admin_auth_header, "Idempotency-Key": "voice-config-bound-request"}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _transcription_result()
        first = client.post(
            "/v1/voice/transcribe",
            headers=headers,
            data={"file": (BytesIO(b"audio"), "sample.webm"), "profile_id": profile_id},
            content_type="multipart/form-data",
        )
        second_configuration = client.put(
            "/v1/voice/configuration",
            headers={**admin_auth_header, "Idempotency-Key": "voice-idempotency-config-v2"},
            json={
                "scope": "profile",
                "scope_id": profile_id,
                "delta": {"confidence_threshold": 0.9},
                "expected_version": 1,
            },
        )
        conflict = client.post(
            "/v1/voice/transcribe",
            headers=headers,
            data={"file": (BytesIO(b"audio"), "sample.webm"), "profile_id": profile_id},
            content_type="multipart/form-data",
        )

    assert first.status_code == 200
    assert second_configuration.status_code == 200
    assert conflict.status_code == 409
    assert conflict.get_json()["data"]["error"]["code"] == "voice_governance.idempotency_conflict"
    assert provider_factory.return_value.transcribe.call_count == 1


def test_batch_idempotency_rejects_different_same_length_audio(client, admin_auth_header):
    headers = {**admin_auth_header, "Idempotency-Key": "voice-audio-binding"}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _transcription_result()
        first = client.post(
            "/v1/voice/transcribe",
            headers=headers,
            data={"file": (BytesIO(b"audio-A"), "same.webm")},
            content_type="multipart/form-data",
        )
        conflict = client.post(
            "/v1/voice/transcribe",
            headers=headers,
            data={"file": (BytesIO(b"audio-B"), "same.webm")},
            content_type="multipart/form-data",
        )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.get_json()["data"]["error"]["code"] == "voice_governance.idempotency_conflict"
    assert provider_factory.return_value.transcribe.call_count == 1


def test_hub_streaming_is_principal_bound_and_finalizes_to_result_artifact(client, admin_auth_header):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider = provider_factory.return_value
        provider.create_stream.side_effect = _created_runtime_stream
        provider.push_stream_chunk.return_value = {"event": {"event_type": "partial", "payload": {"text": "Hallo"}}}
        provider.finalize_stream.return_value = {
            "event": {"event_type": "final", "payload": {"result": _transcription_result()}}
        }
        provider.delete_stream.return_value = {"deleted": True}

        created = client.post(
            "/v1/voice/streams",
            headers={**admin_auth_header, "Idempotency-Key": "voice-stream-create-1"},
            json={"filename": "stream.pcm", "media_type": "audio/pcm;rate=16000;channels=1"},
        )
        created_stream = created.get_json()["data"]["stream"]
        session_id = created_stream["session_id"]
        task_id = created_stream["task_id"]
        with Session(engine) as db:
            provisional_cleanup = db.exec(
                select(VoiceRuntimeCleanupDB).where(
                    VoiceRuntimeCleanupDB.source_session_id == session_id
                )
            ).one()
        assert provisional_cleanup.state == "provisional"
        with client.application.app_context():
            from agent.repository import task_repo

            task = task_repo.get_by_id(task_id)
        assert task is not None
        assert task.status == "in_progress"
        chunk = client.put(
            f"/v1/voice/streams/{session_id}/chunks/0",
            headers=admin_auth_header,
            data=b"\x00\x00" * 100,
        )
        provider.push_stream_chunk.return_value = {
            "event": {"event_type": "chunk_replayed", "payload": {"chunk_sequence": 0}}
        }
        replayed_chunk = client.put(
            f"/v1/voice/streams/{session_id}/chunks/0",
            headers=admin_auth_header,
            data=b"\x00\x00" * 100,
        )
        final = client.post(f"/v1/voice/streams/{session_id}/finalize", headers=admin_auth_header)
        deleted = client.delete(f"/v1/voice/streams/{session_id}", headers=admin_auth_header)

    assert created.status_code == 201
    assert chunk.status_code == 202
    assert replayed_chunk.status_code == 202
    assert replayed_chunk.get_json()["data"]["stream"]["next_chunk_sequence"] == 1
    assert replayed_chunk.get_json()["data"]["event"]["event_type"] == "chunk_replayed"
    assert provider.push_stream_chunk.call_count == 1
    final_data = final.get_json()["data"]
    assert final_data["result"]["text"] == "Hallo Welt"
    assert final_data["result_ref"].startswith("voice-result-")
    assert deleted.get_json()["data"]["deleted"] is True
    with Session(engine) as db:
        assert (
            db.exec(
                select(VoiceRuntimeCleanupDB).where(
                    VoiceRuntimeCleanupDB.source_session_id == session_id
                )
            ).first()
            is None
        )
    with client.application.app_context():
        from agent.repository import task_repo

        task = task_repo.get_by_id(task_id)
    assert task is not None
    assert task.status == "completed"
    assert task.last_output == final_data["result_ref"]


def test_hub_rejects_stream_gap_and_conflicting_replay_before_runtime_dispatch(
    client,
    admin_auth_header,
):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider = provider_factory.return_value
        provider.create_stream.side_effect = _created_runtime_stream
        provider.push_stream_chunk.return_value = {
            "event": {"event_type": "chunk_accepted", "payload": {"chunk_sequence": 0}}
        }
        created = client.post(
            "/v1/voice/streams",
            headers={**admin_auth_header, "Idempotency-Key": "voice-stream-preflight"},
            json={"filename": "stream.pcm", "media_type": "audio/pcm;rate=16000;channels=1"},
        )
        session_id = created.get_json()["data"]["stream"]["session_id"]

        gap = client.put(
            f"/v1/voice/streams/{session_id}/chunks/1",
            headers=admin_auth_header,
            data=b"gap",
        )
        accepted = client.put(
            f"/v1/voice/streams/{session_id}/chunks/0",
            headers=admin_auth_header,
            data=b"accepted",
        )
        conflict = client.put(
            f"/v1/voice/streams/{session_id}/chunks/0",
            headers=admin_auth_header,
            data=b"different",
        )
        provider.delete_stream.return_value = {"deleted": True}
        deleted = client.delete(f"/v1/voice/streams/{session_id}", headers=admin_auth_header)

    assert gap.status_code == 409
    assert gap.get_json()["data"]["error"]["code"] == "voice_stream.sequence_conflict"
    assert accepted.status_code == 202
    assert conflict.status_code == 409
    assert conflict.get_json()["data"]["error"]["code"] == "voice_stream.chunk_conflict"
    assert provider.push_stream_chunk.call_count == 1
    assert deleted.status_code == 200


def test_hub_finalize_reservation_fences_inflight_and_late_chunks() -> None:
    principal = VoicePrincipal(tenant_id="finalize-fence", subject="finalize-fence")
    sessions = VoiceStreamSessionService()
    stream = sessions.create(
        principal,
        runtime_session_id="runtime-finalize-fence",
        deadline_seconds=60,
    )
    chunk_digest = hashlib.sha256(b"chunk").hexdigest()
    sessions.begin_chunk(
        principal,
        stream.session_id,
        chunk_sequence=0,
        chunk_digest=chunk_digest,
        chunk_size=5,
    )

    with pytest.raises(VoiceGovernanceError) as inflight:
        sessions.begin_finalize(principal, stream.session_id)
    assert inflight.value.code == "voice_stream.backpressure"

    sessions.complete_chunk(
        principal,
        stream.session_id,
        chunk_sequence=0,
        chunk_digest=chunk_digest,
    )
    reservation = sessions.begin_finalize(principal, stream.session_id)
    with pytest.raises(VoiceGovernanceError) as late_chunk:
        sessions.begin_chunk(
            principal,
            stream.session_id,
            chunk_sequence=1,
            chunk_digest=hashlib.sha256(b"late").hexdigest(),
            chunk_size=4,
        )
    assert late_chunk.value.code == "voice_stream.invalid_state"

    finalized = sessions.complete_finalize(
        principal,
        stream.session_id,
        token=reservation.token,
        result_ref="voice-result-finalized",
    )
    assert finalized.state == "final"
    assert finalized.result_ref == "voice-result-finalized"


def test_hub_stream_creation_requires_idempotency_key(client, admin_auth_header):
    response = client.post(
        "/v1/voice/streams",
        headers=admin_auth_header,
        json={"filename": "stream.pcm", "media_type": "audio/pcm;rate=16000;channels=1"},
    )

    assert response.status_code == 400
    assert response.get_json()["data"]["error"]["code"] == "voice_stream.idempotency_required"


def test_hub_stream_forwards_and_enforces_pcm_audio_budget(client, admin_auth_header):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider = provider_factory.return_value
        provider.create_stream.side_effect = _created_runtime_stream
        provider.delete_stream.return_value = {"deleted": True}
        created = client.post(
            "/v1/voice/streams",
            headers={**admin_auth_header, "Idempotency-Key": "voice-stream-pcm-budget"},
            json={
                "filename": "budget.pcm",
                "media_type": "audio/pcm;rate=16000;channels=1",
                "max_audio_seconds": 0.001,
            },
        )
        stream = created.get_json()["data"]["stream"]
        exceeded = client.put(
            f"/v1/voice/streams/{stream['session_id']}/chunks/0",
            headers=admin_auth_header,
            data=b"x" * 33,
        )
        deleted = client.delete(
            f"/v1/voice/streams/{stream['session_id']}",
            headers=admin_auth_header,
        )

    assert created.status_code == 201
    assert stream["max_audio_seconds"] == pytest.approx(0.001)
    assert stream["max_audio_bytes"] == 32
    assert provider.create_stream.call_args.kwargs["max_audio_seconds"] == pytest.approx(0.001)
    assert exceeded.status_code == 413
    assert exceeded.get_json()["data"]["error"]["code"] == "voice_stream.audio_budget_exceeded"
    provider.push_stream_chunk.assert_not_called()
    assert deleted.status_code == 200


def test_hub_stream_reserves_configured_maximum_for_opaque_container(client, admin_auth_header):
    limits = VoiceAdmissionLimits(
        max_concurrent_requests=2,
        max_queue_depth=2,
        max_inflight_audio_seconds=60,
        max_audio_seconds_per_request=30,
    )
    admission_service = get_voice_admission_service()
    with (
        patch("agent.routes.voice._voice_admission_limits", return_value=limits),
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
        patch.object(admission_service, "acquire", wraps=admission_service.acquire) as acquire,
    ):
        provider = provider_factory.return_value
        provider.create_stream.side_effect = _created_runtime_stream
        provider.delete_stream.return_value = {"deleted": True}
        created = client.post(
            "/v1/voice/streams",
            headers={**admin_auth_header, "Idempotency-Key": "voice-stream-webm-budget"},
            json={"filename": "budget.webm", "media_type": "audio/webm", "max_audio_seconds": 0.001},
        )
        stream = created.get_json()["data"]["stream"]
        deleted = client.delete(
            f"/v1/voice/streams/{stream['session_id']}",
            headers=admin_auth_header,
        )

    assert created.status_code == 201
    assert acquire.call_args.kwargs["audio_seconds"] == 30
    assert stream["max_audio_seconds"] == pytest.approx(0.001)
    assert stream["max_audio_bytes"] == 25 * 1024 * 1024
    assert provider.create_stream.call_args.kwargs["max_audio_seconds"] == pytest.approx(0.001)
    assert deleted.status_code == 200


def test_hub_stream_creation_failure_durably_deletes_runtime_orphan(client, admin_auth_header):
    cleanup = get_voice_runtime_cleanup_service()
    session_service = get_voice_stream_session_service()
    with (
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
        patch.object(cleanup, "_runtime_stream_delete") as runtime_delete,
        patch.object(
            session_service,
            "create",
            side_effect=VoiceGovernanceError(
                code="voice_stream.capacity_exhausted",
                message="Hub voice stream capacity exhausted",
                status_code=429,
            ),
        ),
    ):
        provider_factory.return_value.create_stream.side_effect = _created_runtime_stream
        response = client.post(
            "/v1/voice/streams",
            headers={**admin_auth_header, "Idempotency-Key": "voice-stream-orphan"},
            json={"filename": "orphan.pcm", "media_type": "audio/pcm;rate=16000;channels=1"},
        )

    assert response.status_code == 429
    runtime_delete.assert_called_once()
    assert runtime_delete.call_args.args[0] == (
        provider_factory.return_value.create_stream.call_args.kwargs["requested_session_id"]
    )
    assert get_voice_runtime_cleanup_service().retry_all_pending() == 0


def test_expired_hub_stream_deletes_runtime_and_terminalizes_task(client, admin_auth_header):
    cleanup = get_voice_runtime_cleanup_service()
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.create_stream.side_effect = _created_runtime_stream
        created = client.post(
            "/v1/voice/streams",
            headers={**admin_auth_header, "Idempotency-Key": "voice-stream-expired"},
            json={"filename": "expired.pcm", "media_type": "audio/pcm;rate=16000;channels=1"},
        )
    stream = created.get_json()["data"]["stream"]
    session = get_voice_stream_session_service().require(principal, stream["session_id"])
    session.deadline_at = time.time() - 1

    with patch.object(cleanup, "_runtime_stream_delete") as runtime_delete:
        expired = client.get(f"/v1/voice/streams/{stream['session_id']}", headers=admin_auth_header)
        missing = client.get(f"/v1/voice/streams/{stream['session_id']}", headers=admin_auth_header)

    assert expired.status_code == 504
    assert expired.get_json()["data"]["error"]["code"] == "voice_stream.deadline_exceeded"
    assert missing.status_code == 404
    runtime_delete.assert_called_once()
    assert runtime_delete.call_args.args[0] == (
        provider_factory.return_value.create_stream.call_args.kwargs["requested_session_id"]
    )
    with Session(engine) as db:
        task = db.get(TaskDB, stream["task_id"])
    assert task is not None
    assert task.status == "cancelled"
    assert task.status_reason_code == "voice_stream_deadline_exceeded"


def test_client_task_header_never_creates_batch_or_stream_parent_link(client, admin_auth_header):
    from agent.repository import task_repo

    foreign_task_id = "foreign-client-selected-parent"
    task_repo.save(
        TaskDB(
            id=foreign_task_id,
            title="Foreign task",
            status="in_progress",
            task_kind="voice_transcription",
            worker_execution_context={
                "voice_transcription": {
                    "profile_id": "default",
                    "tenant_scope_hash": hashlib.sha256(b"foreign-tenant").hexdigest(),
                    "owner_subject_hash": hashlib.sha256(b"foreign-owner").hexdigest(),
                }
            },
        )
    )
    parent_header = {**admin_auth_header, "X-Task-ID": foreign_task_id}
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _transcription_result()
        provider_factory.return_value.create_stream.side_effect = _created_runtime_stream
        provider_factory.return_value.delete_stream.return_value = {"deleted": True}
        batch = client.post(
            "/v1/voice/transcribe",
            headers=parent_header,
            data={"file": (BytesIO(b"audio"), "sample.webm")},
            content_type="multipart/form-data",
        )
        stream = client.post(
            "/v1/voice/streams",
            headers={**parent_header, "Idempotency-Key": "foreign-parent-stream"},
            json={"filename": "stream.pcm", "media_type": "audio/pcm;rate=16000;channels=1"},
        )
        stream_data = stream.get_json()["data"]["stream"]
        deleted = client.delete(
            f"/v1/voice/streams/{stream_data['session_id']}",
            headers=admin_auth_header,
        )

    assert batch.status_code == 200
    assert stream.status_code == 201
    assert deleted.status_code == 200
    with Session(engine) as session:
        batch_task = session.get(TaskDB, batch.get_json()["data"]["task_id"])
        stream_task = session.get(TaskDB, stream_data["task_id"])
        foreign_task = session.get(TaskDB, foreign_task_id)
    assert batch_task is not None
    assert batch_task.parent_task_id is None
    assert stream_task is not None
    assert stream_task.parent_task_id is None
    assert foreign_task is not None


def test_result_artifact_bundle_rolls_back_atomically_on_constraint_failure():
    repository = VoiceResultArtifactRepository()
    principal = VoicePrincipal(tenant_id="artifact-atomic-tenant", subject="artifact-atomic-user")
    duplicate_id = f"voice-result-{uuid.uuid4()}"
    request_hash = uuid.uuid4().hex
    common = {
        "id": duplicate_id,
        "request_hash": request_hash,
        "profile_id": "default",
        "parent_artifact_id": None,
        "payload_ciphertext": "enc:v1:test",
        "payload_digest": "0" * 64,
        "candidate_ids": [],
        "expires_at": 9_999_999_999.0,
    }

    with pytest.raises(IntegrityError):
        repository.create_many(
            principal,
            artifacts=[
                {**common, "artifact_kind": "raw_candidates"},
                {**common, "artifact_kind": "fusion_result"},
            ],
        )

    with Session(engine) as session:
        rows = session.exec(
            select(VoiceResultArtifactDB).where(VoiceResultArtifactDB.request_hash == request_hash)
        ).all()
    assert rows == []


def test_hub_stream_forwards_effective_profile_configuration(client, admin_auth_header):
    profile_id = "stream-policy-profile"
    configured = client.put(
        "/v1/voice/configuration",
        headers={**admin_auth_header, "Idempotency-Key": "stream-policy-config-1"},
        json={
            "scope": "profile",
            "scope_id": profile_id,
            "delta": {
                "recognition_strategy": "parallel_compare",
                "feature_flags": {"voice_fusion": True},
            },
        },
    )
    assert configured.status_code == 200

    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.create_stream.side_effect = _created_runtime_stream
        created = client.post(
            "/v1/voice/streams",
            headers={**admin_auth_header, "Idempotency-Key": "stream-policy-create-1"},
            json={
                "filename": "stream.pcm",
                "media_type": "audio/pcm;rate=16000;channels=1",
                "profile_id": profile_id,
            },
        )

    assert created.status_code == 201
    context = provider_factory.return_value.create_stream.call_args.kwargs["recognition_context"]
    assert context["configuration"]["recognition_strategy"] == "parallel_compare"
    assert context["configuration"]["feature_flags"]["voice_fusion"] is True
