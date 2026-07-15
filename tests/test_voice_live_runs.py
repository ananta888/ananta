from __future__ import annotations

import json
import threading
import time
import uuid
import wave
from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    TaskDB,
    VoiceGovernanceIdempotencyDB,
    VoiceLiveRunDB,
    VoiceLiveRunSegmentDB,
    VoiceResultArtifactDB,
)
from agent.repositories.voice_deletion_tombstone import VoiceDeletionTombstoneRepository
from agent.repositories.voice_live_runs import (
    VoiceLiveRunRepository,
    VoiceLiveRunRepositoryConflict,
)
from agent.services.voice_delegation_task_service import get_voice_delegation_task_service
from agent.services.voice_generative_corrector_service import (
    VoiceGenerativeCorrectorOutcome,
    VoiceGenerativeCorrectorTaskTracker,
)
from agent.services.voice_generative_judge_service import VoiceGenerativeJudgeTaskTracker
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_live_run_correction_service import (
    get_voice_live_run_correction_service,
)
from agent.services.voice_live_run_maintenance_service import VoiceLiveRunMaintenanceService
from agent.services.voice_live_run_service import VoiceLiveRunError, VoiceLiveRunService
from agent.services.voice_live_run_start_lease_service import (
    VoiceLiveRunStartLeaseError,
    VoiceLiveRunStartLeaseService,
    get_voice_live_run_start_lease_service,
)
from agent.services.voice_live_run_task_port import VoiceLiveRunTaskPort
from agent.services.voice_privacy_service import get_voice_privacy_service
from agent.services.voice_provider import VoiceProviderError
from agent.services.voice_result_artifact_service import get_voice_result_artifact_service


def _wav(duration_ms: int = 1_000, sample: int = 0) -> bytes:
    frame_count = round(16_000 * duration_ms / 1_000)
    sample_bytes = int(sample).to_bytes(2, byteorder="little", signed=True)
    target = BytesIO()
    with wave.open(target, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(16_000)
        sink.writeframes(sample_bytes * frame_count)
    return target.getvalue()


def _result(text: str) -> dict:
    return {
        "schema_version": "2.0",
        "provider": "voice-runtime",
        "model": "test-asr",
        "text": text,
        "language": "de",
        "candidates": [],
        "provenance_valid": True,
    }


def _lease_token(principal: VoicePrincipal, profile_id: str) -> str:
    return (
        get_voice_live_run_start_lease_service()
        .issue(
            principal,
            profile_id,
        )
        .lease_token
    )


def _create_run(
    client,
    headers: dict[str, str],
    *,
    key: str | None = None,
    profile_id: str = "live-profile",
    segment_seconds: int = 60,
    max_seconds: int = 120,
    overlap_ms: int = 1_000,
    lease_token: str | None = None,
):
    if lease_token is None:
        lease_response = client.post(
            "/v1/voice/live-runs/lease",
            headers=headers,
            json={"profile_id": profile_id},
        )
        assert lease_response.status_code == 200
        lease_token = lease_response.get_json()["data"]["lease_token"]
    return client.post(
        "/v1/voice/live-runs",
        headers={**headers, "Idempotency-Key": key or f"live-create-{uuid.uuid4().hex}"},
        json={
            "source": "system_audio",
            "profile_id": profile_id,
            "lease_token": lease_token,
            "language": "de",
            "segment_duration_seconds": segment_seconds,
            "max_duration_seconds": max_seconds,
            "overlap_milliseconds": overlap_ms,
        },
    )


def _put_audio(
    client,
    headers: dict[str, str],
    run_id: str,
    sequence: int,
    *,
    key: str,
    audio: bytes,
    started_at_ms: int,
    ended_at_ms: int,
    overlap_ms: int = 0,
):
    return client.put(
        f"/v1/voice/live-runs/{run_id}/segments/{sequence}",
        headers={**headers, "Idempotency-Key": key},
        data={
            "file": (BytesIO(audio), f"segment-{sequence}.wav"),
            "started_at_ms": str(started_at_ms),
            "ended_at_ms": str(ended_at_ms),
            "duration_ms": str(ended_at_ms - started_at_ms),
            "overlap_milliseconds": str(overlap_ms),
        },
        content_type="multipart/form-data",
    )


def _configure_live_corrector(client, headers: dict[str, str], profile_id: str):
    return client.put(
        "/v1/voice/configuration",
        headers={
            **headers,
            "Idempotency-Key": f"live-corrector-{profile_id}-{uuid.uuid4().hex}",
        },
        json={
            "scope": "profile",
            "scope_id": profile_id,
            "delta": {
                "correction_policy": "generative_rewrite",
                "generative_corrector_provider": "embedded",
                "generative_corrector_model": "phi-3-mini-instruct",
                "generative_corrector_max_edit_ratio": 0.35,
                "feature_flags": {"generative_corrector": True},
            },
        },
    )


def test_live_run_create_is_durable_idempotent_and_bounded_to_eight_hours(
    client,
    admin_auth_header,
):
    key = "eight-hour-create"
    first = _create_run(
        client,
        admin_auth_header,
        key=key,
        max_seconds=28_800,
    )
    replay = _create_run(
        client,
        admin_auth_header,
        key=key,
        max_seconds=28_800,
    )
    conflict = _create_run(
        client,
        admin_auth_header,
        key=key,
        max_seconds=3_600,
    )
    too_long = _create_run(
        client,
        admin_auth_header,
        key="too-long-live-run",
        max_seconds=28_801,
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert too_long.status_code == 422
    first_data = first.get_json()["data"]
    assert replay.get_json()["data"]["run"]["id"] == first_data["run"]["id"]
    assert replay.get_json()["data"]["idempotent_replay"] is True
    assert first_data["run"]["max_duration_seconds"] == 28_800
    assert first_data["run"]["expires_at"] > first_data["run"]["capture_deadline_at"]

    restarted = VoiceLiveRunService(repository=VoiceLiveRunRepository())
    durable = restarted.snapshot(
        VoicePrincipal(tenant_id="admin", subject="admin"),
        first_data["run"]["id"],
    )
    assert durable["run"]["status"] == "active"
    with Session(engine) as session:
        parent = session.get(TaskDB, first_data["run"]["parent_task_id"])
    assert parent is not None
    assert parent.status == "in_progress"
    assert parent.task_kind == "voice_live_run"
    assert parent.worker_execution_context["voice_live_run"]["persistence_owner"] == "hub"


def test_start_lease_contract_is_required_and_reusable_for_idempotent_create(
    client,
    admin_auth_header,
):
    profile_id = "lease-contract-profile"
    lease_response = client.post(
        "/v1/voice/live-runs/lease",
        headers=admin_auth_header,
        json={"profile_id": profile_id},
    )

    assert lease_response.status_code == 200
    lease_data = lease_response.get_json()["data"]
    assert set(lease_data) == {"lease_token", "expires_at", "profile_id"}
    assert lease_data["profile_id"] == profile_id
    assert lease_data["lease_token"]
    assert lease_data["expires_at"] > time.time()

    missing = client.post(
        "/v1/voice/live-runs",
        headers={**admin_auth_header, "Idempotency-Key": "missing-start-lease"},
        json={
            "source": "microphone",
            "profile_id": profile_id,
            "segment_duration_seconds": 60,
            "max_duration_seconds": 60,
            "overlap_milliseconds": 0,
        },
    )
    first = _create_run(
        client,
        admin_auth_header,
        profile_id=profile_id,
        key="lease-contract-create",
        lease_token=lease_data["lease_token"],
    )
    replay = _create_run(
        client,
        admin_auth_header,
        profile_id=profile_id,
        key="lease-contract-create",
        lease_token=lease_data["lease_token"],
    )

    assert missing.status_code == 422
    assert missing.get_json()["data"]["error"]["code"] == ("voice_live_run.start_lease_required")
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.get_json()["data"]["idempotent_replay"] is True


def test_start_lease_rejects_wrong_scope_profile_expiry_and_tampering():
    clock = [1_000.0]
    principal = VoicePrincipal(tenant_id="lease-tenant", subject="lease-owner")
    other = VoicePrincipal(tenant_id="lease-tenant", subject="other-owner")
    leases = VoiceLiveRunStartLeaseService(
        clock=lambda: clock[0],
        signing_secret="lease-test-signing-key-at-least-32-bytes",
    )
    issued = leases.issue(principal, "lease-profile")

    assert leases.verify(principal, "lease-profile", issued.lease_token).generation == (issued.generation)
    assert leases.verify(principal, "lease-profile", issued.lease_token).lease_token == (issued.lease_token)
    for scoped_principal, profile_id in (
        (other, "lease-profile"),
        (principal, "different-profile"),
    ):
        with pytest.raises(VoiceLiveRunStartLeaseError) as scope_error:
            leases.verify(scoped_principal, profile_id, issued.lease_token)
        assert scope_error.value.code == "voice_live_run.start_lease_scope_mismatch"

    header, payload, signature = issued.lease_token.split(".")
    replacement = "A" if not signature.startswith("A") else "B"
    tampered = ".".join((header, payload, replacement + signature[1:]))
    with pytest.raises(VoiceLiveRunStartLeaseError) as tamper_error:
        leases.verify(principal, "lease-profile", tampered)
    assert tamper_error.value.code == "voice_live_run.start_lease_invalid"

    clock[0] = issued.expires_at + 1
    with pytest.raises(VoiceLiveRunStartLeaseError) as expiry_error:
        leases.verify(principal, "lease-profile", issued.lease_token)
    assert expiry_error.value.code == "voice_live_run.start_lease_expired"


def test_profile_delete_invalidates_old_lease_and_new_lease_allows_explicit_restart(
    client,
    admin_auth_header,
):
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    profile_id = "lease-delete-generation"
    old_lease = client.post(
        "/v1/voice/live-runs/lease",
        headers=admin_auth_header,
        json={"profile_id": profile_id},
    ).get_json()["data"]["lease_token"]
    get_voice_privacy_service().delete_profile(
        principal,
        profile_id=profile_id,
        idempotency_key="lease-generation-delete",
    )

    stale = _create_run(
        client,
        admin_auth_header,
        profile_id=profile_id,
        key="stale-lease-create",
        lease_token=old_lease,
    )
    fresh_lease_response = client.post(
        "/v1/voice/live-runs/lease",
        headers=admin_auth_header,
        json={"profile_id": profile_id},
    )
    restarted = _create_run(
        client,
        admin_auth_header,
        profile_id=profile_id,
        key="fresh-post-delete-create",
        lease_token=fresh_lease_response.get_json()["data"]["lease_token"],
    )

    assert stale.status_code == 409
    assert stale.get_json()["data"]["error"]["code"] == ("voice_live_run.start_lease_stale")
    assert fresh_lease_response.status_code == 200
    assert restarted.status_code == 201
    with Session(engine) as session:
        runs = session.exec(select(VoiceLiveRunDB).where(VoiceLiveRunDB.profile_id == profile_id)).all()
    assert [run.id for run in runs] == [restarted.get_json()["data"]["run"]["id"]]


def test_fresh_post_delete_lease_cannot_replay_retained_predelete_run(
    client,
    admin_auth_header,
):
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    profile_id = "lease-retained-predelete-run"
    created = _create_run(
        client,
        admin_auth_header,
        profile_id=profile_id,
        key="retained-predelete-idempotency",
    )
    run = created.get_json()["data"]["run"]
    VoiceDeletionTombstoneRepository().claim(
        principal,
        profile_id,
        idempotency_key="retained-run-tombstone-only",
    )
    fresh_lease = client.post(
        "/v1/voice/live-runs/lease",
        headers=admin_auth_header,
        json={"profile_id": profile_id},
    ).get_json()["data"]["lease_token"]

    replay = _create_run(
        client,
        admin_auth_header,
        profile_id=profile_id,
        key="retained-predelete-idempotency",
        lease_token=fresh_lease,
    )

    assert replay.status_code == 409
    assert replay.get_json()["data"]["error"]["code"] == ("voice_live_run.deleted_during_create")
    with Session(engine) as session:
        assert session.get(VoiceLiveRunDB, run["id"]) is None
        assert session.get(TaskDB, run["parent_task_id"]) is None


def test_terminal_create_replay_does_not_resurrect_missing_parent_task():
    for terminal_status in ("completed", "expired"):
        principal = VoicePrincipal(
            tenant_id=f"terminal-replay-{terminal_status}",
            subject="terminal-owner",
        )
        service = VoiceLiveRunService()
        create_kwargs = {
            "idempotency_key": f"terminal-replay-{terminal_status}",
            "lease_token": _lease_token(principal, "terminal-profile"),
            "source": "microphone",
            "profile_id": "terminal-profile",
            "configuration_session_id": None,
            "language": "de",
            "segment_duration_seconds": 60,
            "max_duration_seconds": 60,
            "overlap_milliseconds": 0,
        }
        created, _ = service.create(principal, **create_kwargs)
        run_id = created["run"]["id"]
        parent_task_id = created["run"]["parent_task_id"]
        if terminal_status == "completed":
            stopped = service.stop(
                principal,
                run_id,
                last_sequence=-1,
                reason="user_stop",
            )
            assert stopped["run"]["status"] == "completed"
        else:
            with Session(engine) as session:
                run = session.get(VoiceLiveRunDB, run_id)
                run.status = "expired"
                session.add(run)
                session.commit()

        with Session(engine) as session:
            parent = session.get(TaskDB, parent_task_id)
            assert parent is not None
            session.delete(parent)
            session.commit()

        replay, replayed = service.create(principal, **create_kwargs)

        assert replayed is True
        assert replay["run"]["status"] == terminal_status
        with Session(engine) as session:
            assert session.get(TaskDB, parent_task_id) is None


def test_out_of_order_segments_report_gap_then_compose_and_replay_without_provider_call(
    client,
    admin_auth_header,
):
    created = _create_run(client, admin_auth_header, overlap_ms=1_000)
    run_id = created.get_json()["data"]["run"]["id"]
    audio_one = _wav(sample=1)
    audio_zero = _wav(sample=2)

    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.side_effect = [
            _result("Hallo Welt"),
            _result("Hallo"),
        ]
        second = _put_audio(
            client,
            admin_auth_header,
            run_id,
            1,
            key="stable-segment-one",
            audio=audio_one,
            started_at_ms=900,
            ended_at_ms=1_900,
            overlap_ms=100,
        )
        replay = _put_audio(
            client,
            admin_auth_header,
            run_id,
            1,
            key="stable-segment-one",
            audio=audio_one,
            started_at_ms=900,
            ended_at_ms=1_900,
            overlap_ms=100,
        )
        changed_audio = _put_audio(
            client,
            admin_auth_header,
            run_id,
            1,
            key="stable-segment-one",
            audio=_wav(sample=3),
            started_at_ms=900,
            ended_at_ms=1_900,
            overlap_ms=100,
        )
        changed_key = _put_audio(
            client,
            admin_auth_header,
            run_id,
            1,
            key="different-segment-key",
            audio=audio_one,
            started_at_ms=900,
            ended_at_ms=1_900,
            overlap_ms=100,
        )
        first = _put_audio(
            client,
            admin_auth_header,
            run_id,
            0,
            key="stable-segment-zero",
            audio=audio_zero,
            started_at_ms=0,
            ended_at_ms=1_000,
            overlap_ms=0,
        )

    assert second.status_code == 200
    assert second.get_json()["data"]["gaps"] == [0]
    assert second.get_json()["data"]["segment"]["status"] == "completed"
    assert replay.status_code == 200
    assert replay.get_json()["data"]["idempotent_replay"] is True
    assert changed_audio.status_code == 409
    assert changed_key.status_code == 409
    assert first.status_code == 200
    assert first.get_json()["data"]["gaps"] == []
    assert first.get_json()["data"]["composed_transcript"] is None
    refreshed = client.get(
        f"/v1/voice/live-runs/{run_id}",
        headers=admin_auth_header,
    )
    assert refreshed.get_json()["data"]["composed_transcript"] == "Hallo Welt"
    assert provider_factory.return_value.transcribe.call_count == 2

    with Session(engine) as session:
        parent = session.get(TaskDB, created.get_json()["data"]["run"]["parent_task_id"])
        children = session.exec(select(TaskDB).where(TaskDB.parent_task_id == parent.id)).all()
    assert parent is not None
    assert len(children) == 2
    assert {child.status for child in children} == {"completed"}
    assert {child.task_kind for child in children} == {"voice_transcription"}


def test_retriable_segment_failure_reuses_binding_and_increments_attempt(
    client,
    admin_auth_header,
):
    created = _create_run(client, admin_auth_header)
    run_id = created.get_json()["data"]["run"]["id"]
    audio = _wav(sample=4)
    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.side_effect = [
            VoiceProviderError("voice.runtime_unavailable", "temporary", 503, True),
            _result("erneut erfolgreich"),
        ]
        failed = _put_audio(
            client,
            admin_auth_header,
            run_id,
            0,
            key="retry-same-segment",
            audio=audio,
            started_at_ms=0,
            ended_at_ms=1_000,
        )
        retried = _put_audio(
            client,
            admin_auth_header,
            run_id,
            0,
            key="retry-same-segment",
            audio=audio,
            started_at_ms=0,
            ended_at_ms=1_000,
        )

    assert failed.status_code == 503
    after_failure = client.get(
        f"/v1/voice/live-runs/{run_id}",
        headers=admin_auth_header,
    ).get_json()["data"]
    # The retry has completed by this point, so inspect the durable attempt count.
    assert retried.status_code == 200
    assert retried.get_json()["data"]["segment"]["status"] == "completed"
    assert retried.get_json()["data"]["segment"]["attempt_count"] == 2
    assert after_failure["segments"][0]["attempt_count"] == 2


def test_live_run_is_tenant_isolated_and_result_ref_must_match_profile(
    client,
    admin_auth_header,
    user_auth_header,
):
    source = _create_run(client, admin_auth_header, profile_id="source-profile")
    source_run = source.get_json()["data"]["run"]["id"]
    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _result("privates Ergebnis")
        uploaded = _put_audio(
            client,
            admin_auth_header,
            source_run,
            0,
            key="private-result",
            audio=_wav(sample=5),
            started_at_ms=0,
            ended_at_ms=1_000,
        )
    result_ref = uploaded.get_json()["data"]["result_ref"]
    target = _create_run(client, admin_auth_header, profile_id="target-profile")
    target_run = target.get_json()["data"]["run"]["id"]
    cross_profile = client.put(
        f"/v1/voice/live-runs/{target_run}/segments/0",
        headers={**admin_auth_header, "Idempotency-Key": "cross-profile-link"},
        json={
            "result_ref": result_ref,
            "started_at_ms": 0,
            "ended_at_ms": 1_000,
            "duration_ms": 1_000,
            "overlap_milliseconds": 0,
        },
    )
    other_owner = client.get(
        f"/v1/voice/live-runs/{source_run}",
        headers=user_auth_header,
    )

    assert cross_profile.status_code == 409
    assert other_owner.status_code == 404


def test_heartbeat_rejects_malformed_gap_payload(client, admin_auth_header):
    created = _create_run(client, admin_auth_header)
    run_id = created.get_json()["data"]["run"]["id"]
    malformed = client.post(
        f"/v1/voice/live-runs/{run_id}/heartbeat",
        headers=admin_auth_header,
        json={"last_local_sequence": 0, "gaps": 7},
    )
    assert malformed.status_code == 422
    assert malformed.get_json()["data"]["error"]["code"] == "voice_live_run.invalid_gaps"


def test_reported_gap_is_healed_when_segment_later_completes(client, admin_auth_header):
    created = _create_run(client, admin_auth_header)
    run_id = created.get_json()["data"]["run"]["id"]
    heartbeat = client.post(
        f"/v1/voice/live-runs/{run_id}/heartbeat",
        headers=admin_auth_header,
        json={"last_local_sequence": 0, "gaps": [0]},
    )
    assert heartbeat.get_json()["data"]["gaps"] == [0]
    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _result("nachgereicht")
        uploaded = _put_audio(
            client,
            admin_auth_header,
            run_id,
            0,
            key="healed-gap",
            audio=_wav(sample=13),
            started_at_ms=0,
            ended_at_ms=1_000,
        )
    assert uploaded.status_code == 200
    assert uploaded.get_json()["data"]["gaps"] == []


def test_stop_waits_for_in_flight_segment_then_finalizes_with_explicit_gap(
    client,
    admin_auth_header,
):
    created = _create_run(client, admin_auth_header)
    run_id = created.get_json()["data"]["run"]["id"]
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    service = get_service = VoiceLiveRunService(repository=VoiceLiveRunRepository())
    claim = get_service.reserve_audio_segment(
        principal,
        run_id,
        sequence=0,
        idempotency_key="pending-segment",
        audio=_wav(sample=6),
        started_at_ms=0,
        ended_at_ms=1_000,
        duration_ms=1_000,
        overlap_milliseconds=0,
    )
    blocked = client.post(
        f"/v1/voice/live-runs/{run_id}/stop",
        headers=admin_auth_header,
        json={"last_sequence": 0, "reason": "user_stop"},
    )
    service.fail_segment(
        principal,
        run_id,
        sequence=0,
        idempotency_key_digest=claim.idempotency_key_digest,
        failure_code="network_gap",
    )
    stopped = client.post(
        f"/v1/voice/live-runs/{run_id}/stop",
        headers=admin_auth_header,
        json={"last_sequence": 0, "reason": "user_stop"},
    )
    replay = client.post(
        f"/v1/voice/live-runs/{run_id}/stop",
        headers=admin_auth_header,
        json={"last_sequence": 0, "reason": "user_stop"},
    )

    assert blocked.status_code == 409
    assert blocked.get_json()["data"]["error"]["retriable"] is True
    assert stopped.status_code == 200
    assert stopped.get_json()["data"]["run"]["status"] == "completed_with_gaps"
    assert stopped.get_json()["data"]["gaps"] == [0]
    assert replay.status_code == 200
    assert replay.get_json()["data"]["run"]["final_result_ref"] == stopped.get_json()["data"]["run"]["final_result_ref"]


def test_stale_processing_lease_becomes_gap_instead_of_blocking_stop(
    client,
    admin_auth_header,
):
    created = _create_run(client, admin_auth_header)
    run_id = created.get_json()["data"]["run"]["id"]
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    service = VoiceLiveRunService(repository=VoiceLiveRunRepository())
    service.reserve_audio_segment(
        principal,
        run_id,
        sequence=0,
        idempotency_key="stale-processing",
        audio=_wav(sample=11),
        started_at_ms=0,
        ended_at_ms=1_000,
        duration_ms=1_000,
        overlap_milliseconds=0,
    )
    with Session(engine) as session:
        segment = session.exec(select(VoiceLiveRunSegmentDB).where(VoiceLiveRunSegmentDB.run_id == run_id)).one()
        segment.updated_at = time.time() - 601
        session.add(segment)
        session.commit()

    stopped = client.post(
        f"/v1/voice/live-runs/{run_id}/stop",
        headers=admin_auth_header,
        json={"last_sequence": 0, "reason": "client_shutdown"},
    )
    assert stopped.status_code == 200
    data = stopped.get_json()["data"]
    assert data["run"]["status"] == "completed_with_gaps"
    assert data["segments"][0]["failure_code"] == "processing_lease_expired"


def test_sqlite_reserve_and_finalize_claims_never_commit_a_lost_segment(
    tmp_path,
    monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor

    from agent.repositories.voice_live_runs import (
        VoiceLiveRunRepositoryConflict,
        VoiceLiveRunRepositoryInProgress,
    )

    race_engine = create_engine(
        f"sqlite:///{tmp_path / 'live-run-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    VoiceLiveRunDB.__table__.create(race_engine)
    VoiceLiveRunSegmentDB.__table__.create(race_engine)
    monkeypatch.setattr("agent.repositories.voice_live_runs.engine", race_engine)
    principal = VoicePrincipal(tenant_id="race-tenant", subject="race-owner")

    for index in range(10):
        run_id = f"voice-live-run-race-{index}"
        now = time.time()
        with Session(race_engine) as session:
            session.add(
                VoiceLiveRunDB(
                    id=run_id,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    profile_id="race-profile",
                    idempotency_key_digest=f"run-key-{index}",
                    parent_task_id=f"parent-{index}",
                    source="microphone",
                    segment_duration_seconds=60,
                    max_duration_seconds=120,
                    overlap_milliseconds=0,
                    capture_deadline_at=now + 120,
                    expires_at=now + 3_720,
                )
            )
            session.commit()
        barrier = threading.Barrier(2)

        def reserve():
            barrier.wait()
            try:
                VoiceLiveRunRepository().reserve_segment(
                    principal,
                    run_id,
                    sequence=0,
                    idempotency_key_digest=f"segment-key-{index}",
                    audio_binding=f"binding-{index}",
                    started_at_ms=0,
                    ended_at_ms=1_000,
                    duration_ms=1_000,
                    overlap_milliseconds=0,
                    now=now,
                )
                return "reserved"
            except VoiceLiveRunRepositoryConflict:
                return "rejected"

        def finalize():
            barrier.wait()
            try:
                VoiceLiveRunRepository().begin_finalize(
                    principal,
                    run_id,
                    expected_last_sequence=0,
                    now=now,
                )
                return "finalizing"
            except VoiceLiveRunRepositoryInProgress:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as pool:
            reserve_future = pool.submit(reserve)
            finalize_future = pool.submit(finalize)
            outcomes = {reserve_future.result(), finalize_future.result()}
        with Session(race_engine) as session:
            run = session.get(VoiceLiveRunDB, run_id)
            segments = session.exec(select(VoiceLiveRunSegmentDB).where(VoiceLiveRunSegmentDB.run_id == run_id)).all()
        assert run is not None
        assert not (run.status == "finalizing" and segments)
        assert outcomes in ({"reserved", "blocked"}, {"rejected", "finalizing"})


def test_reclaimed_finalizer_fences_stale_abort_and_completion():
    from agent.repositories.voice_live_runs import VoiceLiveRunRepositoryConflict

    principal = VoicePrincipal(tenant_id="finalize-reclaim", subject="finalize-owner")
    service = VoiceLiveRunService()
    created, _ = service.create(
        principal,
        idempotency_key="finalize-reclaim-create",
        lease_token=_lease_token(principal, "finalize-profile"),
        source="microphone",
        profile_id="finalize-profile",
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=60,
        max_duration_seconds=60,
        overlap_milliseconds=0,
    )
    repository = VoiceLiveRunRepository()
    first, first_replayed = repository.begin_finalize(
        principal,
        created["run"]["id"],
        expected_last_sequence=-1,
        now=1_000.0,
    )
    second, second_replayed = repository.begin_finalize(
        principal,
        created["run"]["id"],
        expected_last_sequence=-1,
        now=1_601.0,
    )

    assert first_replayed is False
    assert second_replayed is False
    assert second.version > first.version
    assert (
        repository.abort_finalize(
            principal,
            created["run"]["id"],
            expected_version=first.version,
            now=1_602.0,
        )
        is False
    )
    with pytest.raises(VoiceLiveRunRepositoryConflict):
        repository.complete_finalize(
            principal,
            created["run"]["id"],
            expected_version=first.version,
            result_ref="voice-result-stale-finalizer",
            has_gaps=False,
            stop_reason="user_stop",
            now=1_603.0,
        )
    current = repository.get(principal, created["run"]["id"])
    assert current is not None
    assert current.status == "finalizing"
    assert current.version == second.version


def test_offline_last_segment_can_drain_after_capture_deadline_but_not_after_expiry():
    clock = [1_000.0]
    principal = VoicePrincipal(tenant_id="drain-tenant", subject="drain-owner")
    service = VoiceLiveRunService(now=lambda: clock[0])
    created, _ = service.create(
        principal,
        idempotency_key="drain-create",
        lease_token=_lease_token(principal, "drain-profile"),
        source="microphone",
        profile_id="drain-profile",
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=60,
        max_duration_seconds=120,
        overlap_milliseconds=0,
    )
    run_id = created["run"]["id"]
    clock[0] = 1_121.0
    drained = service.reserve_audio_segment(
        principal,
        run_id,
        sequence=1,
        idempotency_key="offline-last-segment",
        audio=_wav(sample=7),
        started_at_ms=119_000,
        ended_at_ms=120_000,
        duration_ms=1_000,
        overlap_milliseconds=0,
    )
    assert drained.reservation.segment.status == "processing"

    service.fail_segment(
        principal,
        run_id,
        sequence=1,
        idempotency_key_digest=drained.idempotency_key_digest,
        failure_code="test_cleanup",
    )
    clock[0] = 4_721.0
    try:
        service.reserve_audio_segment(
            principal,
            run_id,
            sequence=0,
            idempotency_key="after-expiry",
            audio=_wav(sample=8),
            started_at_ms=0,
            ended_at_ms=1_000,
            duration_ms=1_000,
            overlap_milliseconds=0,
        )
    except VoiceLiveRunError as exc:
        assert exc.code == "voice_live_run.not_active"
    else:  # pragma: no cover - explicit assertion branch
        raise AssertionError("segment registration after durable expiry was accepted")
    with Session(engine) as session:
        run = session.get(VoiceLiveRunDB, run_id)
        parent = session.get(TaskDB, created["run"]["parent_task_id"])
    assert run is not None and run.status == "expired"
    assert parent is not None and parent.status == "cancelled"


def test_stop_builds_bounded_encrypted_manifest_for_240_segments():
    principal = VoicePrincipal(tenant_id="manifest-tenant", subject="manifest-owner")
    service = VoiceLiveRunService()
    created, _ = service.create(
        principal,
        idempotency_key="manifest-create",
        lease_token=_lease_token(principal, "manifest-profile"),
        source="system_audio",
        profile_id="manifest-profile",
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=120,
        max_duration_seconds=28_800,
        overlap_milliseconds=0,
    )
    run_id = created["run"]["id"]
    shared = get_voice_result_artifact_service().create(
        principal,
        request_hash="manifest-shared-segment",
        result=_result("segment text"),
        profile_id="manifest-profile",
    )
    now = time.time()
    with Session(engine) as session:
        session.add_all(
            [
                VoiceLiveRunSegmentDB(
                    run_id=run_id,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    sequence=sequence,
                    status="completed",
                    idempotency_key_digest=f"digest-{sequence}",
                    task_id=f"segment-task-{sequence}",
                    result_ref=shared["id"],
                    started_at_ms=sequence * 120_000,
                    ended_at_ms=(sequence + 1) * 120_000,
                    duration_ms=120_000,
                    overlap_milliseconds=0,
                    completed_at=now,
                )
                for sequence in range(240)
            ]
        )
        run = session.get(VoiceLiveRunDB, run_id)
        assert run is not None
        run.last_local_sequence = 239
        session.add(run)
        session.commit()

    stopped = service.stop(
        principal,
        run_id,
        last_sequence=239,
        reason="safety_limit",
    )
    final_ref = stopped["run"]["final_result_ref"]
    final_artifact = get_voice_result_artifact_service().get(principal, final_ref)
    canonical = json.dumps(final_artifact["result"], ensure_ascii=False).encode("utf-8")
    assert stopped["run"]["status"] == "completed"
    assert len(final_artifact["result"]["segments"]) == 240
    assert len(canonical) < 2 * 1024 * 1024
    with Session(engine) as session:
        persisted = session.exec(
            select(VoiceResultArtifactDB).where(
                (VoiceResultArtifactDB.id == final_ref) | (VoiceResultArtifactDB.parent_artifact_id == final_ref)
            )
        ).all()
    assert persisted
    assert all("segment text" not in row.payload_ciphertext for row in persisted)


def test_voice_profile_deletion_removes_live_ledgers_tasks_and_encrypted_results(
    client,
    admin_auth_header,
):
    profile_id = "live-delete-profile"
    created = _create_run(client, admin_auth_header, profile_id=profile_id)
    data = created.get_json()["data"]
    run_id = data["run"]["id"]
    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _result("zu loeschender Klartext")
        uploaded = _put_audio(
            client,
            admin_auth_header,
            run_id,
            0,
            key="delete-segment",
            audio=_wav(sample=9),
            started_at_ms=0,
            ended_at_ms=1_000,
        )
    segment_task_id = uploaded.get_json()["data"]["segment"]["task_id"]
    result_ref = uploaded.get_json()["data"]["result_ref"]
    deleted = client.delete(
        f"/v1/voice/privacy/{profile_id}",
        headers={**admin_auth_header, "Idempotency-Key": "delete-live-profile"},
        json={"confirmed": True},
    )

    assert deleted.status_code == 200
    counts = deleted.get_json()["data"]["deletion"]["deleted_by_store"]
    assert counts["voice_live_runs"] == 1
    assert counts["voice_live_run_segments"] == 1
    with Session(engine) as session:
        assert session.get(VoiceLiveRunDB, run_id) is None
        assert session.get(TaskDB, data["run"]["parent_task_id"]) is None
        assert session.get(TaskDB, segment_task_id) is None
        assert session.get(VoiceResultArtifactDB, result_ref) is None


def test_segment_completion_fence_compensates_concurrent_profile_deletion(
    client,
    admin_auth_header,
):
    from agent.services.voice_privacy_service import get_voice_privacy_service

    profile_id = "delete-during-provider"
    created = _create_run(client, admin_auth_header, profile_id=profile_id)
    run = created.get_json()["data"]["run"]
    principal = VoicePrincipal(tenant_id="admin", subject="admin")

    def delete_then_return(**_kwargs):
        get_voice_privacy_service().delete_profile(
            principal,
            profile_id=profile_id,
            idempotency_key="provider-race-delete",
        )
        return _result("darf nicht wiederauferstehen")

    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.side_effect = delete_then_return
        response = _put_audio(
            client,
            admin_auth_header,
            run["id"],
            0,
            key="delete-race-segment",
            audio=_wav(sample=10),
            started_at_ms=0,
            ended_at_ms=1_000,
        )

    assert response.status_code == 409
    assert response.get_json()["data"]["error"]["code"] == ("voice_live_run.deleted_during_processing")
    with Session(engine) as session:
        assert session.exec(select(VoiceLiveRunDB).where(VoiceLiveRunDB.profile_id == profile_id)).all() == []
        assert (
            session.exec(
                select(VoiceLiveRunSegmentDB).where(VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id)
            ).all()
            == []
        )
        assert (
            session.exec(select(VoiceResultArtifactDB).where(VoiceResultArtifactDB.profile_id == profile_id)).all()
            == []
        )
        assert (
            session.exec(
                select(TaskDB).where(
                    (TaskDB.id == run["parent_task_id"]) | (TaskDB.parent_task_id == run["parent_task_id"])
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.operation == "voice.live_segment"
                )
            ).all()
            == []
        )


def test_live_run_database_rows_never_contain_audio_or_transcript_plaintext(
    client,
    admin_auth_header,
):
    secret_text = "streng vertraulicher Livetext"
    audio = _wav(sample=12_345)
    created = _create_run(client, admin_auth_header, profile_id="content-free-ledger")
    run_id = created.get_json()["data"]["run"]["id"]
    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.return_value = _result(secret_text)
        uploaded = _put_audio(
            client,
            admin_auth_header,
            run_id,
            0,
            key="content-free-segment",
            audio=audio,
            started_at_ms=0,
            ended_at_ms=1_000,
        )
    with Session(engine) as session:
        run = session.get(VoiceLiveRunDB, run_id)
        segment = session.exec(select(VoiceLiveRunSegmentDB).where(VoiceLiveRunSegmentDB.run_id == run_id)).one()
        parent = session.get(TaskDB, created.get_json()["data"]["run"]["parent_task_id"])
        child = session.get(TaskDB, uploaded.get_json()["data"]["segment"]["task_id"])
    serialized = json.dumps(
        {
            "run": run.model_dump() if run else {},
            "segment": segment.model_dump(),
            "parent": parent.model_dump() if parent else {},
            "child": child.model_dump() if child else {},
        },
        default=str,
        sort_keys=True,
    )
    assert secret_text not in serialized
    assert audio.hex()[:128] not in serialized
    assert segment.audio_binding.startswith("idem-audio-v1:")


def test_artifact_create_failure_after_profile_delete_is_fully_compensated(
    client,
    admin_auth_header,
):
    profile_id = "delete-during-artifact-create"
    created = _create_run(client, admin_auth_header, profile_id=profile_id)
    run = created.get_json()["data"]["run"]
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    artifacts = get_voice_result_artifact_service()
    original_create = artifacts.create
    execution_task_ids: list[str] = []

    def persist_after_delete(*args, **kwargs):
        with Session(engine) as session:
            execution_task_ids.extend(
                task.id
                for task in session.exec(select(TaskDB).where(TaskDB.parent_task_id == run["parent_task_id"])).all()
            )
        get_voice_privacy_service().delete_profile(
            principal,
            profile_id=profile_id,
            idempotency_key="delete-before-artifact-commit",
        )
        original_create(*args, **kwargs)
        raise VoiceGovernanceError(
            code="voice_result.synthetic_post_commit_failure",
            message="synthetic artifact post-commit failure",
            status_code=409,
        )

    with (
        patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory,
        patch.object(artifacts, "create", side_effect=persist_after_delete),
    ):
        provider_factory.return_value.transcribe.return_value = _result("darf nach Delete nicht persistieren")
        response = _put_audio(
            client,
            admin_auth_header,
            run["id"],
            0,
            key="artifact-delete-race",
            audio=_wav(sample=21),
            started_at_ms=0,
            ended_at_ms=1_000,
        )

    assert response.status_code == 409
    with Session(engine) as session:
        assert (
            session.exec(select(VoiceResultArtifactDB).where(VoiceResultArtifactDB.profile_id == profile_id)).all()
            == []
        )
        assert all(session.get(TaskDB, task_id) is None for task_id in execution_task_ids)
        assert (
            session.exec(
                select(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.operation == "voice.live_segment"
                )
            ).all()
            == []
        )


def test_idempotency_completion_failure_after_delete_does_not_recreate_task(
    client,
    admin_auth_header,
):
    profile_id = "delete-during-idempotency-complete"
    created = _create_run(client, admin_auth_header, profile_id=profile_id)
    run = created.get_json()["data"]["run"]
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    original_complete = VoiceIdempotencyService.complete
    completed_task_ids: list[str] = []

    def delete_then_complete(service, claim, metadata):
        completed_task_ids.append(str(metadata["task_id"]))
        get_voice_privacy_service().delete_profile(
            principal,
            profile_id=profile_id,
            idempotency_key="delete-before-idempotency-complete",
        )
        return original_complete(service, claim, metadata)

    with (
        patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory,
        patch.object(VoiceIdempotencyService, "complete", new=delete_then_complete),
    ):
        provider_factory.return_value.transcribe.return_value = _result("kurzlebig")
        response = _put_audio(
            client,
            admin_auth_header,
            run["id"],
            0,
            key="idempotency-delete-race",
            audio=_wav(sample=22),
            started_at_ms=0,
            ended_at_ms=1_000,
        )

    assert response.status_code == 409
    with Session(engine) as session:
        assert all(session.get(TaskDB, task_id) is None for task_id in completed_task_ids)
        assert (
            session.exec(select(VoiceResultArtifactDB).where(VoiceResultArtifactDB.profile_id == profile_id)).all()
            == []
        )


def test_stale_provider_result_after_stop_is_rejected_and_compensated(
    client,
    admin_auth_header,
):
    from concurrent.futures import ThreadPoolExecutor

    created = _create_run(client, admin_auth_header, profile_id="stale-stop-profile")
    run = created.get_json()["data"]["run"]
    provider_entered = threading.Event()
    release_provider = threading.Event()

    def blocked_provider(**_kwargs):
        provider_entered.set()
        assert release_provider.wait(timeout=10)
        return _result("zu spaet")

    def upload():
        with client.application.test_client() as threaded_client:
            return _put_audio(
                threaded_client,
                admin_auth_header,
                run["id"],
                0,
                key="stale-stop-segment",
                audio=_wav(sample=23),
                started_at_ms=0,
                ended_at_ms=1_000,
            )

    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.side_effect = blocked_provider
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(upload)
            assert provider_entered.wait(timeout=10)
            with Session(engine) as session:
                segment = session.exec(
                    select(VoiceLiveRunSegmentDB).where(VoiceLiveRunSegmentDB.run_id == run["id"])
                ).one()
                segment.updated_at = time.time() - 601
                session.add(segment)
                session.commit()
            stopped = client.post(
                f"/v1/voice/live-runs/{run['id']}/stop",
                headers=admin_auth_header,
                json={"last_sequence": 0, "reason": "provider_stalled"},
            )
            release_provider.set()
            late = future.result(timeout=10)

    assert stopped.status_code == 200
    assert stopped.get_json()["data"]["run"]["status"] == "completed_with_gaps"
    assert late.status_code == 409
    assert late.get_json()["data"]["error"]["code"] == ("voice_live_run.execution_no_longer_owned")
    with Session(engine) as session:
        segment = session.exec(select(VoiceLiveRunSegmentDB).where(VoiceLiveRunSegmentDB.run_id == run["id"])).one()
        assert segment.result_ref is None
        assert (
            session.exec(
                select(VoiceGovernanceIdempotencyDB).where(
                    VoiceGovernanceIdempotencyDB.operation == "voice.live_segment"
                )
            ).all()
            == []
        )


def test_reclaimed_attempt_keeps_new_result_and_rejects_old_provider(
    client,
    admin_auth_header,
):
    from concurrent.futures import ThreadPoolExecutor

    created = _create_run(client, admin_auth_header, profile_id="attempt-reclaim-profile")
    run_id = created.get_json()["data"]["run"]["id"]
    first_entered = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def provider(**_kwargs):
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=10)
            return _result("alter Versuch")
        return _result("neuer Versuch")

    def first_upload():
        with client.application.test_client() as threaded_client:
            return _put_audio(
                threaded_client,
                admin_auth_header,
                run_id,
                0,
                key="reclaimed-segment",
                audio=_wav(sample=24),
                started_at_ms=0,
                ended_at_ms=1_000,
            )

    with patch("agent.routes.voice_live_runs.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.side_effect = provider
        with ThreadPoolExecutor(max_workers=1) as pool:
            old_future = pool.submit(first_upload)
            assert first_entered.wait(timeout=10)
            with Session(engine) as session:
                segment = session.exec(
                    select(VoiceLiveRunSegmentDB).where(VoiceLiveRunSegmentDB.run_id == run_id)
                ).one()
                segment.updated_at = time.time() - 601
                session.add(segment)
                session.commit()
            new_response = _put_audio(
                client,
                admin_auth_header,
                run_id,
                0,
                key="reclaimed-segment",
                audio=_wav(sample=24),
                started_at_ms=0,
                ended_at_ms=1_000,
            )
            release_first.set()
            old_response = old_future.result(timeout=10)

    assert new_response.status_code == 200
    assert new_response.get_json()["data"]["result"]["text"] == "neuer Versuch"
    assert old_response.status_code == 409
    snapshot = client.get(
        f"/v1/voice/live-runs/{run_id}",
        headers=admin_auth_header,
    ).get_json()["data"]
    assert snapshot["segments"][0]["attempt_count"] == 2
    assert snapshot["segments"][0]["text"] == "neuer Versuch"
    with Session(engine) as session:
        claims = session.exec(
            select(VoiceGovernanceIdempotencyDB).where(VoiceGovernanceIdempotencyDB.operation == "voice.live_segment")
        ).all()
    assert len(claims) == 1
    assert claims[0].state == "completed"


def test_late_voice_worker_finishes_do_not_resurrect_deleted_tasks(
    client,
    admin_auth_header,
):
    profile_id = "late-worker-delete-profile"
    created = _create_run(client, admin_auth_header, profile_id=profile_id)
    run = created.get_json()["data"]["run"]
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    root = get_voice_delegation_task_service().start(
        principal,
        request_id="late-worker-root",
        request_hash="late-worker-root-ref",
        effective_configuration={},
        deadline_seconds=120,
        idempotency_key="late-worker-root",
        profile_id=profile_id,
        parent_task_id=run["parent_task_id"],
        operation="live_segment",
    )
    judge_id = VoiceGenerativeJudgeTaskTracker().start(
        tenant_id=principal.tenant_id,
        parent_task_id=root.task_id,
        request_id="late-judge",
        content_digest="a" * 64,
        policy_digest="b" * 64,
    )
    corrector_id = VoiceGenerativeCorrectorTaskTracker().start(
        tenant_id=principal.tenant_id,
        parent_task_id=root.task_id,
        request_id="late-corrector",
        content_digest="c" * 64,
        policy_digest="d" * 64,
        model_id="local-model",
    )
    get_voice_privacy_service().delete_profile(
        principal,
        profile_id=profile_id,
        idempotency_key="delete-before-worker-finishes",
    )

    VoiceGenerativeJudgeTaskTracker.finish(
        judge_id,
        status="selected",
        reason_code="generative_judge_selected",
    )
    VoiceGenerativeCorrectorTaskTracker.finish(
        corrector_id,
        status="corrected",
        reason_code="generative_corrector_applied",
    )
    get_voice_delegation_task_service().complete(root, result_ref="deleted-result")

    with Session(engine) as session:
        assert session.get(TaskDB, root.task_id) is None
        assert session.get(TaskDB, judge_id) is None
        assert session.get(TaskDB, corrector_id) is None


def test_request_lazy_expiry_fails_segment_and_cancels_all_tasks():
    clock = [10_000.0]
    principal = VoicePrincipal(tenant_id="lazy-expiry-tenant", subject="lazy-expiry-owner")
    service = VoiceLiveRunService(now=lambda: clock[0])
    created, _ = service.create(
        principal,
        idempotency_key="lazy-expiry-create",
        lease_token=_lease_token(principal, "lazy-expiry-profile"),
        source="microphone",
        profile_id="lazy-expiry-profile",
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=60,
        max_duration_seconds=60,
        overlap_milliseconds=0,
    )
    run_id = created["run"]["id"]
    claim = service.reserve_audio_segment(
        principal,
        run_id,
        sequence=0,
        idempotency_key="lazy-expiry-segment",
        audio=_wav(sample=25),
        started_at_ms=0,
        ended_at_ms=1_000,
        duration_ms=1_000,
        overlap_milliseconds=0,
    )
    child = get_voice_delegation_task_service().start(
        principal,
        request_id="lazy-expiry-child",
        request_hash="lazy-expiry-child-ref",
        effective_configuration={},
        deadline_seconds=120,
        idempotency_key="lazy-expiry-child",
        profile_id="lazy-expiry-profile",
        parent_task_id=created["run"]["parent_task_id"],
        operation="live_segment",
    )
    service.bind_segment_task(
        principal,
        run_id,
        sequence=0,
        idempotency_key_digest=claim.idempotency_key_digest,
        attempt_count=claim.reservation.segment.attempt_count,
        task_id=child.task_id,
    )
    clock[0] = created["run"]["expires_at"] + 1
    snapshot = service.snapshot(principal, run_id, include_text=False)

    assert snapshot["run"]["status"] == "expired"
    assert snapshot["segments"][0]["failure_code"] == "run_expired"
    with Session(engine) as session:
        assert session.get(TaskDB, created["run"]["parent_task_id"]).status == "cancelled"
        assert session.get(TaskDB, child.task_id).status == "cancelled"


def test_maintenance_claim_is_idempotent_and_multi_hub_safe(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    now = time.time()
    race_engine = create_engine(
        f"sqlite:///{tmp_path / 'maintenance-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    VoiceLiveRunDB.__table__.create(race_engine)
    VoiceLiveRunSegmentDB.__table__.create(race_engine)
    monkeypatch.setattr("agent.repositories.voice_live_runs.engine", race_engine)
    run_id = "voice-live-run-maintenance-race"
    with Session(race_engine) as session:
        session.add(
            VoiceLiveRunDB(
                id=run_id,
                tenant_id="maintenance-tenant",
                owner_subject="maintenance-owner",
                profile_id="maintenance-profile",
                idempotency_key_digest="maintenance-key",
                parent_task_id="maintenance-parent",
                source="microphone",
                segment_duration_seconds=60,
                max_duration_seconds=60,
                overlap_milliseconds=0,
                capture_deadline_at=now - 61,
                expires_at=now - 1,
            )
        )
        session.commit()

    class NoopTasks:
        @staticmethod
        def cancel_child(_task_id, *, reason_code):
            assert reason_code == "voice_live_run_expired"

        @staticmethod
        def expire_parent(_run):
            return None

    barrier = threading.Barrier(2)

    def sweep():
        barrier.wait()
        return VoiceLiveRunMaintenanceService(
            tasks=NoopTasks(),
            clock=lambda: now,
        ).run_once()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(sweep), pool.submit(sweep)]
        results = [future.result() for future in futures]
    replay = VoiceLiveRunMaintenanceService(
        tasks=NoopTasks(),
        clock=lambda: now,
    ).run_once()

    assert sum(item["expired_runs"] for item in results) == 1
    assert replay["expired_runs"] == 0
    with Session(race_engine) as session:
        run = session.get(VoiceLiveRunDB, run_id)
        assert run.status == "expired"
        assert run.maintenance_reconciled_at is not None


def test_expiry_maintenance_replays_after_crash_window():
    now = time.time()
    principal = VoicePrincipal(tenant_id="maintenance-replay", subject="maintenance-owner")
    service = VoiceLiveRunService()
    created, _ = service.create(
        principal,
        idempotency_key="maintenance-replay-create",
        lease_token=_lease_token(principal, "maintenance-replay-profile"),
        source="microphone",
        profile_id="maintenance-replay-profile",
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=60,
        max_duration_seconds=60,
        overlap_milliseconds=0,
    )
    with Session(engine) as session:
        run = session.get(VoiceLiveRunDB, created["run"]["id"])
        run.expires_at = now - 1
        session.add(run)
        session.commit()
    claimed = VoiceLiveRunRepository().claim_expired_runs(
        now=now,
        lease_seconds=30,
    )
    assert len(claimed) == 1
    with Session(engine) as session:
        assert session.get(TaskDB, created["run"]["parent_task_id"]).status == "in_progress"

    replay = VoiceLiveRunMaintenanceService(clock=lambda: now + 31).run_once()

    assert replay["expired_runs"] == 1
    with Session(engine) as session:
        run = session.get(VoiceLiveRunDB, created["run"]["id"])
        assert run.maintenance_reconciled_at is not None
        assert session.get(TaskDB, created["run"]["parent_task_id"]).status == "cancelled"


def test_stop_profile_delete_race_removes_post_delete_final_manifest():
    principal = VoicePrincipal(tenant_id="stop-delete-tenant", subject="stop-delete-owner")
    profile_id = "stop-delete-profile"
    service = VoiceLiveRunService()
    created, _ = service.create(
        principal,
        idempotency_key="stop-delete-create",
        lease_token=_lease_token(principal, profile_id),
        source="microphone",
        profile_id=profile_id,
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=60,
        max_duration_seconds=60,
        overlap_milliseconds=0,
    )
    artifacts = get_voice_result_artifact_service()
    original_create = artifacts.create

    def create_manifest_after_delete(*args, **kwargs):
        get_voice_privacy_service().delete_profile(
            principal,
            profile_id=profile_id,
            idempotency_key="delete-during-stop",
        )
        return original_create(*args, **kwargs)

    with patch.object(artifacts, "create", side_effect=create_manifest_after_delete):
        try:
            service.stop(
                principal,
                created["run"]["id"],
                last_sequence=-1,
                reason="user_stop",
            )
        except VoiceLiveRunError as exc:
            assert exc.code == "voice_live_run.deleted_during_finalization"
        else:  # pragma: no cover - explicit assertion branch
            raise AssertionError("stop succeeded after its profile was deleted")

    with Session(engine) as session:
        assert session.get(VoiceLiveRunDB, created["run"]["id"]) is None
        assert (
            session.exec(select(VoiceResultArtifactDB).where(VoiceResultArtifactDB.profile_id == profile_id)).all()
            == []
        )
        assert session.get(TaskDB, created["run"]["parent_task_id"]) is None


def test_create_profile_delete_race_removes_post_delete_parent_task(
    client,
    admin_auth_header,
):
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    profile_id = "create-delete-profile"
    original_ensure = VoiceLiveRunTaskPort.ensure_parent
    parent_ids: list[str] = []

    def delete_then_create_parent(task_port, scoped_principal, run):
        parent_ids.append(run.parent_task_id)
        get_voice_privacy_service().delete_profile(
            principal,
            profile_id=profile_id,
            idempotency_key="delete-during-live-create",
        )
        return original_ensure(task_port, scoped_principal, run)

    with patch.object(
        VoiceLiveRunTaskPort,
        "ensure_parent",
        new=delete_then_create_parent,
    ):
        response = _create_run(
            client,
            admin_auth_header,
            profile_id=profile_id,
            key="create-delete-race",
        )

    assert response.status_code == 409
    assert response.get_json()["data"]["error"]["code"] == ("voice_live_run.deleted_during_create")
    with Session(engine) as session:
        assert session.exec(select(VoiceLiveRunDB).where(VoiceLiveRunDB.profile_id == profile_id)).all() == []
        assert all(session.get(TaskDB, task_id) is None for task_id in parent_ids)


def test_create_commit_after_delete_scan_removes_exact_run_identity(
    client,
    admin_auth_header,
):
    principal = VoicePrincipal(tenant_id="admin", subject="admin")
    profile_id = "create-commit-after-delete"
    original_create = VoiceLiveRunRepository.create

    def delete_then_persist(repository, run):
        get_voice_privacy_service().delete_profile(
            principal,
            profile_id=profile_id,
            idempotency_key="delete-before-live-run-commit",
        )
        return original_create(repository, run)

    with patch.object(VoiceLiveRunRepository, "create", new=delete_then_persist):
        response = _create_run(
            client,
            admin_auth_header,
            profile_id=profile_id,
            key="create-after-delete-scan",
        )

    assert response.status_code == 409
    with Session(engine) as session:
        assert session.exec(select(VoiceLiveRunDB).where(VoiceLiveRunDB.profile_id == profile_id)).all() == []
        assert session.exec(select(TaskDB).where(TaskDB.task_kind == "voice_live_run")).all() == []


def test_result_link_profile_delete_race_removes_post_delete_child_task():
    principal = VoicePrincipal(tenant_id="link-delete-tenant", subject="link-delete-owner")
    profile_id = "link-delete-profile"
    service = VoiceLiveRunService()
    created, _ = service.create(
        principal,
        idempotency_key="link-delete-create",
        lease_token=_lease_token(principal, profile_id),
        source="microphone",
        profile_id=profile_id,
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=60,
        max_duration_seconds=60,
        overlap_milliseconds=0,
    )
    linked = get_voice_result_artifact_service().create(
        principal,
        request_hash="link-delete-existing-result",
        result=_result("existing result"),
        profile_id=profile_id,
    )
    original_create_link = VoiceLiveRunTaskPort.create_link_child
    task_ids: list[str] = []

    def delete_then_create_child(task_port, scoped_principal, run, **kwargs):
        get_voice_privacy_service().delete_profile(
            principal,
            profile_id=profile_id,
            idempotency_key="delete-during-result-link",
        )
        task = original_create_link(task_port, scoped_principal, run, **kwargs)
        task_ids.append(task.task_id)
        return task

    with patch.object(
        VoiceLiveRunTaskPort,
        "create_link_child",
        new=delete_then_create_child,
    ):
        try:
            service.register_result_segment(
                principal,
                created["run"]["id"],
                sequence=0,
                idempotency_key="link-delete-segment",
                result_ref=linked["id"],
                started_at_ms=0,
                ended_at_ms=1_000,
                duration_ms=1_000,
                overlap_milliseconds=0,
            )
        except VoiceLiveRunError as exc:
            assert exc.code == "voice_live_run.not_found"
        else:  # pragma: no cover - explicit assertion branch
            raise AssertionError("result link succeeded after profile deletion")

    with Session(engine) as session:
        assert all(session.get(TaskDB, task_id) is None for task_id in task_ids)
        assert session.get(VoiceLiveRunDB, created["run"]["id"]) is None


def test_create_rejects_max_duration_shorter_than_segment(
    client,
    admin_auth_header,
):
    response = _create_run(
        client,
        admin_auth_header,
        segment_seconds=120,
        max_seconds=60,
    )
    assert response.status_code == 422
    assert response.get_json()["data"]["error"]["code"] == ("voice_live_run.invalid_max_duration_seconds")


def test_live_segment_publishes_provisional_before_blocked_correction_and_delta_revises_it(
    client,
    admin_auth_header,
):
    profile_id = f"revisioned-live-{uuid.uuid4().hex}"
    assert _configure_live_corrector(client, admin_auth_header, profile_id).status_code == 200
    created = _create_run(client, admin_auth_header, profile_id=profile_id)
    run_id = created.get_json()["data"]["run"]["id"]
    correction_started = threading.Event()
    release_correction = threading.Event()
    provider = Mock()
    provider.transcribe.return_value = _result("hallo welt")
    corrector = Mock()

    def blocked_correction(result, **_kwargs):
        correction_started.set()
        assert release_correction.wait(timeout=10)
        return VoiceGenerativeCorrectorOutcome(
            result={**dict(result), "original_text": result["text"], "text": "Hallo Welt."},
            applied=True,
            reason_code="generative_corrector_corrected",
        )

    corrector.apply.side_effect = blocked_correction
    with (
        patch(
            "agent.routes.voice_live_runs.get_voice_provider_service",
            return_value=provider,
        ),
        patch(
            "agent.services.voice_transcription_postprocessing_service."
            "get_voice_generative_corrector_service",
            return_value=corrector,
        ),
    ):
        uploaded = _put_audio(
            client,
            admin_auth_header,
            run_id,
            0,
            key="revisioned-segment-zero",
            audio=_wav(sample=31),
            started_at_ms=0,
            ended_at_ms=1_000,
        )
        assert uploaded.status_code == 200
        provisional = uploaded.get_json()["data"]["segment"]
        assert provisional["status"] == "completed"
        assert provisional["text_state"] == "provisional"
        assert provisional["correction_status"] == "queued"
        assert provisional["revision"] == provisional["text_revision"] == 1
        assert provisional["text"] == "hallo welt"
        provisional_timeline_revision = provisional["timeline_revision"]
        assert correction_started.wait(timeout=5)

        blocked_stop = client.post(
            f"/v1/voice/live-runs/{run_id}/stop",
            headers=admin_auth_header,
            json={"last_sequence": 0, "reason": "test_stop"},
        )
        assert blocked_stop.status_code == 409
        assert blocked_stop.get_json()["data"]["error"] == {
            "code": "voice_live_run.segments_in_flight",
            "message": "voice live run still has in-flight corrections",
            "retriable": True,
        }

        processing = client.get(
            f"/v1/voice/live-runs/{run_id}?after_revision={provisional_timeline_revision}",
            headers=admin_auth_header,
        )
        assert processing.status_code == 200
        assert processing.get_json()["data"]["composed_transcript"] is None

        release_correction.set()
        assert get_voice_live_run_correction_service().wait_for_idle(timeout=10)

    artifacts = get_voice_result_artifact_service()
    with patch.object(artifacts, "get", wraps=artifacts.get) as artifact_get:
        revised = client.get(
            f"/v1/voice/live-runs/{run_id}?after_revision={provisional_timeline_revision}",
            headers=admin_auth_header,
        )
    assert revised.status_code == 200
    revised_data = revised.get_json()["data"]
    assert revised_data["composed_transcript"] is None
    assert len(revised_data["segments"]) == 1
    final = revised_data["segments"][0]
    assert final["sequence"] == 0
    assert final["text_state"] == "final"
    assert final["correction_status"] == "completed"
    assert final["revision"] == final["text_revision"] == 2
    assert final["text"] == "Hallo Welt."
    assert final["timeline_revision"] > provisional_timeline_revision
    assert artifact_get.call_count == 1

    with Session(engine) as session:
        segment = session.exec(
            select(VoiceLiveRunSegmentDB).where(
                VoiceLiveRunSegmentDB.run_id == run_id,
                VoiceLiveRunSegmentDB.sequence == 0,
            )
        ).one()
        task = session.get(TaskDB, segment.correction_task_id)
        serialized_ledger = json.dumps(segment.model_dump(), default=str)
        serialized_task = json.dumps(task.worker_execution_context, default=str)
    assert "hallo welt" not in serialized_ledger.casefold()
    assert "hallo welt" not in serialized_task.casefold()
    assert segment.provisional_result_ref != segment.result_ref
    assert segment.correction_spec_ref

    stopped = client.post(
        f"/v1/voice/live-runs/{run_id}/stop",
        headers=admin_auth_header,
        json={"last_sequence": 0, "reason": "test_stop"},
    )
    assert stopped.status_code == 200
    assert stopped.get_json()["data"]["run"]["status"] == "completed"


def test_stop_reclaims_stale_correction_inside_the_client_drain_window(
    client,
    admin_auth_header,
):
    profile_id = f"stale-correction-stop-{uuid.uuid4().hex}"
    assert _configure_live_corrector(client, admin_auth_header, profile_id).status_code == 200
    created = _create_run(client, admin_auth_header, profile_id=profile_id)
    run_id = created.get_json()["data"]["run"]["id"]
    provider = Mock()
    provider.transcribe.return_value = _result("bleibt als vorläufiges Ergebnis erhalten")
    correction_service = get_voice_live_run_correction_service()
    with (
        patch(
            "agent.routes.voice_live_runs.get_voice_provider_service",
            return_value=provider,
        ),
        patch.object(correction_service, "schedule", return_value=False),
    ):
        uploaded = _put_audio(
            client,
            admin_auth_header,
            run_id,
            0,
            key="stale-correction-stop-segment",
            audio=_wav(sample=35),
            started_at_ms=0,
            ended_at_ms=1_000,
        )
    assert uploaded.status_code == 200
    assert uploaded.get_json()["data"]["segment"]["correction_status"] == "queued"

    with Session(engine) as session:
        segment = session.exec(
            select(VoiceLiveRunSegmentDB).where(
                VoiceLiveRunSegmentDB.run_id == run_id,
                VoiceLiveRunSegmentDB.sequence == 0,
            )
        ).one()
        segment.updated_at = time.time() - 301
        session.add(segment)
        session.commit()

    stopped = client.post(
        f"/v1/voice/live-runs/{run_id}/stop",
        headers=admin_auth_header,
        json={"last_sequence": 0, "reason": "test_stop"},
    )
    assert stopped.status_code == 200
    data = stopped.get_json()["data"]
    assert data["run"]["status"] == "completed"
    assert data["segments"][0]["correction_status"] == "failed"
    assert data["segments"][0]["correction_failure_code"] == "correction_lease_expired"
    assert data["segments"][0]["text_state"] == "final_uncorrected"
    assert data["segments"][0]["text"] == "bleibt als vorläufiges Ergebnis erhalten"


def test_stale_correction_attempt_cannot_overwrite_new_authoritative_revision():
    principal = VoicePrincipal(tenant_id="correction-race-tenant", subject="correction-race-owner")
    service = VoiceLiveRunService()
    created, _ = service.create(
        principal,
        idempotency_key="correction-race-create",
        lease_token=_lease_token(principal, "correction-race-profile"),
        source="microphone",
        profile_id="correction-race-profile",
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=60,
        max_duration_seconds=60,
        overlap_milliseconds=0,
    )
    run_id = created["run"]["id"]
    reserved = service.reserve_audio_segment(
        principal,
        run_id,
        sequence=0,
        idempotency_key="correction-race-segment",
        audio=_wav(sample=32),
        started_at_ms=0,
        ended_at_ms=1_000,
        duration_ms=1_000,
        overlap_milliseconds=0,
    )
    service.bind_segment_task(
        principal,
        run_id,
        sequence=0,
        idempotency_key_digest=reserved.idempotency_key_digest,
        attempt_count=1,
        task_id="correction-race-asr-task",
    )
    service.publish_provisional(
        principal,
        run_id,
        sequence=0,
        idempotency_key_digest=reserved.idempotency_key_digest,
        attempt_count=1,
        task_id="correction-race-asr-task",
        result_ref="correction-race-provisional",
        correction_configuration_digest="a" * 64,
        correction_spec_ref="correction-race-spec",
        correction_requested=True,
    )
    repository = VoiceLiveRunRepository()
    first = repository.claim_correction(
        principal,
        run_id,
        0,
        provisional_result_ref="correction-race-provisional",
        configuration_digest="a" * 64,
        now=time.time(),
    )
    assert first.claimed is True
    repository.bind_correction_task(
        principal,
        run_id,
        0,
        provisional_result_ref="correction-race-provisional",
        attempt_count=1,
        task_id="correction-race-old-task",
        now=time.time(),
    )
    with Session(engine) as session:
        segment = session.exec(
            select(VoiceLiveRunSegmentDB).where(
                VoiceLiveRunSegmentDB.run_id == run_id,
                VoiceLiveRunSegmentDB.sequence == 0,
            )
        ).one()
        # Correction work has a shorter lease than ASR segment processing so
        # the supervised client's bounded stop drain always reaches reclaim.
        segment.updated_at = time.time() - 301
        session.add(segment)
        session.commit()
    second = repository.claim_correction(
        principal,
        run_id,
        0,
        provisional_result_ref="correction-race-provisional",
        configuration_digest="a" * 64,
        now=time.time(),
    )
    assert second.claimed is True
    assert second.segment.correction_attempt_count == 2
    with pytest.raises(VoiceLiveRunRepositoryConflict, match="ownership changed"):
        repository.complete_correction(
            principal,
            run_id,
            0,
            provisional_result_ref="correction-race-provisional",
            attempt_count=1,
            task_id="correction-race-old-task",
            result_ref="correction-race-stale-result",
            applied=True,
            reason_code="corrected",
            now=time.time(),
        )
    repository.bind_correction_task(
        principal,
        run_id,
        0,
        provisional_result_ref="correction-race-provisional",
        attempt_count=2,
        task_id="correction-race-new-task",
        now=time.time(),
    )
    completed = repository.complete_correction(
        principal,
        run_id,
        0,
        provisional_result_ref="correction-race-provisional",
        attempt_count=2,
        task_id="correction-race-new-task",
        result_ref="correction-race-authoritative-result",
        applied=True,
        reason_code="corrected",
        now=time.time(),
    )
    assert completed.result_ref == "correction-race-authoritative-result"
    assert completed.text_revision == 2
    assert completed.timeline_revision > first.segment.timeline_revision


def test_invalid_correction_spec_fails_terminal_and_keeps_provisional_result():
    principal = VoicePrincipal(tenant_id="invalid-spec-tenant", subject="invalid-spec-owner")
    service = VoiceLiveRunService()
    created, _ = service.create(
        principal,
        idempotency_key="invalid-spec-create",
        lease_token=_lease_token(principal, "invalid-spec-profile"),
        source="microphone",
        profile_id="invalid-spec-profile",
        configuration_session_id=None,
        language="de",
        segment_duration_seconds=60,
        max_duration_seconds=60,
        overlap_milliseconds=0,
    )
    run_id = created["run"]["id"]
    reserved = service.reserve_audio_segment(
        principal,
        run_id,
        sequence=0,
        idempotency_key="invalid-spec-segment",
        audio=_wav(sample=33),
        started_at_ms=0,
        ended_at_ms=1_000,
        duration_ms=1_000,
        overlap_milliseconds=0,
    )
    service.bind_segment_task(
        principal,
        run_id,
        sequence=0,
        idempotency_key_digest=reserved.idempotency_key_digest,
        attempt_count=1,
        task_id="invalid-spec-asr-task",
    )
    service.publish_provisional(
        principal,
        run_id,
        sequence=0,
        idempotency_key_digest=reserved.idempotency_key_digest,
        attempt_count=1,
        task_id="invalid-spec-asr-task",
        result_ref="invalid-spec-provisional",
        correction_configuration_digest="b" * 64,
        correction_spec_ref="missing-encrypted-correction-spec",
        correction_requested=True,
    )

    corrections = get_voice_live_run_correction_service()
    assert corrections.schedule(principal, run_id, 0) is True
    assert corrections.wait_for_idle(timeout=10)
    snapshot = service.snapshot(principal, run_id, include_text=False)
    segment = snapshot["segments"][0]
    assert segment["status"] == "completed"
    assert segment["correction_status"] == "failed"
    assert segment["text_state"] == "final_uncorrected"
    assert segment["revision"] == 2
    assert segment["result_ref"] == "invalid-spec-provisional"
    assert snapshot["resume"]["pending_correction_sequences"] == []


def test_profile_deletion_fences_blocked_live_correction_and_removes_late_writes(
    client,
    admin_auth_header,
):
    profile_id = f"delete-live-correction-{uuid.uuid4().hex}"
    assert _configure_live_corrector(client, admin_auth_header, profile_id).status_code == 200
    created = _create_run(client, admin_auth_header, profile_id=profile_id)
    run = created.get_json()["data"]["run"]
    correction_started = threading.Event()
    release_correction = threading.Event()
    provider = Mock()
    provider.transcribe.return_value = _result("zu loeschender rohtext")
    corrector = Mock()

    def blocked_correction(result, **_kwargs):
        correction_started.set()
        assert release_correction.wait(timeout=10)
        return VoiceGenerativeCorrectorOutcome(
            result={**dict(result), "text": "Zu löschender korrigierter Text."},
            applied=True,
            reason_code="generative_corrector_corrected",
        )

    corrector.apply.side_effect = blocked_correction
    with (
        patch(
            "agent.routes.voice_live_runs.get_voice_provider_service",
            return_value=provider,
        ),
        patch(
            "agent.services.voice_transcription_postprocessing_service."
            "get_voice_generative_corrector_service",
            return_value=corrector,
        ),
    ):
        uploaded = _put_audio(
            client,
            admin_auth_header,
            run["id"],
            0,
            key="delete-live-correction-segment",
            audio=_wav(sample=34),
            started_at_ms=0,
            ended_at_ms=1_000,
        )
        assert uploaded.status_code == 200
        assert correction_started.wait(timeout=5)
        with Session(engine) as session:
            segment = session.exec(
                select(VoiceLiveRunSegmentDB).where(
                    VoiceLiveRunSegmentDB.run_id == run["id"],
                    VoiceLiveRunSegmentDB.sequence == 0,
                )
            ).one()
            task_ids = {
                run["parent_task_id"],
                str(segment.task_id),
                str(segment.correction_task_id),
            }
        get_voice_privacy_service().delete_profile(
            VoicePrincipal(tenant_id="admin", subject="admin"),
            profile_id=profile_id,
            idempotency_key=f"delete-{profile_id}",
        )
        release_correction.set()
        assert get_voice_live_run_correction_service().wait_for_idle(timeout=10)

    with Session(engine) as session:
        assert session.get(VoiceLiveRunDB, run["id"]) is None
        assert session.exec(
            select(VoiceLiveRunSegmentDB).where(VoiceLiveRunSegmentDB.run_id == run["id"])
        ).first() is None
        assert session.exec(
            select(VoiceResultArtifactDB).where(
                VoiceResultArtifactDB.tenant_id == "admin",
                VoiceResultArtifactDB.owner_subject == "admin",
                VoiceResultArtifactDB.profile_id == profile_id,
            )
        ).first() is None
        assert all(session.get(TaskDB, task_id) is None for task_id in task_ids)


def test_expiry_assigns_unique_delta_revisions_beyond_one_page():
    principal = VoicePrincipal(tenant_id="expiry-delta-tenant", subject="expiry-delta-owner")
    now = time.time()
    run = VoiceLiveRunDB(
        id=f"voice-live-run-expiry-delta-{uuid.uuid4()}",
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        profile_id="expiry-delta-profile",
        idempotency_key_digest=uuid.uuid4().hex,
        parent_task_id=f"voice-live-parent-{uuid.uuid4()}",
        source="microphone",
        status="active",
        segment_duration_seconds=60,
        max_duration_seconds=28_800,
        overlap_milliseconds=0,
        last_local_sequence=149,
        last_heartbeat_at=now - 100,
        capture_deadline_at=now - 10,
        expires_at=now - 1,
        created_at=now - 1_000,
        updated_at=now - 100,
    )
    run_id = run.id
    with Session(engine) as session:
        session.add(run)
        for sequence in range(150):
            provisional_ref = f"expiry-delta-provisional-{sequence}"
            session.add(
                VoiceLiveRunSegmentDB(
                    run_id=run_id,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    sequence=sequence,
                    status="completed",
                    idempotency_key_digest=uuid.uuid4().hex,
                    result_ref=provisional_ref,
                    provisional_result_ref=provisional_ref,
                    correction_status="queued",
                    correction_configuration_digest="c" * 64,
                    correction_spec_ref=f"expiry-delta-spec-{sequence}",
                    text_revision=1,
                    started_at_ms=sequence * 1_000,
                    ended_at_ms=(sequence + 1) * 1_000,
                    duration_ms=1_000,
                    created_at=now - 100,
                    updated_at=now - 100,
                )
            )
        session.commit()

    expired = VoiceLiveRunRepository().mark_expired(principal, run_id, now=now)
    assert expired is not None
    assert expired.status == "expired"
    service = VoiceLiveRunService()
    first = service.snapshot(
        principal,
        run_id,
        after_revision=-1,
        limit=100,
        include_text=False,
    )
    second = service.snapshot(
        principal,
        run_id,
        after_revision=first["page"]["next_after_revision"],
        limit=100,
        include_text=False,
    )
    revisions = [
        int(item["timeline_revision"])
        for item in [*first["segments"], *second["segments"]]
    ]
    assert len(first["segments"]) == 100
    assert first["page"]["has_more"] is True
    assert len(second["segments"]) == 50
    assert second["page"]["has_more"] is False
    assert len(revisions) == len(set(revisions)) == 150
    assert all(item["correction_status"] == "failed" for item in second["segments"])


def test_segment_upload_and_replay_decrypt_only_the_current_revision(
    client,
    admin_auth_header,
):
    created = _create_run(
        client,
        admin_auth_header,
        max_seconds=3_600,
        overlap_ms=0,
    )
    run_id = created.get_json()["data"]["run"]["id"]
    provider = Mock()
    provider.transcribe.return_value = _result("kurzer abschnitt")
    artifacts = get_voice_result_artifact_service()
    with (
        patch(
            "agent.routes.voice_live_runs.get_voice_provider_service",
            return_value=provider,
        ),
        patch.object(artifacts, "get", wraps=artifacts.get) as artifact_get,
    ):
        for sequence in range(20):
            response = _put_audio(
                client,
                admin_auth_header,
                run_id,
                sequence,
                key=f"bounded-decrypt-{sequence}",
                audio=_wav(sample=sequence),
                started_at_ms=sequence * 1_000,
                ended_at_ms=(sequence + 1) * 1_000,
            )
            assert response.status_code == 200
        assert artifact_get.call_count == 20
        artifact_get.reset_mock()
        replay = _put_audio(
            client,
            admin_auth_header,
            run_id,
            19,
            key="bounded-decrypt-19",
            audio=_wav(sample=19),
            started_at_ms=19_000,
            ended_at_ms=20_000,
        )
        assert replay.status_code == 200
        assert replay.get_json()["data"]["idempotent_replay"] is True
        assert artifact_get.call_count == 1
