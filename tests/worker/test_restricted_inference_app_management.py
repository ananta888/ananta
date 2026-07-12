from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from agent.services.restricted_inference_contract import RestrictedInferenceOperation
from agent.services.restricted_inference_model_manifest import (
    ENGINE_HUGGINGFACE,
    FORMAT_SAFETENSORS,
    ROLE_WEIGHTS,
    SOURCE_LOCAL_SNAPSHOT,
    ModelManifestFile,
    RestrictedModelManifest,
)
from worker.runtime.restricted_inference_app import (
    CACHE_GC_ENDPOINT,
    CONFIGURATION_ENDPOINT,
    LOAD_ENDPOINT,
    STATUS_ENDPOINT,
    create_app,
)

TEST_TOKEN = "restricted-inference-test-token"


def test_environment_roots_build_lazy_runtime_without_loading_a_model(tmp_path: Path, monkeypatch) -> None:
    manifests = tmp_path / "manifests"
    snapshots = tmp_path / "snapshots"
    manifests.mkdir()
    snapshots.mkdir()
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_MANIFEST_ROOT", str(manifests))
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_SNAPSHOT_ROOT", str(snapshots))
    client = create_app(auth_token=TEST_TOKEN).test_client()

    health = client.get("/health")
    status = client.get(STATUS_ENDPOINT, headers={"Authorization": f"Bearer {TEST_TOKEN}"})

    assert health.get_json()["runtime_configured"] is True
    assert status.status_code == 200
    assert status.get_json()["models"] == []
    assert status.get_json()["resources"]["loaded_models"] == 0


def test_management_endpoints_require_authentication_and_cache_gc_is_bounded(tmp_path: Path, monkeypatch) -> None:
    manifests = tmp_path / "manifests"
    snapshots = tmp_path / "snapshots"
    manifests.mkdir()
    snapshots.mkdir()
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_MANIFEST_ROOT", str(manifests))
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_SNAPSHOT_ROOT", str(snapshots))
    client = create_app(auth_token=TEST_TOKEN).test_client()

    unauthorized = client.get(STATUS_ENDPOINT)
    gc = client.post(CACHE_GC_ENDPOINT, headers={"Authorization": f"Bearer {TEST_TOKEN}"})

    assert unauthorized.status_code == 401
    assert gc.status_code == 200
    assert gc.get_json() == {"ok": True, "removed_entries": 0}


def test_status_publishes_versioned_verified_restricted_capability_catalog(tmp_path: Path, monkeypatch) -> None:
    manifests = tmp_path / "manifests"
    snapshots = tmp_path / "snapshots"
    manifests.mkdir()
    snapshots.mkdir()
    digest = hashlib.sha256(b"").hexdigest()
    manifest = RestrictedModelManifest(
        manifest_id="fixture-classifier",
        model_id="fixture/classifier",
        engine=ENGINE_HUGGINGFACE,
        model_format=FORMAT_SAFETENSORS,
        revision="0123456789abcdef",
        source_type=SOURCE_LOCAL_SNAPSHOT,
        license_id="Apache-2.0",
        operations=(RestrictedInferenceOperation.CLASSIFY,),
        files=(ModelManifestFile("model.safetensors", digest, 0, ROLE_WEIGHTS),),
        metadata={"languages": ["de", "en"]},
    )
    (manifests / "fixture-classifier.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    snapshot = snapshots / manifest.digest
    snapshot.mkdir()
    (snapshot / "model.safetensors").write_bytes(b"")
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_MANIFEST_ROOT", str(manifests))
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_SNAPSHOT_ROOT", str(snapshots))

    response = (
        create_app(auth_token=TEST_TOKEN)
        .test_client()
        .get(
            STATUS_ENDPOINT,
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
    )

    assert response.status_code == 200
    capability = response.get_json()["capability_catalog"][0]
    assert capability["schema_version"] == "ananta.model-capability.v1"
    assert capability["id"] == "fixture/classifier"
    assert capability["tasks"] == ["classify"]
    assert capability["languages"] == ["de", "en"]
    assert capability["license"] == "Apache-2.0"
    assert capability["status"] == "ready"
    assert capability["extensions"]["restricted_inference"]["no_generation"] is True


def test_explicit_load_and_runtime_configuration_are_authenticated_bounded_worker_operations() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.load_calls = 0
            self.version = 1
            self.allow_cpu_fallback = False

        def handle(self, _envelope):
            raise AssertionError("management must not enter inference handling")

        def status(self):
            return {"models": [], "resources": {}, "cache_entries": 0}

        def load(self, manifest_id: str, *, deadline_epoch_ms: int):
            self.load_calls += 1
            return {
                "manifest_id": manifest_id,
                "manifest_digest": "a" * 64,
                "model_id": "fixture/model",
                "engine": "huggingface-transformers",
                "state": "idle",
                "active_leases": 0,
                "loaded_device": "cpu",
                "failure_code": "",
                "deadline_accepted": deadline_epoch_ms > time.time_ns() // 1_000_000,
            }

        def configuration(self):
            return {
                "schema_version": "ananta.restricted-runtime-config.v1",
                "version": self.version,
                "mutable": {"allow_cpu_fallback": self.allow_cpu_fallback},
                "fixed": {
                    "downloads_allowed": False,
                    "generation_allowed": False,
                    "local_snapshots_only": True,
                    "trust_remote_code": False,
                },
            }

        def update_configuration(self, delta, *, expected_version: int):
            if expected_version != self.version:
                raise RuntimeError("version conflict")
            self.allow_cpu_fallback = bool(delta["allow_cpu_fallback"])
            self.version += 1
            return {**self.configuration(), "changed": True}

    runtime = _Runtime()
    client = create_app(runtime=runtime, auth_token=TEST_TOKEN).test_client()
    auth = {"Authorization": f"Bearer {TEST_TOKEN}"}

    unauthorized_load = client.post(
        LOAD_ENDPOINT,
        json={
            "manifest_id": "fixture-classifier",
            "deadline_epoch_ms": time.time_ns() // 1_000_000 + 30_000,
        },
    )
    status = client.get(STATUS_ENDPOINT, headers=auth)
    configuration = client.get(CONFIGURATION_ENDPOINT, headers=auth)
    invalid_load = client.post(
        LOAD_ENDPOINT,
        headers=auth,
        json={"manifest_id": "../../unsafe", "deadline_epoch_ms": time.time_ns() // 1_000_000 + 30_000},
    )
    loaded = client.post(
        LOAD_ENDPOINT,
        headers=auth,
        json={
            "manifest_id": "fixture-classifier",
            "deadline_epoch_ms": time.time_ns() // 1_000_000 + 30_000,
        },
    )
    updated = client.patch(
        CONFIGURATION_ENDPOINT,
        headers=auth,
        json={"delta": {"allow_cpu_fallback": True}, "expected_version": 1},
    )
    stale = client.patch(
        CONFIGURATION_ENDPOINT,
        headers=auth,
        json={"delta": {"allow_cpu_fallback": False}, "expected_version": 1},
    )

    assert unauthorized_load.status_code == 401
    assert status.status_code == 200
    assert runtime.load_calls == 1
    assert configuration.get_json()["fixed"] == {
        "downloads_allowed": False,
        "generation_allowed": False,
        "local_snapshots_only": True,
        "trust_remote_code": False,
    }
    assert invalid_load.status_code == 422
    assert loaded.status_code == 200
    assert loaded.get_json()["no_generation"] is True
    assert loaded.get_json()["model"]["state"] == "idle"
    assert updated.status_code == 200
    assert updated.get_json()["mutable"]["allow_cpu_fallback"] is True
    assert stale.status_code == 422
