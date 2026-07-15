import time
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TaskDB, VoiceGovernanceIdempotencyDB, VoiceResultArtifactDB
from agent.services.voice_governance_domain import VoiceGovernanceError
from agent.services.voice_provider import VoiceProviderError
from agent.services.voice_result_artifact_service import get_voice_result_artifact_service


def test_voice_transcribe_requires_file(client, admin_auth_header):
    res = client.post("/v1/voice/transcribe", headers=admin_auth_header)
    assert res.status_code == 400
    payload = res.json["data"]["error"]
    assert payload["code"] == "validation.missing_file"


def test_voice_transcribe_rejects_body_before_multipart_materialization(client, admin_auth_header):
    original = client.application.config.get("AGENT_CONFIG")
    client.application.config["AGENT_CONFIG"] = {"voice_runtime": {"max_audio_mb": 1}}
    try:
        with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
            res = client.post(
                "/v1/voice/transcribe",
                headers=admin_auth_header,
                data={"file": (BytesIO(b"x" * (2 * 1024 * 1024)), "too-large.webm")},
                content_type="multipart/form-data",
            )
        assert provider_factory.return_value.transcribe.call_count == 0
    finally:
        client.application.config["AGENT_CONFIG"] = original

    assert res.status_code == 413
    assert ((res.json.get("data") or {}).get("error") or {}).get("code") == "validation.file_too_large"


def test_voice_capabilities_degraded_when_runtime_unavailable(client, admin_auth_header):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.health.side_effect = VoiceProviderError(
            code="voice.runtime_unavailable",
            message="voice runtime unavailable",
            status_code=503,
            retriable=True,
        )
        res = client.get("/v1/voice/capabilities", headers=admin_auth_header)
    assert res.status_code == 200
    data = res.json["data"]
    assert data["available"] is False
    assert data["health"]["status"] == "unavailable"


def test_provider_catalog_contains_voice_runtime_entry(client, admin_auth_header):
    with patch("agent.routes.config.providers.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.models.return_value = [{"id": "voxtral", "capabilities": ["transcription"]}]
        provider_factory.return_value.health.return_value = {"ok": True, "status": "ok"}
        res = client.get("/providers/catalog", headers=admin_auth_header)
    assert res.status_code == 200
    providers = (res.json.get("data") or {}).get("providers") or []
    voice = next(
        (item for item in providers if item.get("capabilities", {}).get("provider_type") == "local_voice_runtime"), None
    )
    assert voice is not None
    assert voice["available"] is True


def test_voice_goal_requires_explicit_approval(client, admin_auth_header):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.voice_command.return_value = {"text": "create goal", "transcript": "create goal"}
        res = client.post(
            "/v1/voice/goal",
            headers=admin_auth_header,
            data={"file": (BytesIO(b"audio"), "sample.webm"), "create_tasks": "false"},
            content_type="multipart/form-data",
        )
    assert res.status_code == 403
    assert ((res.json.get("data") or {}).get("error") or {}).get("code") == "policy_denied"


def test_voice_capabilities_blocked_when_policy_disabled(client, admin_auth_header):
    with patch("agent.routes.voice.get_exposure_policy_service") as policy_factory:
        policy_factory.return_value.evaluate_voice_access.return_value = type(
            "Decision",
            (),
            {
                "allowed": False,
                "reason": "voice_exposure_disabled",
                "auth_source": "user_jwt",
                "policy": {"emit_audit_events": False},
            },
        )()
        res = client.get("/v1/voice/capabilities", headers=admin_auth_header)
    assert res.status_code == 403
    assert ((res.json.get("data") or {}).get("error") or {}).get("code") == "policy_denied"


def test_voice_capabilities_privacy_stays_fail_closed(client, admin_auth_header):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.health.return_value = {"ok": True, "status": "ok"}
        provider_factory.return_value.models.return_value = [{"id": "voxtral"}]
        with patch("agent.routes.voice._store_audio_enabled", return_value=True):
            res = client.get("/v1/voice/capabilities", headers=admin_auth_header)
    assert res.status_code == 200
    privacy = (res.json.get("data") or {}).get("privacy") or {}
    assert privacy.get("store_audio_requested") is True
    assert privacy.get("store_audio_effective") is False
    assert privacy.get("raw_audio_persisted") is False
    assert privacy.get("raw_audio_persisted_after_request") is False
    assert privacy.get("transient_request_spooling") is True


def test_voice_transcribe_propagates_provider_error_shape(client, admin_auth_header):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.transcribe.side_effect = VoiceProviderError(
            code="voice.runtime_unavailable",
            message="voice runtime unavailable",
            status_code=503,
            retriable=True,
        )
        res = client.post(
            "/v1/voice/transcribe",
            headers=admin_auth_header,
            data={"file": (BytesIO(b"audio"), "sample.webm")},
            content_type="multipart/form-data",
        )
    assert res.status_code == 503
    error = (res.json.get("data") or {}).get("error") or {}
    assert error.get("code") == "voice.runtime_unavailable"
    assert error.get("retriable") is True


def test_voice_command_passes_parsed_context_to_provider(client, admin_auth_header):
    with (
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
        patch("agent.routes.voice.log_audit") as audit,
    ):
        provider_factory.return_value.voice_command.return_value = {
            "text": "create repo health goal",
            "transcript": "create repo health goal",
            "tool_intent": {"type": "voice_command", "confidence": 0.8},
        }
        res = client.post(
            "/v1/voice/command",
            headers=admin_auth_header,
            data={
                "file": (BytesIO(b"audio"), "sample.webm"),
                "command_context": '{"scope":"release","priority":"high"}',
            },
            content_type="multipart/form-data",
        )
        _, kwargs = provider_factory.return_value.voice_command.call_args
    assert res.status_code == 200
    assert kwargs.get("context") == {"scope": "release", "priority": "high"}
    event_name, details = audit.call_args.args
    assert event_name == "voice_command"
    assert details["actor"]
    assert details["tenant_id"]
    assert details["operation"] == "command"
    assert details["policy_decision"] == "allowed"
    assert details["request_id"] == details["audit_id"]


def test_voice_goal_rejects_empty_transcript_even_if_approved(client, admin_auth_header):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.voice_command.return_value = {"text": "", "transcript": ""}
        res = client.post(
            "/v1/voice/goal",
            headers=admin_auth_header,
            data={"file": (BytesIO(b"audio"), "sample.webm"), "approved": "true"},
            content_type="multipart/form-data",
        )
    assert res.status_code == 422
    assert ((res.json.get("data") or {}).get("error") or {}).get("code") == "voice.empty_transcript"
    with Session(engine) as session:
        tasks = session.exec(select(TaskDB)).all()
    assert len(tasks) == 1
    assert tasks[0].status == "failed"


def test_voice_command_uses_hub_task_encrypted_artifact_and_idempotent_replay(
    client,
    admin_auth_header,
):
    headers = {**admin_auth_header, "Idempotency-Key": "voice-command-hub-path"}
    runtime_result = {
        "provider": "voice-runtime",
        "model": "local-command",
        "transcript": "create a private goal",
        "tool_intent": {"type": "voice_command", "confidence": 0.9},
    }
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.voice_command.return_value = runtime_result
        first = client.post(
            "/v1/voice/command",
            headers=headers,
            data={"file": (BytesIO(b"command audio"), "command.webm")},
            content_type="multipart/form-data",
        )
        replay = client.post(
            "/v1/voice/command",
            headers=headers,
            data={"file": (BytesIO(b"command audio"), "command.webm")},
            content_type="multipart/form-data",
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    first_data = first.get_json()["data"]
    replay_data = replay.get_json()["data"]
    assert first_data["idempotent_replay"] is False
    assert replay_data["idempotent_replay"] is True
    assert replay_data["task_id"] == first_data["task_id"]
    assert replay_data["result_ref"] == first_data["result_ref"]
    assert provider_factory.return_value.voice_command.call_count == 1
    with Session(engine) as session:
        task = session.get(TaskDB, first_data["task_id"])
        artifact = session.get(VoiceResultArtifactDB, first_data["result_ref"])
    assert task is not None
    assert task.status == "completed"
    assert task.last_output == first_data["result_ref"]
    assert task.parent_task_id is None
    assert artifact is not None
    assert "create a private goal" not in artifact.payload_ciphertext


def test_shared_hub_voice_helper_recovers_artifact_after_claim_completion_crash(
    client,
    admin_auth_header,
):
    idempotency_key = "voice-command-artifact-crash"
    headers = {**admin_auth_header, "Idempotency-Key": idempotency_key}
    runtime_result = {
        "provider": "voice-runtime",
        "model": "local-command",
        "transcript": "recover this command",
        "tool_intent": {"type": "voice_command", "confidence": 0.9},
    }
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.voice_command.return_value = runtime_result
        with patch(
            "agent.services.voice_idempotency_service.VoiceIdempotencyService.complete",
            side_effect=SystemExit("simulated claim completion crash"),
        ):
            with pytest.raises(SystemExit, match="claim completion crash"):
                client.post(
                    "/v1/voice/command",
                    headers=headers,
                    data={"file": (BytesIO(b"command recovery audio"), "command.webm")},
                    content_type="multipart/form-data",
                )

        with Session(engine) as session:
            claim = session.exec(
                select(VoiceGovernanceIdempotencyDB).where(VoiceGovernanceIdempotencyDB.operation == "voice.command")
            ).one()
            envelope = session.exec(
                select(VoiceResultArtifactDB).where(VoiceResultArtifactDB.artifact_kind == "result_envelope")
            ).one()
            task = session.exec(select(TaskDB)).one()
            assert claim.state == "pending"
            assert task.status == "completed"
            claim_id = claim.id
            envelope_id = envelope.id
            task_id = task.id
            claim.lease_expires_at = time.time() - 1
            session.add(claim)
            session.commit()

        replay = client.post(
            "/v1/voice/command",
            headers=headers,
            data={"file": (BytesIO(b"command recovery audio"), "command.webm")},
            content_type="multipart/form-data",
        )

    assert replay.status_code == 200
    replay_data = replay.get_json()["data"]
    assert replay_data["idempotent_replay"] is True
    assert replay_data["result_ref"] == envelope_id
    assert replay_data["task_id"] == task_id
    assert replay_data["transcript"] == "recover this command"
    assert provider_factory.return_value.voice_command.call_count == 1
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


def test_voice_command_admission_rejection_never_calls_provider_or_creates_task(
    client,
    admin_auth_header,
):
    with (
        patch("agent.routes.voice.get_voice_admission_service") as admission_factory,
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
    ):
        admission_factory.return_value.acquire.side_effect = VoiceGovernanceError(
            code="voice_admission.queue_full",
            message="Hub voice delegation queue is full",
            status_code=429,
        )
        response = client.post(
            "/v1/voice/command",
            headers=admin_auth_header,
            data={"file": (BytesIO(b"command audio"), "command.webm")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 429
    assert response.get_json()["data"]["error"]["code"] == "voice_admission.queue_full"
    provider_factory.return_value.voice_command.assert_not_called()
    with Session(engine) as session:
        assert session.exec(select(TaskDB)).all() == []


def test_voice_command_provider_failure_marks_hub_task_failed(client, admin_auth_header):
    with patch("agent.routes.voice.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.voice_command.side_effect = VoiceProviderError(
            code="voice.runtime_unavailable",
            message="voice runtime unavailable",
            status_code=503,
            retriable=True,
        )
        response = client.post(
            "/v1/voice/command",
            headers=admin_auth_header,
            data={"file": (BytesIO(b"command audio"), "command.webm")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 503
    with Session(engine) as session:
        tasks = session.exec(select(TaskDB)).all()
    assert len(tasks) == 1
    assert tasks[0].status == "failed"
    assert tasks[0].status_reason_code == "voice_runtime_failed"


def test_voice_goal_uses_existing_goal_policy_path_and_replays_once(client, admin_auth_header):
    headers = {**admin_auth_header, "Idempotency-Key": "voice-goal-hub-path"}
    internal_response = SimpleNamespace(
        status_code=201,
        get_json=lambda silent=True: {"data": {"goal": {"id": "goal-from-policy-path"}}},
    )
    with (
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
        patch.object(client.application, "test_client") as internal_client_factory,
    ):
        provider_factory.return_value.voice_command.return_value = {
            "provider": "voice-runtime",
            "model": "local-command",
            "transcript": "create the governed goal",
        }
        internal_client_factory.return_value.post.return_value = internal_response
        first = client.post(
            "/v1/voice/goal",
            headers=headers,
            data={
                "file": (BytesIO(b"goal audio"), "goal.webm"),
                "approved": "true",
                "create_tasks": "false",
            },
            content_type="multipart/form-data",
        )
        replay = client.post(
            "/v1/voice/goal",
            headers=headers,
            data={
                "file": (BytesIO(b"goal audio"), "goal.webm"),
                "approved": "true",
                "create_tasks": "false",
            },
            content_type="multipart/form-data",
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    first_data = first.get_json()["data"]
    replay_data = replay.get_json()["data"]
    assert first_data["goal_id"] == "goal-from-policy-path"
    assert replay_data["goal_id"] == first_data["goal_id"]
    assert replay_data["task_id"] == first_data["task_id"]
    assert replay_data["result_ref"] == first_data["result_ref"]
    assert replay_data["idempotent_replay"] is True
    assert provider_factory.return_value.voice_command.call_count == 1
    assert internal_client_factory.return_value.post.call_count == 1
    policy_call = internal_client_factory.return_value.post.call_args
    assert policy_call.args == ("/goals",)
    assert policy_call.kwargs["json"]["goal"] == "create the governed goal"
    assert policy_call.kwargs["json"]["source"] == "voice"
    with Session(engine) as session:
        task = session.get(TaskDB, first_data["task_id"])
    assert task is not None
    assert task.status == "completed"
    assert task.last_output == first_data["result_ref"]


def test_voice_goal_reuses_recovered_transcript_before_resuming_goal_policy(
    client,
    admin_auth_header,
):
    idempotency_key = "voice-goal-artifact-crash"
    headers = {**admin_auth_header, "Idempotency-Key": idempotency_key}
    internal_response = SimpleNamespace(
        status_code=201,
        get_json=lambda silent=True: {"data": {"goal": {"id": "recovered-goal"}}},
    )
    artifact_service = get_voice_result_artifact_service()
    original_create = artifact_service.create

    def create_then_crash(*args, **kwargs):
        original_create(*args, **kwargs)
        raise SystemExit("simulated goal artifact crash")

    with (
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
        patch.object(client.application, "test_client") as internal_client_factory,
    ):
        provider_factory.return_value.voice_command.return_value = {
            "provider": "voice-runtime",
            "transcript": "create recovered goal",
        }
        internal_client_factory.return_value.post.return_value = internal_response
        with patch.object(artifact_service, "create", side_effect=create_then_crash):
            with pytest.raises(SystemExit, match="goal artifact crash"):
                client.post(
                    "/v1/voice/goal",
                    headers=headers,
                    data={
                        "file": (BytesIO(b"goal recovery audio"), "goal.webm"),
                        "approved": "true",
                    },
                    content_type="multipart/form-data",
                )

        with Session(engine) as session:
            claim = session.exec(
                select(VoiceGovernanceIdempotencyDB).where(VoiceGovernanceIdempotencyDB.operation == "voice.goal")
            ).one()
            task = session.exec(select(TaskDB)).one()
            assert claim.state == "pending"
            assert task.status == "in_progress"
            claim_id = claim.id
            task_id = task.id
            claim.lease_expires_at = time.time() - 1
            session.add(claim)
            session.commit()

        replay = client.post(
            "/v1/voice/goal",
            headers=headers,
            data={
                "file": (BytesIO(b"goal recovery audio"), "goal.webm"),
                "approved": "true",
            },
            content_type="multipart/form-data",
        )

    assert replay.status_code == 200
    replay_data = replay.get_json()["data"]
    assert replay_data["goal_id"] == "recovered-goal"
    assert replay_data["idempotent_replay"] is True
    assert replay_data["task_id"] == task_id
    assert provider_factory.return_value.voice_command.call_count == 1
    assert internal_client_factory.return_value.post.call_count == 1
    with Session(engine) as session:
        recovered_claim = session.get(VoiceGovernanceIdempotencyDB, claim_id)
        recovered_task = session.get(TaskDB, task_id)
    assert recovered_claim is not None
    assert recovered_claim.state == "completed"
    assert recovered_claim.result_metadata["goal_id"] == "recovered-goal"
    assert recovered_task is not None
    assert recovered_task.status == "completed"
    assert recovered_task.last_output == replay_data["result_ref"]


def test_voice_goal_policy_rejection_marks_deferred_hub_task_failed(client, admin_auth_header):
    internal_response = SimpleNamespace(
        status_code=412,
        get_json=lambda silent=True: {"message": "goal_precondition_failed"},
    )
    with (
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
        patch.object(client.application, "test_client") as internal_client_factory,
    ):
        provider_factory.return_value.voice_command.return_value = {
            "provider": "voice-runtime",
            "transcript": "create a denied goal",
        }
        internal_client_factory.return_value.post.return_value = internal_response
        response = client.post(
            "/v1/voice/goal",
            headers=admin_auth_header,
            data={
                "file": (BytesIO(b"denied goal audio"), "goal.webm"),
                "approved": "true",
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 412
    with Session(engine) as session:
        tasks = session.exec(select(TaskDB)).all()
    assert len(tasks) == 1
    assert tasks[0].status == "failed"
    assert tasks[0].last_output is None
    assert tasks[0].status_reason_code == "voice_runtime_failed"


def test_voice_goal_policy_exception_marks_deferred_hub_task_failed(client, admin_auth_header):
    with (
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
        patch.object(client.application, "test_client") as internal_client_factory,
    ):
        provider_factory.return_value.voice_command.return_value = {
            "provider": "voice-runtime",
            "transcript": "create a failing goal",
        }
        internal_client_factory.return_value.post.side_effect = RuntimeError("goal persistence failed")
        response = client.post(
            "/v1/voice/goal",
            headers=admin_auth_header,
            data={
                "file": (BytesIO(b"failing goal audio"), "goal.webm"),
                "approved": "true",
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 500
    with Session(engine) as session:
        tasks = session.exec(select(TaskDB)).all()
    assert len(tasks) == 1
    assert tasks[0].status == "failed"
    assert tasks[0].last_output is None
