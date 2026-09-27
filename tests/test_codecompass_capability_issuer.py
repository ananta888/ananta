"""WCRB-007: signed, task- and worker-bound CodeCompass capabilities; no authority from model arguments."""

import pytest

from agent.services.access_roles import BUILTIN_ROLES, effective_grants
from agent.services.codecompass_capability_issuer import (
    CapabilityError,
    allowed_codecompass_operations,
    issue_capability,
    verify_signed_capability,
)
from agent.services.tools import execute_ananta_tool

pytestmark = pytest.mark.timeout(30)
KEY = b"k" * 32
NOW = 1_800_000_000
BINDING = ("index-1", "ananta", "rev-7")
ROLES = {role_id: spec["grants"] for role_id, spec in BUILTIN_ROLES.items()}
ROLES["docs"] = {"allow_groups": ["ananta.tool.read.v1"], "constraints": {"paths": ["docs"]}}
ROLES["other-index"] = {"allow_groups": ["ananta.tool.read.v1"], "constraints": {"index_ids": ["index-2"]}}


def issue(grants=None, **overrides):
    values = {"subject_id": "anna", "tenant_id": "t1", "task_id": "task-1", "audience": "worker-a",
              "grants": grants, "key": KEY, "binding": BINDING, "top_level": ["agent", "docs"],
              "now_epoch": NOW}
    values.update(overrides)
    return issue_capability(**values)


def test_an_issued_capability_verifies_for_its_task_worker_and_operation():
    capability = issue()
    assert capability["repository_id"] == "ananta" and capability["revision"] == "rev-7"
    assert capability["allowed_paths"] == ["agent", "docs"] and capability["allowed_index_ids"] == ["index-1"]
    assert "ananta.tool.codecompass.search" in capability["allowed_operations"]
    verify_signed_capability(capability, key=KEY, task_id="task-1", audience="worker-a",
                             operation_id="ananta.tool.codecompass.search", now_epoch=NOW + 10)


@pytest.mark.parametrize("change, error", [
    (lambda c: c.update(allowed_paths=["agent", "docs", "secrets"]), "retrieval_capability_digest_invalid"),
    (lambda c: c.update(task_id="task-2"), "capability_signature_invalid"),
    (lambda c: c.update(audience="worker-b"), "capability_signature_invalid"),
    (lambda c: c.update(allowed_operations=[*c["allowed_operations"], "ananta.tool.repo.write_file"]),
     "capability_signature_invalid"),
    (lambda c: c.pop("capability_signature"), "capability_signature_invalid"),
])
def test_any_change_breaks_the_capability(change, error):
    capability = issue()
    change(capability)
    with pytest.raises(CapabilityError, match=error):
        verify_signed_capability(capability, key=KEY, now_epoch=NOW + 10)


def test_a_capability_is_bound_to_task_worker_operation_key_and_time():
    capability = issue(grants=effective_grants(ROLES, ["viewer"]))
    for kwargs, error in (({"task_id": "task-9"}, "capability_task_mismatch"),
                          ({"audience": "worker-b"}, "capability_audience_mismatch"),
                          ({"operation_id": "ananta.tool.repo.write_file"}, "capability_operation_not_allowed")):
        with pytest.raises(CapabilityError, match=error):
            verify_signed_capability(capability, key=KEY, now_epoch=NOW + 10, **kwargs)
    with pytest.raises(CapabilityError, match="capability_signature_invalid"):
        verify_signed_capability(capability, key=b"x" * 32, now_epoch=NOW + 10)
    with pytest.raises(CapabilityError, match="expired"):
        verify_signed_capability(capability, key=KEY, now_epoch=NOW + 901)


def test_role_constraints_narrow_the_scope():
    docs = issue(grants=effective_grants(ROLES, ["docs"]))
    assert docs["allowed_paths"] == ["docs"]
    with pytest.raises(CapabilityError, match="capability_index_not_granted"):
        issue(grants=effective_grants(ROLES, ["other-index"]))
    assert allowed_codecompass_operations(None) == allowed_codecompass_operations(effective_grants(ROLES, []))
    assert allowed_codecompass_operations(effective_grants(ROLES, ["developer"])) == [
        op for op in allowed_codecompass_operations(None)
        if op in set(allowed_codecompass_operations(effective_grants(ROLES, ["developer"])))]


def test_a_model_cannot_supply_authority_to_a_codecompass_tool(tmp_path):
    for arguments in ({"query": "x", "capability": {"subject_id": "root"}}, {"query": "x", "token": "t"},
                      {"query": "x", "scope": {"collection": "other"}}):
        result = execute_ananta_tool(tool_name="codecompass.search", arguments=arguments,
                                     workspace_dir=str(tmp_path), tool_call_id="t")
        assert result["status"] == "error" and result["error"] == "client_authority_forbidden"
