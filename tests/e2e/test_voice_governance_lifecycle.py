from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_result_artifact_service import get_voice_result_artifact_service


def _headers(auth: dict[str, str], key: str) -> dict[str, str]:
    return {**auth, "Idempotency-Key": key}


def test_review_consent_learning_export_and_complete_delete_flow(client, user_auth_header) -> None:
    profile_id = "governance-e2e"
    candidate_id = "governance-e2e-candidate"
    with client.application.app_context():
        artifact = get_voice_result_artifact_service().create(
            VoicePrincipal(tenant_id="testuser", subject="testuser"),
            request_hash="governance-e2e-request",
            profile_id=profile_id,
            result={
                "text": "Ananta",
                "selected_candidate_id": candidate_id,
                "candidates": [
                    {
                        "candidate_id": candidate_id,
                        "text": "Ananta",
                        "status": "succeeded",
                    }
                ],
            },
        )
    created = client.post(
        "/v1/voice/reviews",
        headers=_headers(user_auth_header, "governance-e2e-review"),
        json={
            "profile_id": profile_id,
            "result_ref": artifact["id"],
            "candidate_ids": [candidate_id],
        },
    )
    review = created.get_json()["data"]["review"]
    decided = client.post(
        f"/v1/voice/reviews/{review['id']}/decision",
        headers=_headers(user_auth_header, "governance-e2e-decision"),
        json={
            "decision": "correct",
            "expected_version": review["version"],
            "selected_candidate_id": candidate_id,
            "correction_text": "Ananta",
        },
    )
    assert decided.status_code == 200

    consent = client.put(
        f"/v1/voice/consents/{profile_id}",
        headers=_headers(user_auth_header, "governance-e2e-consent"),
        json={
            "granted": True,
            "categories": ["text_corrections", "vocabulary"],
            "retention_days": 30,
        },
    )
    assert consent.status_code == 200
    feedback = client.post(
        "/v1/voice/personalization/feedback",
        headers=_headers(user_auth_header, "governance-e2e-feedback"),
        json={
            "profile_id": profile_id,
            "review_id": review["id"],
            "kind": "vocabulary",
            "source_text": None,
            "target_text": "Ananta",
            "metadata": {"language": "de"},
        },
    )
    assert feedback.status_code == 201
    snapshot = client.get(f"/v1/voice/personalization/{profile_id}/snapshot", headers=user_auth_header)
    assert snapshot.get_json()["data"]["snapshot"]["vocabulary"] == ["Ananta"]

    deleted = client.delete(
        f"/v1/voice/privacy/{profile_id}",
        headers=_headers(user_auth_header, "governance-e2e-delete"),
        json={"confirmed": True},
    )
    assert deleted.status_code == 200
    deletion = deleted.get_json()["data"]["deletion"]
    assert deletion["snapshots_revoked"] is True
    assert deletion["deleted_count"] > 0
    assert client.get(f"/v1/voice/reviews/{review['id']}", headers=user_auth_header).status_code == 404
    export = client.get(f"/v1/voice/personalization/{profile_id}/export", headers=user_auth_header)
    assert export.get_json()["data"]["personalization"]["items"] == []
    assert client.get(
        f"/v1/voice/personalization/{profile_id}/snapshot",
        headers=user_auth_header,
    ).status_code == 403
