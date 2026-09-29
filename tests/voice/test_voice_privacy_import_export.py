from __future__ import annotations

import threading
from unittest.mock import patch

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import VoiceFeedbackDB
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_personalization_service import get_voice_personalization_service


def _grant(client, headers, *, key):
    return client.put(
        "/v1/voice/consents/profile-portable",
        headers={**headers, "Idempotency-Key": key},
        json={
            "granted": True,
            "categories": ["preferences", "text_corrections", "vocabulary"],
            "retention_days": 30,
        },
    )


def test_explicit_import_and_full_delete_are_tenant_scoped(client, user_auth_header):
    assert _grant(client, user_auth_header, key="portable-consent-1").status_code == 200
    import_payload = {
        "schema_version": "voice-personalization.v1",
        "profile_id": "profile-portable",
        "version": 1,
        "items": [
            {
                "kind": "vocabulary",
                "source_text": None,
                "target_text": "Ananta",
                "metadata": {"language": "de"},
            },
            {
                "kind": "substitution",
                "source_text": "Anantha",
                "target_text": "Ananta",
                "metadata": {"reason_code": "confirmed_spelling"},
            },
        ],
    }
    imported = client.post(
        "/v1/voice/personalization/profile-portable/import",
        headers={**user_auth_header, "Idempotency-Key": "portable-import-1"},
        json=import_payload,
    )
    exported = client.get(
        "/v1/voice/personalization/profile-portable/export",
        headers=user_auth_header,
    )

    assert imported.status_code == 200
    assert imported.get_json()["data"]["import"]["imported_count"] == 2
    assert len(exported.get_json()["data"]["personalization"]["items"]) == 2

    deleted = client.delete(
        "/v1/voice/privacy/profile-portable",
        headers={**user_auth_header, "Idempotency-Key": "portable-delete-1"},
        json={"confirmed": True},
    )
    replay = client.delete(
        "/v1/voice/privacy/profile-portable",
        headers={**user_auth_header, "Idempotency-Key": "portable-delete-1"},
        json={"confirmed": True},
    )
    after = client.get(
        "/v1/voice/personalization/profile-portable/export",
        headers=user_auth_header,
    )
    consent = client.get("/v1/voice/consents/profile-portable", headers=user_auth_header)

    assert deleted.status_code == 200
    assert deleted.get_json()["data"]["deletion"]["snapshots_revoked"] is True
    assert replay.get_json()["data"]["deletion"]["idempotent_replay"] is True
    assert after.get_json()["data"]["personalization"]["items"] == []
    assert consent.get_json()["data"]["consent"]["granted"] is False


def test_import_requires_active_consent(client, user_auth_header):
    response = client.post(
        "/v1/voice/personalization/profile-portable/import",
        headers={**user_auth_header, "Idempotency-Key": "portable-import-denied"},
        json={
            "schema_version": "voice-personalization.v1",
            "profile_id": "profile-portable",
            "items": [],
        },
    )

    assert response.status_code == 403
    assert response.get_json()["data"]["error"]["code"] == "voice_consent.required"


def test_import_recovery_after_idempotency_completion_failure_does_not_duplicate_items(
    client,
    user_auth_header,
):
    assert _grant(client, user_auth_header, key="atomic-import-consent").status_code == 200
    payload = {
        "schema_version": "voice-personalization.v1",
        "profile_id": "profile-portable",
        "items": [
            {"kind": "vocabulary", "source_text": None, "target_text": "Ananta", "metadata": {}},
            {
                "kind": "substitution",
                "source_text": "Anantha",
                "target_text": "Ananta",
                "metadata": {},
            },
        ],
    }
    service = get_voice_personalization_service()
    with patch.object(
        service._idempotency,
        "complete",
        side_effect=[RuntimeError("simulated completion crash")],
    ):
        failed = client.post(
            "/v1/voice/personalization/profile-portable/import",
            headers={**user_auth_header, "Idempotency-Key": "atomic-import"},
            json=payload,
        )
    assert failed.status_code == 500

    recovered = client.post(
        "/v1/voice/personalization/profile-portable/import",
        headers={**user_auth_header, "Idempotency-Key": "atomic-import"},
        json=payload,
    )

    assert recovered.status_code == 200
    assert recovered.get_json()["data"]["import"]["imported_count"] == 2
    with Session(engine) as session:
        feedback = session.exec(
            select(VoiceFeedbackDB).where(
                VoiceFeedbackDB.profile_id == "profile-portable",
                VoiceFeedbackDB.source_review_id.startswith("import-"),
            )
        ).all()
    assert len(feedback) == 2


def test_personalization_reset_recovers_as_replay_after_post_commit_crash(
    client,
    user_auth_header,
):
    assert _grant(client, user_auth_header, key="reset-atomic-consent").status_code == 200
    imported = client.post(
        "/v1/voice/personalization/profile-portable/import",
        headers={**user_auth_header, "Idempotency-Key": "reset-atomic-import"},
        json={
            "schema_version": "voice-personalization.v1",
            "profile_id": "profile-portable",
            "items": [
                {
                    "kind": "vocabulary",
                    "source_text": None,
                    "target_text": "Ananta",
                    "metadata": {},
                }
            ],
        },
    )
    assert imported.status_code == 200
    service = get_voice_personalization_service()
    original_reset = service._repository.reset

    def commit_then_crash(*args, **kwargs):
        original_reset(*args, **kwargs)
        raise RuntimeError("simulated process crash after atomic reset")

    headers = {**user_auth_header, "Idempotency-Key": "reset-atomic-key"}
    with patch.object(service._repository, "reset", side_effect=commit_then_crash):
        failed = client.delete("/v1/voice/personalization/profile-portable", headers=headers)
    recovered = client.delete("/v1/voice/personalization/profile-portable", headers=headers)

    assert failed.status_code == 500
    assert recovered.status_code == 200
    result = recovered.get_json()["data"]["reset"]
    assert result["idempotent_replay"] is True
    assert result["deleted_count"] == 1
    assert result["version"] == 2
    with Session(engine) as session:
        feedback = session.exec(
            select(VoiceFeedbackDB).where(VoiceFeedbackDB.profile_id == "profile-portable")
        ).all()
    assert feedback == []


def test_personalization_reset_rolls_back_when_idempotency_fence_is_lost(
    client,
    user_auth_header,
):
    assert _grant(client, user_auth_header, key="reset-fence-consent").status_code == 200
    imported = client.post(
        "/v1/voice/personalization/profile-portable/import",
        headers={**user_auth_header, "Idempotency-Key": "reset-fence-import"},
        json={
            "schema_version": "voice-personalization.v1",
            "profile_id": "profile-portable",
            "items": [
                {
                    "kind": "vocabulary",
                    "source_text": None,
                    "target_text": "Ananta",
                    "metadata": {},
                }
            ],
        },
    )
    assert imported.status_code == 200
    service = get_voice_personalization_service()
    headers = {**user_auth_header, "Idempotency-Key": "reset-fence-key"}

    with patch.object(
        service._repository,
        "_complete_idempotency_claim",
        side_effect=RuntimeError("simulated stale reset fence"),
    ):
        failed = client.delete("/v1/voice/personalization/profile-portable", headers=headers)

    with Session(engine) as session:
        feedback_after_failure = session.exec(
            select(VoiceFeedbackDB).where(VoiceFeedbackDB.profile_id == "profile-portable")
        ).all()
    retried = client.delete("/v1/voice/personalization/profile-portable", headers=headers)

    assert failed.status_code == 500
    assert len(feedback_after_failure) == 1
    assert retried.status_code == 200
    result = retried.get_json()["data"]["reset"]
    assert result["idempotent_replay"] is False
    assert result["deleted_count"] == 1
    assert result["version"] == 2


def test_concurrent_personalization_reset_retries_delete_once(
    app,
    client,
    user_auth_header,
):
    assert _grant(client, user_auth_header, key="reset-concurrent-consent").status_code == 200
    imported = client.post(
        "/v1/voice/personalization/profile-portable/import",
        headers={**user_auth_header, "Idempotency-Key": "reset-concurrent-import"},
        json={
            "schema_version": "voice-personalization.v1",
            "profile_id": "profile-portable",
            "items": [
                {
                    "kind": "vocabulary",
                    "source_text": None,
                    "target_text": "Ananta",
                    "metadata": {},
                }
            ],
        },
    )
    assert imported.status_code == 200
    service = get_voice_personalization_service()
    principal = VoicePrincipal(tenant_id="testuser", subject="testuser")
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def reset() -> None:
        try:
            with app.app_context():
                barrier.wait(timeout=2)
                results.append(
                    service.reset(
                        principal,
                        profile_id="profile-portable",
                        idempotency_key="reset-concurrent-key",
                    )
                )
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=reset) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert errors == []
    assert len(results) == 2
    assert sorted(result["idempotent_replay"] for result in results) == [False, True]
    assert {result["deleted_count"] for result in results} == {1}
    assert len({result["version"] for result in results}) == 1


def test_negative_feedback_deterministically_retracts_profile_rule(client, user_auth_header):
    _grant(client, user_auth_header, key="negative-consent")
    response = client.post(
        "/v1/voice/personalization/profile-portable/import",
        headers={**user_auth_header, "Idempotency-Key": "negative-import"},
        json={
            "schema_version": "voice-personalization.v1",
            "profile_id": "profile-portable",
            "items": [
                {
                    "kind": "substitution",
                    "source_text": "falsch",
                    "target_text": "richtig",
                    "metadata": {},
                },
                {
                    "kind": "negative",
                    "source_text": "falsch",
                    "target_text": "richtig",
                    "metadata": {},
                },
            ],
        },
    )
    snapshot = client.get(
        "/v1/voice/personalization/profile-portable/snapshot",
        headers=user_auth_header,
    )

    assert response.status_code == 200
    payload = snapshot.get_json()["data"]["snapshot"]
    assert payload["substitutions"] == []
    assert payload["negative_examples"] == [{"source": "falsch", "target": "richtig"}]


def test_fine_tuning_export_is_only_an_explicit_non_training_hub_task(client, user_auth_header):
    _grant(client, user_auth_header, key="training-export-consent")
    imported = client.post(
        "/v1/voice/personalization/profile-portable/import",
        headers={**user_auth_header, "Idempotency-Key": "training-export-import"},
        json={
            "schema_version": "voice-personalization.v1",
            "profile_id": "profile-portable",
            "items": [
                {
                    "kind": "vocabulary",
                    "source_text": None,
                    "target_text": "Ananta",
                    "metadata": {"language": "de"},
                }
            ],
        },
    )
    denied = client.post(
        "/v1/voice/personalization/profile-portable/fine-tuning-export-tasks",
        headers={**user_auth_header, "Idempotency-Key": "training-export-denied"},
        json={"confirmed": False},
    )
    approved_headers = {**user_auth_header, "Idempotency-Key": "training-export-approved"}
    approved = client.post(
        "/v1/voice/personalization/profile-portable/fine-tuning-export-tasks",
        headers=approved_headers,
        json={"confirmed": True, "purpose": "private spelling model", "license": "private"},
    )
    replay = client.post(
        "/v1/voice/personalization/profile-portable/fine-tuning-export-tasks",
        headers=approved_headers,
        json={"confirmed": True, "purpose": "private spelling model", "license": "private"},
    )

    assert imported.status_code == 200
    assert denied.status_code == 403
    assert approved.status_code == 201
    approved_data = approved.get_json()["data"]
    task_id = approved_data["task_id"]
    assert task_id.startswith("voice-training-export-")
    assert approved_data["starts_training"] is False
    assert approved_data["item_count"] == 1
    assert replay.get_json()["data"]["task_id"] == task_id
    assert replay.get_json()["data"]["artifact_ref"] == approved_data["artifact_ref"]
    assert replay.get_json()["data"]["idempotent_replay"] is True

    artifact = client.get(
        (
            "/v1/voice/personalization/profile-portable/fine-tuning-exports/"
            f"{approved_data['artifact_ref']}"
        ),
        headers=user_auth_header,
    )
    payload = artifact.get_json()["data"]["training_export"]["export"]
    assert artifact.status_code == 200
    assert payload["schema_version"] == "ananta.voice-training-export.v1"
    assert payload["purpose"] == "private spelling model"
    assert payload["license"] == "private"
    assert payload["consent"]["categories"] == ["preferences", "text_corrections", "vocabulary"]
    assert payload["provenance"]["origin"] == "explicit_user_approved_hub_task"
    assert payload["deletion"] == {"profile_id": "profile-portable", "delete_with_profile": True}
    assert payload["items"][0]["target_text"] == "Ananta"
    assert payload["starts_training"] is False

    with client.application.app_context():
        from agent.repository import task_repo

        task = task_repo.get_by_id(task_id)
    assert task is not None
    assert task.status == "completed"
    assert task.verification_status["voice_training_export"]["starts_training"] is False
