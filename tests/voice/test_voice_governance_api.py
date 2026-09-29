from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from agent.auth import generate_token
from agent.config import settings
from agent.database import engine
from agent.db_models import (
    AuditLogDB,
    VoiceConsentDB,
    VoiceFeedbackDB,
    VoiceGovernanceIdempotencyDB,
    VoiceResultArtifactDB,
    VoiceReviewDB,
    VoiceRuntimeCleanupDB,
)
from agent.repositories.voice_result_artifact import VoiceResultArtifactRepository
from agent.services.voice_consent_service import get_voice_consent_service
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_result_artifact_service import get_voice_result_artifact_service
from agent.services.voice_review_service import get_voice_review_service
from agent.services.voice_runtime_cleanup_service import get_voice_runtime_cleanup_service


@pytest.fixture(autouse=True)
def _privacy_cache_gc():
    with patch.object(
        get_voice_runtime_cleanup_service(),
        "_restricted_cache_gc",
        return_value=None,
    ) as cache_gc:
        yield cache_gc


def _data(response, key: str):
    payload = response.get_json()
    return payload["data"][key]


def _consent(client, headers, *, key: str = "consent-1", granted: bool = True):
    return client.put(
        "/v1/voice/consents/profile-a",
        headers={**headers, "Idempotency-Key": key},
        json={
            "granted": granted,
            "categories": ["preferences", "text_corrections", "vocabulary"] if granted else [],
            "retention_days": 90,
        },
    )


def _review(client, headers, *, key: str = "review-1"):
    artifact = get_voice_result_artifact_service().create(
        VoicePrincipal(tenant_id="testuser", subject="testuser"),
        request_hash=f"review-helper-{key}",
        profile_id="profile-a",
        result={
            "text": "review fixture",
            "candidates": [
                {"candidate_id": "candidate-a", "text": "candidate a"},
                {"candidate_id": "candidate-b", "text": "candidate b"},
            ],
        },
    )
    return client.post(
        "/v1/voice/reviews",
        headers={**headers, "Idempotency-Key": key},
        json={
            "profile_id": "profile-a",
            "session_id": "session-1",
            "result_ref": artifact["id"],
            "candidate_ids": ["candidate-a", "candidate-b"],
        },
    )


def _accept_review(client, headers, review_id: str, *, key: str = "decision-1"):
    return client.post(
        f"/v1/voice/reviews/{review_id}/decision",
        headers={**headers, "Idempotency-Key": key},
        json={
            "decision": "accept",
            "expected_version": 1,
            "selected_candidate_id": "candidate-a",
        },
    )


def test_voice_governance_routes_require_user_auth(client):
    response = client.get("/v1/voice/consents/profile-a")

    assert response.status_code == 401


def test_voice_governance_mutations_require_idempotency_key(client, user_auth_header):
    response = client.post(
        "/v1/voice/reviews",
        headers=user_auth_header,
        json={"result_ref": "result-1", "candidate_ids": ["candidate-a"]},
    )

    assert response.status_code == 400
    assert _data(response, "error")["code"] == "voice_governance.idempotency_key_required"


def test_consent_is_scoped_idempotent_and_conflict_safe(client, user_auth_header):
    first = _consent(client, user_auth_header)
    replay = _consent(client, user_auth_header)
    conflict = client.put(
        "/v1/voice/consents/profile-a",
        headers={**user_auth_header, "Idempotency-Key": "consent-1"},
        json={"granted": False, "categories": [], "retention_days": 90},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    first_consent = _data(first, "consent")
    replay_consent = _data(replay, "consent")
    assert first_consent["granted"] is True
    assert first_consent["version"] == 1
    assert first_consent["idempotent_replay"] is False
    assert replay_consent["version"] == 1
    assert replay_consent["idempotent_replay"] is True
    assert conflict.status_code == 409
    assert _data(conflict, "error")["code"] == "voice_governance.idempotency_conflict"


def test_consent_post_commit_crash_replays_without_version_increment() -> None:
    principal = VoicePrincipal(tenant_id="consent-crash-tenant", subject="consent-crash-owner")
    service = get_voice_consent_service()
    repository = service._repository
    set_state = repository.set_state

    def commit_then_crash(*args, **kwargs):
        set_state(*args, **kwargs)
        raise RuntimeError("simulated consent post-commit crash")

    mutation = {
        "profile_id": "consent-crash-profile",
        "granted": True,
        "categories": ["preferences", "vocabulary"],
        "retention_days": 90,
        "idempotency_key": "consent-post-commit-crash",
    }
    with patch.object(repository, "set_state", side_effect=commit_then_crash):
        with pytest.raises(RuntimeError, match="consent post-commit crash"):
            service.set(principal, **mutation)

    replay = service.set(principal, **mutation)

    assert replay["idempotent_replay"] is True
    assert replay["version"] == 1
    with Session(engine) as session:
        consents = session.exec(
            select(VoiceConsentDB).where(
                VoiceConsentDB.tenant_id == principal.tenant_id,
                VoiceConsentDB.owner_subject == principal.subject,
                VoiceConsentDB.profile_id == "consent-crash-profile",
            )
        ).all()
        claim = session.exec(
            select(VoiceGovernanceIdempotencyDB).where(
                VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                VoiceGovernanceIdempotencyDB.operation
                == "voice_consent.set:consent-crash-profile",
            )
        ).one()
    assert len(consents) == 1
    assert consents[0].version == 1
    assert claim.state == "completed"
    assert claim.idempotency_key != "consent-post-commit-crash"
    assert claim.result_metadata["consent"]["version"] == 1


def test_consent_atomic_mutation_rolls_back_for_wrong_owner_or_lease() -> None:
    principal = VoicePrincipal(tenant_id="consent-fence-tenant", subject="consent-fence-owner")
    other = VoicePrincipal(tenant_id="consent-fence-tenant", subject="other-owner")
    service = get_voice_consent_service()
    claim = service._idempotency.begin(
        principal,
        operation="voice_consent.set:consent-fence-profile",
        idempotency_key="consent-fence-key",
        payload={"mutation": "fixture"},
    )
    assert claim.lease_token is not None

    for mutation_principal, lease_token in (
        (other, claim.lease_token),
        (principal, claim.lease_token + 1),
    ):
        with pytest.raises(VoiceGovernanceError) as error:
            service._repository.set_state(
                mutation_principal,
                profile_id="consent-fence-profile",
                granted=True,
                categories=["preferences"],
                retention_days=90,
                idempotency_record_id=claim.record_id,
                idempotency_lease_token=lease_token,
                result_builder=lambda consent: {"version": consent.version},
            )
        assert error.value.code == "voice_governance.stale_idempotency_claim"
        assert service._repository.get(mutation_principal, "consent-fence-profile") is None

    service._idempotency.abandon(claim)


def test_review_decision_is_tenant_scoped_versioned_and_idempotent(client, user_auth_header):
    created = _review(client, user_auth_header)
    review = _data(created, "review")

    other_token = generate_token(
        {"sub": "other-user", "tenant_id": "other-tenant", "role": "user"},
        settings.secret_key,
    )
    hidden = client.get(
        f"/v1/voice/reviews/{review['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    invalid_candidate = client.post(
        f"/v1/voice/reviews/{review['id']}/decision",
        headers={**user_auth_header, "Idempotency-Key": "wrong-candidate"},
        json={
            "decision": "accept",
            "expected_version": 1,
            "selected_candidate_id": "candidate-other",
        },
    )
    decided = _accept_review(client, user_auth_header, review["id"])
    replay = _accept_review(client, user_auth_header, review["id"])

    assert created.status_code == 201
    assert review["state"] == "pending"
    assert hidden.status_code == 404
    assert invalid_candidate.status_code == 422
    assert decided.status_code == 200
    decided_review = _data(decided, "review")
    assert decided_review["state"] == "accepted"
    assert decided_review["version"] == 2
    assert decided_review["decision_artifact_ref"].startswith("voice-result-")
    assert _data(replay, "review")["idempotent_replay"] is True
    assert _data(replay, "review")["decision_artifact_ref"] == decided_review["decision_artifact_ref"]
    with Session(engine) as session:
        artifact = session.get(VoiceResultArtifactDB, decided_review["decision_artifact_ref"])
    assert artifact is not None
    assert artifact.artifact_kind == "review_decision"
    assert artifact.candidate_ids == ["candidate-a", "candidate-b"]


def test_parallel_review_decisions_have_one_atomic_winner(client, user_auth_header):
    review = _data(_review(client, user_auth_header, key="parallel-review"), "review")
    principal = VoicePrincipal(tenant_id="testuser", subject="testuser")
    service = get_voice_review_service()

    def decide(key: str, candidate_id: str):
        try:
            return service.decide(
                principal,
                review_id=review["id"],
                decision="accept",
                expected_version=1,
                selected_candidate_id=candidate_id,
                correction_text=None,
                idempotency_key=key,
            )
        except VoiceGovernanceError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda values: decide(*values),
                (("parallel-decision-a", "candidate-a"), ("parallel-decision-b", "candidate-b")),
            )
        )

    successes = [item for item in outcomes if isinstance(item, dict)]
    failures = [item for item in outcomes if isinstance(item, VoiceGovernanceError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code in {"voice_review.version_conflict", "voice_review.already_decided"}
    with Session(engine) as session:
        stored = session.get(VoiceReviewDB, review["id"])
        artifacts = session.exec(
            select(VoiceResultArtifactDB).where(
                VoiceResultArtifactDB.artifact_kind == "review_decision",
                VoiceResultArtifactDB.parent_artifact_id == review["result_ref"],
            )
        ).all()
    assert stored is not None
    assert stored.version == 2
    assert len(artifacts) == 1


def test_review_create_replays_single_committed_review_after_post_commit_crash(client, user_auth_header):
    principal = VoicePrincipal(tenant_id="testuser", subject="testuser")
    artifact = get_voice_result_artifact_service().create(
        principal,
        request_hash="review-create-crash-window",
        profile_id="profile-a",
        result={"text": "fixture", "candidates": [{"candidate_id": "candidate-a"}]},
    )
    service = get_voice_review_service()
    repository = service._decision_repository
    create = repository.create

    def commit_then_crash(*args, **kwargs):
        create(*args, **kwargs)
        raise RuntimeError("simulated post-commit process crash")

    mutation = {
        "profile_id": "profile-a",
        "session_id": "session-crash-create",
        "result_ref": artifact["id"],
        "candidate_ids": ["candidate-a"],
        "idempotency_key": "review-create-crash",
    }
    with patch.object(repository, "create", side_effect=commit_then_crash):
        with pytest.raises(RuntimeError, match="post-commit process crash"):
            service.create(principal, **mutation)

    replay = service.create(principal, **mutation)

    assert replay["idempotent_replay"] is True
    with Session(engine) as session:
        reviews = session.exec(
            select(VoiceReviewDB).where(
                VoiceReviewDB.tenant_id == principal.tenant_id,
                VoiceReviewDB.owner_subject == principal.subject,
                VoiceReviewDB.result_ref == artifact["id"],
            )
        ).all()
        claim = session.exec(
            select(VoiceGovernanceIdempotencyDB).where(
                VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                VoiceGovernanceIdempotencyDB.operation == "voice_review.create",
            )
        ).one()
    assert [review.id for review in reviews] == [replay["id"]]
    assert claim.state == "completed"
    assert claim.result_metadata == {"review_id": replay["id"]}


def test_review_decision_replays_committed_artifact_after_post_commit_crash(client, user_auth_header):
    principal = VoicePrincipal(tenant_id="testuser", subject="testuser")
    review = _data(_review(client, user_auth_header, key="decision-crash-review"), "review")
    service = get_voice_review_service()
    repository = service._decision_repository
    decide = repository.decide

    def commit_then_crash(*args, **kwargs):
        decide(*args, **kwargs)
        raise RuntimeError("simulated post-commit process crash")

    mutation = {
        "review_id": review["id"],
        "decision": "accept",
        "expected_version": 1,
        "selected_candidate_id": "candidate-a",
        "correction_text": None,
        "idempotency_key": "review-decision-crash",
    }
    with patch.object(repository, "decide", side_effect=commit_then_crash):
        with pytest.raises(RuntimeError, match="post-commit process crash"):
            service.decide(principal, **mutation)

    replay = service.decide(principal, **mutation)

    assert replay["idempotent_replay"] is True
    assert replay["state"] == "accepted"
    with Session(engine) as session:
        stored = session.get(VoiceReviewDB, review["id"])
        artifacts = session.exec(
            select(VoiceResultArtifactDB).where(
                VoiceResultArtifactDB.artifact_kind == "review_decision",
                VoiceResultArtifactDB.parent_artifact_id == review["result_ref"],
            )
        ).all()
        claim = session.exec(
            select(VoiceGovernanceIdempotencyDB).where(
                VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                VoiceGovernanceIdempotencyDB.operation == f"voice_review.decide:{review['id']}",
            )
        ).one()
    assert stored is not None
    assert stored.version == 2
    assert len(artifacts) == 1
    assert claim.state == "completed"
    assert claim.result_metadata == {
        "review_id": review["id"],
        "decision_artifact_ref": stored.decision_artifact_id,
    }


def test_review_result_reference_must_belong_to_the_selected_profile(client, user_auth_header):
    principal = VoicePrincipal(tenant_id="testuser", subject="testuser")
    artifact_service = get_voice_result_artifact_service()
    artifact = artifact_service.create(
        principal,
        request_hash="review-profile-isolation",
        profile_id="profile-a",
        result={
            "text": "profile A result",
            "candidates": [{"candidate_id": "profile-a-candidate"}],
        },
    )

    assert artifact["profile_id"] == "profile-a"
    assert artifact_service.get(principal, artifact["id"])["profile_id"] == "profile-a"
    artifact_repository = VoiceResultArtifactRepository()
    assert artifact_repository.get(principal, artifact["id"], profile_id="profile-a") is not None
    assert artifact_repository.get(principal, artifact["id"], profile_id="profile-b") is None

    wrong_profile = client.post(
        "/v1/voice/reviews",
        headers={**user_auth_header, "Idempotency-Key": "review-profile-b"},
        json={
            "profile_id": "profile-b",
            "result_ref": artifact["id"],
            "candidate_ids": ["profile-a-candidate"],
        },
    )
    correct_profile = client.post(
        "/v1/voice/reviews",
        headers={**user_auth_header, "Idempotency-Key": "review-profile-a"},
        json={
            "profile_id": "profile-a",
            "result_ref": artifact["id"],
            "candidate_ids": ["profile-a-candidate"],
        },
    )

    assert wrong_profile.status_code == 422
    assert _data(wrong_profile, "error")["code"] == "voice_review.result_profile_mismatch"
    assert correct_profile.status_code == 201
    assert _data(correct_profile, "review")["profile_id"] == "profile-a"


def test_review_rejects_unverified_result_and_candidate_references(client, user_auth_header):
    response = client.post(
        "/v1/voice/reviews",
        headers={**user_auth_header, "Idempotency-Key": "unverified-review"},
        json={
            "profile_id": "profile-a",
            "result_ref": "client-invented-result",
            "candidate_ids": ["client-invented-candidate"],
        },
    )

    assert response.status_code == 404
    assert _data(response, "error")["code"] == "voice_result.not_found"


def test_manual_review_correction_is_encrypted_at_rest(client, user_auth_header):
    review = _data(_review(client, user_auth_header), "review")
    correction = "private corrected transcript"
    response = client.post(
        f"/v1/voice/reviews/{review['id']}/decision",
        headers={**user_auth_header, "Idempotency-Key": "correction-1"},
        json={
            "decision": "correct",
            "expected_version": 1,
            "selected_candidate_id": "candidate-a",
            "correction_text": correction,
        },
    )

    assert response.status_code == 200
    assert _data(response, "review")["correction_text"] == correction
    with Session(engine) as session:
        stored = session.get(VoiceReviewDB, review["id"])
        artifact = session.get(VoiceResultArtifactDB, stored.decision_artifact_id) if stored else None
    assert stored is not None
    assert correction not in str(stored.correction_ciphertext)
    assert str(stored.correction_ciphertext).startswith("enc:v1:")
    assert artifact is not None
    assert artifact.artifact_kind == "review_decision"
    assert correction not in artifact.payload_ciphertext


def test_personalization_requires_explicit_consent_and_review(client, user_auth_header):
    created = _review(client, user_auth_header)
    review_id = _data(created, "review")["id"]
    assert _accept_review(client, user_auth_header, review_id).status_code == 200

    payload = {
        "profile_id": "profile-a",
        "review_id": review_id,
        "kind": "vocabulary",
        "target_text": "AnantaName",
        "metadata": {"language": "de"},
    }
    blocked = client.post(
        "/v1/voice/personalization/feedback",
        headers={**user_auth_header, "Idempotency-Key": "feedback-1"},
        json=payload,
    )
    assert blocked.status_code == 403
    assert _data(blocked, "error")["code"] == "voice_consent.required"

    assert _consent(client, user_auth_header).status_code == 200
    created_feedback = client.post(
        "/v1/voice/personalization/feedback",
        headers={**user_auth_header, "Idempotency-Key": "feedback-1"},
        json=payload,
    )
    replay = client.post(
        "/v1/voice/personalization/feedback",
        headers={**user_auth_header, "Idempotency-Key": "feedback-1"},
        json=payload,
    )
    snapshot = client.get(
        "/v1/voice/personalization/profile-a/snapshot",
        headers=user_auth_header,
    )

    assert created_feedback.status_code == 201
    assert replay.status_code == 200
    assert _data(replay, "feedback")["idempotent_replay"] is True
    snapshot_data = _data(snapshot, "snapshot")
    assert snapshot_data["vocabulary"] == ["AnantaName"]
    assert snapshot_data["consent_granted"] is True
    assert snapshot_data["revocation_epoch"] == snapshot_data["consent_version"]
    assert snapshot_data["weights"] == {
        "preference": 0.75,
        "substitution": 1.0,
        "vocabulary": 1.0,
    }
    assert snapshot_data["persistence_owner"] == "hub"
    assert snapshot_data["runtime_persistence_allowed"] is False


def test_expired_feedback_is_excluded_from_snapshot_and_export(client, user_auth_header):
    review = _data(_review(client, user_auth_header, key="review-expiry"), "review")
    assert _accept_review(client, user_auth_header, review["id"], key="decision-expiry").status_code == 200
    assert _consent(client, user_auth_header, key="consent-expiry").status_code == 200
    response = client.post(
        "/v1/voice/personalization/feedback",
        headers={**user_auth_header, "Idempotency-Key": "feedback-expiry"},
        json={
            "profile_id": "profile-a",
            "review_id": review["id"],
            "kind": "vocabulary",
            "target_text": "Kurzlebig",
            "metadata": {},
        },
    )
    feedback_id = _data(response, "feedback")["id"]
    with Session(engine) as session:
        feedback = session.get(VoiceFeedbackDB, feedback_id)
        assert feedback is not None
        feedback.expires_at = time.time() - 1
        session.add(feedback)
        session.commit()

    snapshot = client.get("/v1/voice/personalization/profile-a/snapshot", headers=user_auth_header)
    exported = client.get("/v1/voice/personalization/profile-a/export", headers=user_auth_header)

    assert _data(snapshot, "snapshot")["vocabulary"] == []
    assert _data(exported, "personalization")["items"] == []
    with Session(engine) as session:
        assert session.get(VoiceFeedbackDB, feedback_id) is None


def test_revoke_blocks_snapshots_and_reset_removes_personalization(client, user_auth_header):
    review = _data(_review(client, user_auth_header), "review")
    assert _accept_review(client, user_auth_header, review["id"]).status_code == 200
    assert _consent(client, user_auth_header).status_code == 200
    feedback_payload = {
        "profile_id": "profile-a",
        "review_id": review["id"],
        "kind": "substitution",
        "source_text": "an ander",
        "target_text": "Ananta",
        "metadata": {},
    }
    feedback = client.post(
        "/v1/voice/personalization/feedback",
        headers={**user_auth_header, "Idempotency-Key": "feedback-reset"},
        json=feedback_payload,
    )
    assert feedback.status_code == 201

    revoked = _consent(client, user_auth_header, key="consent-revoke", granted=False)
    feedback_replay_after_revoke = client.post(
        "/v1/voice/personalization/feedback",
        headers={**user_auth_header, "Idempotency-Key": "feedback-reset"},
        json=feedback_payload,
    )
    blocked_snapshot = client.get(
        "/v1/voice/personalization/profile-a/snapshot",
        headers=user_auth_header,
    )
    reset = client.delete(
        "/v1/voice/personalization/profile-a",
        headers={**user_auth_header, "Idempotency-Key": "reset-1"},
    )
    reset_replay = client.delete(
        "/v1/voice/personalization/profile-a",
        headers={**user_auth_header, "Idempotency-Key": "reset-1"},
    )
    exported = client.get(
        "/v1/voice/personalization/profile-a/export",
        headers=user_auth_header,
    )

    assert revoked.status_code == 200
    assert feedback_replay_after_revoke.status_code == 200
    assert _data(feedback_replay_after_revoke, "feedback")["idempotent_replay"] is True
    assert blocked_snapshot.status_code == 403
    assert _data(reset, "reset")["deleted_count"] == 1
    assert _data(reset_replay, "reset")["idempotent_replay"] is True
    assert _data(exported, "personalization")["items"] == []


def test_consent_revocation_closes_active_profile_stream_capabilities(
    client,
    user_auth_header,
    _privacy_cache_gc,
):
    from agent.services.voice_stream_session_service import get_voice_stream_session_service

    principal = VoicePrincipal(tenant_id="testuser", subject="testuser")
    session_service = get_voice_stream_session_service()
    session = session_service.create(
        principal,
        runtime_session_id="runtime-consent-revoke",
        deadline_seconds=60,
        profile_id="profile-a",
    )
    assert _consent(client, user_auth_header, key="consent-stream-grant").status_code == 200

    with patch("agent.services.voice_provider.get_voice_provider_service") as provider_factory:
        revoked = _consent(
            client,
            user_auth_header,
            key="consent-stream-revoke",
            granted=False,
        )

    assert revoked.status_code == 200
    assert _data(revoked, "consent")["revoked_stream_count"] == 1
    provider_factory.return_value.delete_stream.assert_called_once_with(
        runtime_session_id="runtime-consent-revoke",
        request_id=f"consent-revoke-{session.session_id}",
    )
    with pytest.raises(VoiceGovernanceError) as error:
        session_service.require(principal, session.session_id)
    assert error.value.code == "voice_stream.not_found"
    _privacy_cache_gc.assert_called_once()


def test_consent_revocation_cache_gc_failure_is_reported_and_replayed(
    client,
    user_auth_header,
    _privacy_cache_gc,
):
    assert _consent(client, user_auth_header, key="consent-cache-grant").status_code == 200
    _privacy_cache_gc.side_effect = RuntimeError("restricted worker unavailable")

    failed = _consent(
        client,
        user_auth_header,
        key="consent-cache-revoke",
        granted=False,
    )

    failed_result = _data(failed, "consent")
    assert failed.status_code == 200
    assert failed_result["runtime_cleanup_pending"] is True
    assert failed_result["runtime_cleanup_failed_count"] == 1

    _privacy_cache_gc.side_effect = None
    replay = _consent(
        client,
        user_auth_header,
        key="consent-cache-revoke",
        granted=False,
    )

    replay_result = _data(replay, "consent")
    assert replay_result["idempotent_replay"] is True
    assert replay_result["runtime_cleanup_pending"] is False
    assert replay_result["runtime_cleanup_failed_count"] == 0
    assert _privacy_cache_gc.call_count == 2


def test_consent_revocation_reports_failed_cleanup_and_idempotent_replay_retries(
    client,
    user_auth_header,
):
    from agent.services.voice_stream_session_service import get_voice_stream_session_service

    principal = VoicePrincipal(tenant_id="testuser", subject="testuser")
    session = get_voice_stream_session_service().create(
        principal,
        runtime_session_id="runtime-consent-retry",
        deadline_seconds=60,
        profile_id="profile-a",
    )
    assert _consent(client, user_auth_header, key="consent-retry-grant").status_code == 200

    with patch("agent.services.voice_provider.get_voice_provider_service") as provider_factory:
        provider_factory.return_value.delete_stream.side_effect = RuntimeError("runtime unavailable")
        failed = _consent(
            client,
            user_auth_header,
            key="consent-retry-revoke",
            granted=False,
        )

        failed_result = _data(failed, "consent")
        assert failed.status_code == 200
        assert failed_result["runtime_cleanup_pending"] is True
        assert failed_result["runtime_cleanup_failed_count"] == 1
        with Session(engine) as database:
            stored = database.exec(select(VoiceRuntimeCleanupDB)).one()
            assert stored.state == "failed"
            assert "runtime-consent-retry" not in stored.runtime_session_ciphertext

        provider_factory.return_value.delete_stream.side_effect = None
        replay = _consent(
            client,
            user_auth_header,
            key="consent-retry-revoke",
            granted=False,
        )

    replay_result = _data(replay, "consent")
    assert replay.status_code == 200
    assert replay_result["idempotent_replay"] is True
    assert replay_result["runtime_cleanup_pending"] is False
    assert replay_result["runtime_cleanup_failed_count"] == 0
    provider_factory.return_value.delete_stream.assert_called_with(
        runtime_session_id="runtime-consent-retry",
        request_id=f"consent-revoke-{session.session_id}",
    )
    with Session(engine) as database:
        assert database.exec(select(VoiceRuntimeCleanupDB)).all() == []


def test_feedback_content_is_not_written_to_audit_details(client, user_auth_header):
    review = _data(_review(client, user_auth_header), "review")
    assert _accept_review(client, user_auth_header, review["id"]).status_code == 200
    assert _consent(client, user_auth_header).status_code == 200
    secret_phrase = "private-spoken-correction"
    response = client.post(
        "/v1/voice/personalization/feedback",
        headers={**user_auth_header, "Idempotency-Key": "feedback-audit"},
        json={
            "profile_id": "profile-a",
            "review_id": review["id"],
            "kind": "vocabulary",
            "target_text": secret_phrase,
            "metadata": {},
        },
    )

    assert response.status_code == 201
    with Session(engine) as session:
        audit = session.exec(
            select(AuditLogDB)
            .where(AuditLogDB.action == "voice_personalization_feedback_added")
            .order_by(AuditLogDB.id.desc())
        ).first()
        stored_feedback = session.exec(
            select(VoiceFeedbackDB).where(VoiceFeedbackDB.id == _data(response, "feedback")["id"])
        ).first()
    assert audit is not None
    assert secret_phrase not in str(audit.details)
    assert stored_feedback is not None
    assert secret_phrase not in str(stored_feedback.target_ciphertext)
    assert str(stored_feedback.target_ciphertext).startswith("enc:v1:")


def test_governance_endpoints_reject_raw_audio(client, user_auth_header):
    response = client.post(
        "/v1/voice/reviews",
        headers={**user_auth_header, "Idempotency-Key": "raw-audio"},
        json={
            "profile_id": "profile-a",
            "result_ref": "result-1",
            "candidate_ids": ["candidate-a"],
            "raw_audio": "not-allowed",
        },
    )

    assert response.status_code == 422
    assert _data(response, "error")["code"] == "voice_governance.raw_audio_not_accepted"


def test_stale_pending_idempotency_claim_can_be_recovered(app):
    principal = VoicePrincipal(tenant_id="tenant-recovery", subject="user-recovery")
    service = VoiceIdempotencyService()
    first = service.begin(
        principal,
        operation="voice_test.recover",
        idempotency_key="recovery-key",
        payload={"value": 1},
    )
    with Session(engine) as session:
        record = session.get(VoiceGovernanceIdempotencyDB, first.record_id)
        assert record is not None
        record.lease_expires_at = time.time() - 1
        session.add(record)
        session.commit()

    recovered = service.begin(
        principal,
        operation="voice_test.recover",
        idempotency_key="recovery-key",
        payload={"value": 1},
    )

    assert recovered.record_id == first.record_id
    assert recovered.replayed is False
    service.abandon(recovered)


def test_reclaimed_idempotency_lease_fences_the_previous_owner(app):
    principal = VoicePrincipal(tenant_id="tenant-fencing", subject="user-fencing")
    service = VoiceIdempotencyService()
    previous = service.begin(
        principal,
        operation="voice_test.fencing",
        idempotency_key="fencing-key",
        payload={"value": 1},
    )
    with Session(engine) as session:
        record = session.get(VoiceGovernanceIdempotencyDB, previous.record_id)
        assert record is not None
        record.lease_expires_at = time.time() - 1
        session.add(record)
        session.commit()

    current = service.begin(
        principal,
        operation="voice_test.fencing",
        idempotency_key="fencing-key",
        payload={"value": 1},
    )

    service.abandon(previous)
    with Session(engine) as session:
        record = session.get(VoiceGovernanceIdempotencyDB, current.record_id)
        assert record is not None
        assert record.state == "pending"
        assert record.lease_expires_at == current.lease_token
    with pytest.raises(VoiceGovernanceError) as stale:
        service.complete(previous, {"owner": "previous"})
    assert stale.value.code == "voice_governance.stale_idempotency_claim"

    service.complete(current, {"owner": "current"})
    replay = service.begin(
        principal,
        operation="voice_test.fencing",
        idempotency_key="fencing-key",
        payload={"value": 1},
    )
    assert replay.replayed is True
    assert replay.result_metadata == {"owner": "current"}


def test_completed_idempotency_claim_expires_and_can_be_reused(app):
    principal = VoicePrincipal(tenant_id="tenant-ttl", subject="user-ttl")
    service = VoiceIdempotencyService(ttl_seconds=60)
    first = service.begin(
        principal,
        operation="voice_test.ttl",
        idempotency_key="ttl-key",
        payload={"value": 1},
    )
    service.complete(first, {"result": "first"})
    with Session(engine) as session:
        record = session.get(VoiceGovernanceIdempotencyDB, first.record_id)
        assert record is not None
        assert record.idempotency_key != "ttl-key"
        assert len(record.idempotency_key) == 64
        record.expires_at = time.time() - 1
        session.add(record)
        session.commit()

    replacement = service.begin(
        principal,
        operation="voice_test.ttl",
        idempotency_key="ttl-key",
        payload={"value": 2},
    )

    assert replacement.replayed is False
    assert replacement.record_id != first.record_id
    service.abandon(replacement)


def test_idempotency_retention_cleanup_purges_only_expired_rows(app):
    principal = VoicePrincipal(tenant_id="tenant-purge", subject="user-purge")
    service = VoiceIdempotencyService(ttl_seconds=60)
    expired = service.begin(
        principal,
        operation="voice_test.purge.expired",
        idempotency_key="expired-key",
        payload={"value": 1},
    )
    live = service.begin(
        principal,
        operation="voice_test.purge.live",
        idempotency_key="live-key",
        payload={"value": 2},
    )
    with Session(engine) as session:
        record = session.get(VoiceGovernanceIdempotencyDB, expired.record_id)
        assert record is not None
        record.expires_at = time.time() - 1
        session.add(record)
        session.commit()

    assert service.purge_expired() == 1
    with Session(engine) as session:
        assert session.get(VoiceGovernanceIdempotencyDB, expired.record_id) is None
        assert session.get(VoiceGovernanceIdempotencyDB, live.record_id) is not None
    service.abandon(live)
