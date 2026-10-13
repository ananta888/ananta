"""Actual layer/snapshot stores and MCP/SQL Registry under synthetic authority."""

from types import SimpleNamespace

import pytest

from agent.services.codecompass_layer_hub_store import SnapshotManifestStore
from agent.services.codecompass_layer_publication_observers import pointer_index_id
from agent.services.codecompass_layer_query_backend import CodeCompassLayerQueryBackend
from agent.services.codecompass_layer_service import CodeCompassLayerService
from agent.services.mcp_registry_service import get_mcp_registry_service
from tests.codecompass.test_codecompass_layer_query_backend import PROFILE, manifest, publish_base
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import hub as hub
from tests.meet.test_meet_companion_task_tools import post
from worker.incremental_index.head_registry import LayerHeadRegistry
from worker.incremental_index.layer_store import ArtifactLayerStore

pytestmark = pytest.mark.timeout(20)


@pytest.fixture
def layers(hub, tmp_path, monkeypatch):
    from agent.services import repository_registry

    old_files = {"worker/visible.py": "old", "secret/private.py": "old secret"}
    publish_base(tmp_path, old_files)
    new = manifest({"worker/visible.py": "new", "worker/added.py": "added",
                    "worker-secret/sibling.py": "sibling", "secret/private.py": "new secret"})
    snapshots = SnapshotManifestStore(tmp_path)
    snapshots.put(new)
    heads = LayerHeadRegistry(tmp_path)
    heads.create_head("foreign", layer_id="f" * 64, snapshot_revision="f" * 64)
    metadata = {"codecompass_layer_head": {"schema": "ananta.codecompass_layer_pointer.v1",
                                         "profile_id": PROFILE}}
    pointer = SimpleNamespace(id=pointer_index_id(PROFILE), status="completed", index_metadata=metadata)
    lookups = []

    def get_index(identity):
        lookups.append(identity)
        return pointer if identity == pointer.id else None

    indices = SimpleNamespace(get_by_id=get_index)
    monkeypatch.setattr(repository_registry, "get_repository_registry",
                        lambda: SimpleNamespace(knowledge_index_repo=indices))
    query = CodeCompassLayerQueryBackend(layers=ArtifactLayerStore(tmp_path), heads=heads, snapshots=snapshots)
    hub.app.extensions["codecompass_layer_service"] = CodeCompassLayerService(query)
    hub.service.execution.registry = get_mcp_registry_service()
    hub.capability.update(revision=new["snapshot_revision"], allowed_index_ids=[pointer.id])
    monkeypatch.setattr(heads, "list_profiles", lambda: pytest.fail("must not enumerate the global catalog"))
    return SimpleNamespace(**locals())


def call(f, name, arguments):
    f.sequence = getattr(f, "sequence", 0) + 1
    return post(f.hub, f.hub.request | {"nonce": f"{f.sequence:032x}",
        "tool": "codecompass." + name, "arguments": arguments})[1]


def test_actual_head_handler_lists_only_authorized_pointer_and_excludes_foreign_profile(layers):
    response = call(layers, "layers_heads", {})
    assert response.status_code == 200, response.get_json()
    text = response.get_json()["text"]
    assert PROFILE in text and "foreign" not in text
    assert set(layers.lookups) == {layers.pointer.id}
    response = call(layers, "layers_heads", {"profile_id": "foreign"})
    assert response.status_code == 403


def test_actual_plan_uses_immutable_hub_snapshot_and_only_permitted_changes(layers):
    response = call(layers, "layers_plan", {"old_manifest": {},
        "new_manifest": {"snapshot_revision": layers.new["snapshot_revision"]}, "profile_id": PROFILE})
    assert response.status_code == 200, response.get_json()
    text = response.get_json()["text"]
    assert "worker/visible.py" in text and "worker/added.py" in text
    assert "secret/private.py" not in text and "worker-secret" not in text
    assert response.get_json()["run"]["evidence_scope"] == "test"


@pytest.mark.parametrize("change", ["manifest", "profile", "pointer", "tenant_scope"])
def test_model_cannot_replace_hub_manifest_pointer_or_scope(layers, change):
    args = {"old_manifest": {}, "new_manifest": {"snapshot_revision": layers.new["snapshot_revision"]},
            "profile_id": PROFILE}
    if change == "manifest":
        args["new_manifest"] = layers.new | {"files": []}
    elif change == "profile":
        args["profile_id"] = "foreign"
    elif change == "pointer":
        layers.pointer.index_metadata["codecompass_layer_head"]["profile_id"] = "foreign"
    else:
        path = layers.heads._head_path(PROFILE)
        import json

        value = json.loads(path.read_text())
        value["repository_id"] = "foreign-repository"
        path.write_text(json.dumps(value))
    assert call(layers, "layers_plan", args).status_code == 403


def test_head_change_during_plan_cannot_return_mixed_generation(layers, monkeypatch):
    original = layers.query._layers.get_layer

    def changed(identity):
        layer = original(identity)
        result = layers.heads.update_head(PROFILE, expected_generation=1, new_layer_id=identity,
                                         snapshot_revision=layers.new["snapshot_revision"])
        assert result.success
        return layer

    monkeypatch.setattr(layers.query._layers, "get_layer", changed)
    response = call(layers, "layers_plan", {"old_manifest": {}, "new_manifest": {}, "profile_id": PROFILE})
    assert response.status_code == 403
