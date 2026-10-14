"""Actual Registry-issued test identity plus Hub-only signed inference grants."""

from __future__ import annotations


import pytest
import sys
import time
import types
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.services.specialist_inference_assignment_service import SpecialistInferenceAssignmentService
from ananta_contracts.specialist_inference_assignment import input_digest, profile_digest, verify_assignment

pytestmark = pytest.mark.timeout(20)
MODEL = "d0a4e53d09cebe6bc963dd9be319d4279084bb2d"


def issued_token(tmp_path, *, clock=lambda: 100.0):
    from tests.hub.test_hub_evidence_registry_service import service as registry_fixture
    from agent.services.research_training_dataset_service import ResearchTrainingDatasetService
    from agent.services.research_training_evidence_service import ResearchTrainingEvidenceService

    identities = registry_fixture.__wrapped__()
    (tmp_path / "input.txt").write_text("Original synthetic inference input")
    dataset = ResearchTrainingDatasetService(
        tmp_path,
        evidence=ResearchTrainingEvidenceService(identities),
        allowed_licenses=["synthetic-test"],
        maximum_dataset_bytes=1000,
    ).admit(
        tenant_id="tenant-a",
        project_id="project-a",
        candidates=[
            {
                "origin": {"kind": "generated-fixture", "version": "v1"},
                "relative_ref": "input.txt",
                "split": "train",
                "media_type": "text_plain",
                "license_id": "synthetic-test",
                "consent_class": "synthetic",
            }
        ],
        policy={"usage": "test-only"},
        evidence_scope="test",
    )
    body = {
        "state": "Original synthetic inference input",
        "model": "gliformer-large-v1",
        "questions": {"needed": {"type": "noul", "instructions": "Is this synthetic?"}},
    }
    run = identities.reserve_run(
        tenant_id="tenant-a",
        project_id="project-a",
        task_id="test-task",
        assignment_id="test-assignment",
        dispatch_lease_id="test-lease",
        repository_revision="a" * 64,
        input_digest=input_digest(body),
        execution_profile_digest=profile_digest(MODEL),
        environment_digest="b" * 64,
        source_ids=tuple(s["source_id"] for s in dataset["shards"]),
        evidence_scope="test",
        idempotency_key="inference-test",
        synthetic=True,
    )
    key = Ed25519PrivateKey.generate()
    service = SpecialistInferenceAssignmentService(registry=identities, private_key=key, clock=clock)
    bindings = dict(
        tenant_id="tenant-a",
        project_id="project-a",
        run_id=run.run_id,
        task_id="test-task",
        assignment_id="test-assignment",
        dispatch_lease_id="test-lease",
        body=body,
        model_revision=MODEL,
    )
    token = service.issue(**bindings)
    return token, key, body, service, bindings


def test_issuer_requires_registered_exact_input_model_and_dispatch_binding(tmp_path):
    token, key, body, service, bindings = issued_token(tmp_path)
    assignment = verify_assignment(token, key.public_key(), body=body, model_revision=MODEL, now=110)
    assert assignment["evidence"]["evidence_scope"] == "test"
    assert assignment["evidence"]["run_id"] == bindings["run_id"]
    for changed in (
        {"body": {**body, "state": "changed"}},
        {"dispatch_lease_id": "wrong-lease"},
        {"model_revision": "a" * 40},
        {"run_id": "unregistered"},
    ):
        with pytest.raises(ValueError):
            service.issue(**{**bindings, **changed})


def test_worker_refuses_tampered_expired_wrong_model_or_wrong_issuer_tokens(tmp_path):
    token, key, body, _, _ = issued_token(tmp_path)
    for candidate, public_key, request, model, now in [
        (token + "x", key.public_key(), body, MODEL, 110),
        (token, Ed25519PrivateKey.generate().public_key(), body, MODEL, 110),
        (token, key.public_key(), {**body, "state": "changed"}, MODEL, 110),
        (token, key.public_key(), body, "b" * 40, 110),
        (token, key.public_key(), body, MODEL, 161),
    ]:
        with pytest.raises(ValueError, match="unverified"):
            verify_assignment(candidate, public_key, body=request, model_revision=model, now=now)


def test_headless_worker_requires_signed_assignment_and_rejects_replay(tmp_path, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from cryptography.hazmat.primitives import serialization
    from worker.decision import jeff_runtime

    token, key, body, _, _ = issued_token(tmp_path, clock=time.time)
    key_file = tmp_path / "api-key"
    key_file.write_text("synthetic-service-key-0123456789")
    public_file = tmp_path / "hub-public.pem"
    public_file.write_bytes(
        key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    calls = []

    def fake_upstream(settings):
        app = FastAPI()

        async def infer(request):
            calls.append(await request.json())
            return {"model": "gliformer-large-v1", "answers": {"needed": {"type": "noul", "noul": 0.9}}}

        infer.__annotations__["request"] = Request
        app.post("/v1/systemone")(infer)
        return app

    fake_app_module = types.ModuleType("jeff.server.app")
    fake_app_module.create_app = fake_upstream
    fake_config_module = types.ModuleType("jeff.server.config")
    fake_config_module.Settings = lambda: types.SimpleNamespace(max_queue=32)
    for name, module in {
        "jeff": types.ModuleType("jeff"),
        "jeff.server": types.ModuleType("jeff.server"),
        "jeff.server.app": fake_app_module,
        "jeff.server.config": fake_config_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(jeff_runtime, "verify_weights", lambda root, manifest: None)  # model port double only
    monkeypatch.setenv(
        "JEFF_MODEL_MANIFEST", str(Path(__file__).resolve().parents[2] / "worker/decision/jeff-model-manifest.json")
    )
    for name, value in {
        "JEFF_API_KEY_FILE": str(key_file),
        "JEFF_HUB_PUBLIC_KEY_FILE": str(public_file),
        "JEFF_REQUIRE_ASSIGNMENT": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }.items():
        monkeypatch.setenv(name, value)
    client = TestClient(jeff_runtime.create_app())
    headers = {"Authorization": "Bearer " + key_file.read_text()}
    assert client.post("/v1/systemone", json=body, headers=headers).status_code == 403
    headers["X-Ananta-Assignment"] = token
    response = client.post("/v1/systemone", json=body, headers=headers)
    assert response.status_code == 200
    assert response.headers["x-ananta-assignment-digest"]
    assert client.post("/v1/systemone", json=body, headers=headers).status_code == 409
    assert len(calls) == 1
