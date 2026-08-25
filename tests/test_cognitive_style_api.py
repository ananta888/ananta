from __future__ import annotations

from agent.routes.config import cognitive_styles as routes
from agent.services.cognitive_style_service import (
    CognitiveStyleService,
    InMemoryCognitiveStyleStateRepository,
)
from agent.services.model_profile_loader import ModelProfile


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _context():
    return {
        "model_profile_id": "local-chat",
        "model_revision": "r1",
        "quantization": "q8",
        "runtime": "llamacpp",
        "backend_id": "lmstudio",
        "system_prompt_digest": "sha256:system",
        "role_prompt_digest": "sha256:role",
        "tool_mode": "prompt_json",
        "sampling_digest": "sha256:sampling",
    }


def _setup(monkeypatch):
    service = CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    monkeypatch.setattr(routes, "get_cognitive_style_service", lambda: service)
    return service


def test_style_read_model_is_versioned_explainable_and_capability_protected(
    client, admin_token, user_auth_header, monkeypatch,
):
    _setup(monkeypatch)

    allowed = client.get("/models/styles/v1", headers=_headers(admin_token))
    denied = client.get("/models/styles/v1", headers=user_auth_header)

    assert allowed.status_code == 200
    assert allowed.json["data"]["schema"] == "ananta.cognitive-style-read-model.v1"
    assert allowed.json["data"]["configuration"]["role_targets"]
    assert "keine psychologische Diagnose" in allowed.json["data"]["heuristic_notice"]
    assert denied.status_code == 403


def test_style_mutation_rejects_unknown_fields_and_revision_conflicts(
    client, admin_token, monkeypatch,
):
    service = _setup(monkeypatch)
    current = service.read().configuration.model_dump(mode="json", by_alias=True)
    body = {
        "schema": "ananta.cognitive-style-mutation-command.v1",
        "expected_revision": 0,
        "profiles": current["profiles"],
        "role_targets": current["role_targets"],
        "overlays": current["overlays"],
    }
    invalid = client.put(
        "/models/styles/v1", json={**body, "permission_grant": "admin"},
        headers=_headers(admin_token),
    )
    applied = client.put(
        "/models/styles/v1", json=body, headers=_headers(admin_token),
    )
    conflict = client.put(
        "/models/styles/v1", json=body, headers=_headers(admin_token),
    )

    assert invalid.status_code == 400
    assert applied.status_code == 200
    assert applied.json["data"]["revision"] == 1
    assert conflict.status_code == 409


def test_benchmark_plan_is_fixed_and_async_run_is_bounded(
    client, admin_token, monkeypatch,
):
    _setup(monkeypatch)
    monkeypatch.setattr(routes, "_profiles", lambda: (
        ModelProfile(profile_id="local-chat", provider_id="lmstudio", model="lfm"),
    ))

    class _Jobs:
        def submit_cognitive_style_benchmark_job(self, **kwargs):
            assert len(kwargs["plan"].variants) == 6
            assert kwargs["plan"].repeats == 2
            return {
                "job_id": "job-style-1",
                "job_type": "cognitive_style_benchmark",
                "status": "queued",
            }

    monkeypatch.setattr(routes, "get_benchmark_job_service", lambda: _Jobs())
    body = {
        "schema": "ananta.style-benchmark-run-command.v1",
        "expected_revision": 0,
        "context": _context(),
        "repeats": 2,
        "seeds": [17, 41],
        "temperatures": [0, .4],
    }
    plan = client.post(
        "/models/styles/v1/benchmarks/plan", json=body,
        headers=_headers(admin_token),
    )
    run = client.post(
        "/models/styles/v1/benchmarks/run", json=body,
        headers=_headers(admin_token),
    )

    assert plan.status_code == 200
    assert len(plan.json["data"]["variants"]) == 6
    assert run.status_code == 202
    assert run.json["data"]["status"] == "queued"


def test_mismatch_and_evolution_endpoints_preserve_review_boundary(
    client, admin_token, monkeypatch,
):
    _setup(monkeypatch)
    mismatch = client.post(
        "/models/styles/v1/mismatches",
        json={
            "schema": "ananta.style-mismatch-record-command.v1",
            "expected_revision": 0,
            "evidence": {
                "evidence_id": "evidence-1", "agent_id": "agent-1",
                "role_id": "reviewer", "model_profile_id": "local-chat",
                "signal": "rework", "observed_at": "2026-08-25T00:00:00Z",
                "correlation_score": .3, "hypothesis": "Style könnte beitragen.",
                "alternative_causes": ["Unvollständiger Kontext"],
            },
        },
        headers=_headers(admin_token),
    )
    proposal = client.post(
        "/models/styles/v1/proposals",
        json={
            "schema": "ananta.style-evolution-proposal-command.v1",
            "expected_revision": 1,
            "proposal": {
                "proposal_id": "proposal-1", "proposal_type": "role_overlay",
                "hypothesis": "Ein Overlay reduziert Rework.",
                "expected_effect": "Weniger Rework.",
                "payload": {"role_id": "reviewer"},
            },
        },
        headers=_headers(admin_token),
    )
    unreviewed_apply = client.post(
        "/models/styles/v1/proposals/proposal-1/transition",
        json={
            "schema": "ananta.style-evolution-transition-mutation-command.v1",
            "expected_revision": 2,
            "transition": {
                "expected_status": "proposed", "target_status": "applied",
            },
        },
        headers=_headers(admin_token),
    )

    assert mismatch.status_code == proposal.status_code == 200
    assert mismatch.json["data"]["mismatch_evidence"][0]["causes_reclassification"] is False
    assert unreviewed_apply.status_code == 400


def test_drift_retrospective_evidence_proposal_and_experiment_endpoints(
    client, admin_token, monkeypatch,
):
    _setup(monkeypatch)
    headers = _headers(admin_token)
    drift = client.post(
        "/models/styles/v1/drift",
        json={
            "schema": "ananta.style-profile-drift-command.v1",
            "contexts": [_context()],
            "stale_after_days": 90,
        },
        headers=headers,
    )
    retrospective = client.post(
        "/models/styles/v1/retrospectives/analyze",
        json={
            "schema": "ananta.style-retrospective-analysis-command.v1",
            "signals": [{
                "agent_id": "agent-1", "role_id": "reviewer",
                "model_profile_id": "local-chat", "signal": "rework",
                "observed_at": "2026-08-25T00:00:00Z", "severity": .5,
                "evidence_refs": ["RUN_caller_supplied"],
            }],
        },
        headers=headers,
    )
    hypothesis = retrospective.json["data"]["hypotheses"][0]
    mismatch = client.post(
        "/models/styles/v1/mismatches",
        json={
            "schema": "ananta.style-mismatch-record-command.v1",
            "expected_revision": 0,
            "evidence": hypothesis,
        },
        headers=headers,
    )
    proposal = client.post(
        "/models/styles/v1/proposals/from-evidence",
        json={
            "schema": "ananta.style-evolution-from-evidence-command.v1",
            "expected_revision": 1,
            "evidence_id": hypothesis["evidence_id"],
            "proposal_id": "proposal-from-retro",
            "proposal_type": "role_overlay",
            "experiment_id": "experiment-next-sprint",
        },
        headers=headers,
    )
    experiment = client.post(
        "/models/styles/v1/experiments/evaluate",
        json={
            "schema": "ananta.complementary-style-experiment-command.v1",
            "experiment_id": "paired-run-1",
            "complementary": {
                "quality_score": .9, "rework_count": 1, "cost_units": 5,
                "duration_seconds": 10, "gates_passed": 3, "gates_total": 3,
            },
            "homogeneous_control": {
                "quality_score": .7, "rework_count": 2, "cost_units": 4,
                "duration_seconds": 9, "gates_passed": 2, "gates_total": 3,
            },
        },
        headers=headers,
    )

    assert drift.status_code == 200
    assert drift.json["data"]["entries"][0]["status"] == "missing"
    assert retrospective.status_code == mismatch.status_code == proposal.status_code == 200
    assert retrospective.json["data"]["causal_claim_made"] is False
    assert proposal.json["data"]["evolution_proposals"][0]["review_required"] is True
    assert experiment.status_code == 200
    assert experiment.json["data"]["outcome"] == "supported"


def test_drift_rebenchmark_queues_one_revision_safe_hub_batch(
    client, admin_token, monkeypatch,
):
    _setup(monkeypatch)
    monkeypatch.setattr(routes, "_profiles", lambda: (
        ModelProfile(profile_id="local-chat", provider_id="lmstudio", model="lfm"),
    ))

    class _Jobs:
        def submit_cognitive_style_rebenchmark_job(self, **kwargs):
            items = tuple(kwargs["work_items"])
            assert [item.profile.profile_id for item in items] == ["local-chat"]
            assert kwargs["expected_revision"] == 0
            return {
                "job_id": "job-style-drift-1",
                "job_type": "cognitive_style_rebenchmark",
                "status": "queued",
            }

    monkeypatch.setattr(routes, "get_benchmark_job_service", lambda: _Jobs())
    response = client.post(
        "/models/styles/v1/drift/rebenchmark",
        json={
            "schema": "ananta.style-rebenchmark-due-command.v1",
            "expected_revision": 0,
            "contexts": [_context()],
        },
        headers=_headers(admin_token),
    )

    assert response.status_code == 202
    assert response.json["data"]["scheduled_profile_ids"] == ["local-chat"]
    assert response.json["data"]["skipped_profile_ids"] == []
    assert response.json["data"]["job"]["job_type"] == "cognitive_style_rebenchmark"


def test_drift_rebenchmark_rejects_revision_conflict_before_queueing(
    client, admin_token, monkeypatch,
):
    _setup(monkeypatch)
    response = client.post(
        "/models/styles/v1/drift/rebenchmark",
        json={
            "schema": "ananta.style-rebenchmark-due-command.v1",
            "expected_revision": 7,
            "contexts": [_context()],
        },
        headers=_headers(admin_token),
    )

    assert response.status_code == 409
    assert response.json["data"]["current_revision"] == 0
