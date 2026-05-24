from __future__ import annotations


def test_source_verification_routes_success(client, app, admin_auth_header):
    tid = "T-SOURCE-VERIFY-1"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "proposing",
            verification_status={
                "source_catalog": {
                    "source_catalog_id": "catalog-1",
                    "source_catalog_hash": "abc123def4567890",
                    "sources": [
                        {
                            "source_id": "SRC_0001",
                            "source_type": "repo_file",
                            "path": "src/a.py",
                            "record_id": "r1",
                            "allowed_for_llm_scope": True,
                        },
                        {
                            "source_id": "SRC_0002",
                            "source_type": "repo_file",
                            "path": "src/secret.py",
                            "record_id": "r2",
                            "allowed_for_llm_scope": False,
                            "content": "TOP_SECRET",
                        },
                    ],
                },
                "answer_verification": {
                    "citation_verification_status": "failed_policy_scope",
                    "answer_schema": "grounded_answer.v1",
                    "verified_claim_count": 1,
                    "unverified_claim_count": 1,
                    "failed_claims": [{"claim_id": "CLM_0002", "reason": "failed_policy_scope"}],
                },
            },
        )

    res_sources = client.get(f"/tasks/{tid}/sources", headers=admin_auth_header)
    assert res_sources.status_code == 200
    payload_sources = res_sources.json["data"]
    assert payload_sources["source_catalog_id"] == "catalog-1"
    assert payload_sources["catalog_hash"] == "abc123def4567890"
    assert payload_sources["source_count"] == 2
    blocked = [s for s in payload_sources["sources"] if s["source_id"] == "SRC_0002"][0]
    assert blocked["content_exposed"] is False
    assert blocked["redaction_reason"] == "blocked_by_policy_scope"
    assert "content" not in blocked

    res_ver = client.get(f"/tasks/{tid}/answer-verification", headers=admin_auth_header)
    assert res_ver.status_code == 200
    payload_ver = res_ver.json["data"]
    assert payload_ver["status"] == "failed_policy_scope"
    assert payload_ver["answer_schema"] == "grounded_answer.v1"
    assert payload_ver["verified_claim_count"] == 1
    assert payload_ver["unverified_claim_count"] == 1


def test_source_verification_routes_404(client, admin_auth_header):
    res_sources = client.get("/tasks/does-not-exist/sources", headers=admin_auth_header)
    assert res_sources.status_code == 404

    res_ver = client.get("/tasks/does-not-exist/answer-verification", headers=admin_auth_header)
    assert res_ver.status_code == 404
