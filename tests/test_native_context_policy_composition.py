"""Real SQL policy activation/revocation through the Native Hub context port."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.services.context_policy_lifecycle import ContextPolicyActor
from agent.services.native_context_bundle_composition import HubNativeContextBundleReader
from tests.test_context_policy_lifecycle_persistence import _service
from tests.test_native_context_bundle_gateway import setup_context
from tests.test_native_context_policy import build_policy_setup
from tests.test_pi_native_context import context_setup
from worker.runtime.workflow_hub_gateway import WorkflowHubDecisionError


@pytest.fixture
def policy_setup(monkeypatch):
    return build_policy_setup(monkeypatch)


def configured_app(tmp_path, policy_setup, *, task, bundle):
    engine, lifecycle = _service(tmp_path)
    document = policy_setup[4].active.return_value.document
    actor = ContextPolicyActor("synthetic-owner", task["tenant_id"], task["project_id"], frozenset({"project_owner"}))
    draft = lifecycle.create_draft(
        actor=actor, policy_id="policy-1", document=document,
        expected_latest_version=None, idempotency_key="synthetic-draft",
    )
    active = lifecycle.activate(
        actor=actor, policy_id="policy-1", version=draft.version,
        if_match=draft.etag, idempotency_key="synthetic-activation",
    )
    app = Flask("native-context-synthetic")
    tasks, bundles = Mock(), Mock()
    tasks.get_by_id.side_effect = lambda value: task if value == task["id"] else None
    bundles.get_by_id.side_effect = lambda value: bundle if value == bundle["id"] else None
    app.extensions["repository_registry"] = SimpleNamespace(task_repo=tasks, context_bundle_repo=bundles)
    app.extensions["source_control_destination_catalog"] = policy_setup[5]
    return app, HubNativeContextBundleReader(engine=engine), lifecycle, actor, active


def test_real_persistent_policy_grants_then_revokes_context_without_human_input(tmp_path, policy_setup):
    client, request, task, bundle, _, _ = setup_context()
    bundle.update(policy_setup[2])
    app, reader, lifecycle, actor, active = configured_app(tmp_path, policy_setup, task=task, bundle=bundle)
    client.service._context_bundles = reader
    with app.app_context():
        response = client.command("native_context_read", **request)
        assert "def example(): return 1" in response["content"]
        assert "unapproved assembled" not in response["content"]
        lifecycle.revoke(
            actor=actor, policy_id="policy-1", version=active.version,
            if_match=active.etag, idempotency_key="synthetic-revocation",
        )
        with pytest.raises(WorkflowHubDecisionError, match="native_context_active_policy_required"):
            client.command("native_context_read", **request)


def test_current_app_missing_catalog_does_not_reuse_another_apps_grants(tmp_path, policy_setup):
    client, request, task, bundle, _, _ = setup_context()
    bundle.update(policy_setup[2])
    app, reader, *_ = configured_app(tmp_path, policy_setup, task=task, bundle=bundle)
    client.service._context_bundles = reader
    with app.app_context():
        assert client.command("native_context_read", **request)["content"]
    with Flask("other-synthetic-app").app_context():
        with pytest.raises(WorkflowHubDecisionError, match="native_context_destination_catalog_unavailable"):
            client.command("native_context_read", **request)


def test_actual_native_worker_consumes_persisted_hub_policy_projection(tmp_path, policy_setup):
    adapter, task, runner, client, _, _ = context_setup(tmp_path)
    bundle = policy_setup[2] | {"id": "bundle-1", "task_id": task["id"]}
    app, reader, *_ = configured_app(tmp_path, policy_setup, task=task, bundle=bundle)
    client.service._context_bundles = reader
    content = json.dumps([
        {"source_ref": "docs/public/example.py", "content": "def example(): return 1"},
    ], ensure_ascii=True, separators=(",", ":"))
    runner.prompt = json.dumps({
        "task": "Explain this code.", "context_bundle": {"id": "bundle-1", "content": content},
    }, ensure_ascii=True)
    with app.app_context():
        result = adapter.execute_task(task)
    assert result.status == "completed" and len(runner.calls) == 1
    assert [name for name, _ in client.calls].count("native_context_read") == 5
    assert "unapproved assembled" not in runner.calls[0][1]["input_text"]


@pytest.mark.parametrize(
    "status", [None, "unknown", "archived", "aborted", "timeout", "verification_failed", "skipped"],
)
def test_inactive_task_never_reaches_context_policy(status):
    client, request, task, _, policy, _ = setup_context()
    task["status"] = status
    with pytest.raises(WorkflowHubDecisionError, match="native_context_task_binding_mismatch"):
        client.command("native_context_read", **request)
    policy.project.assert_not_called()
