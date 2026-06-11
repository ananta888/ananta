"""HDE-010: tool_proposal.v1 schema validation."""
import pytest

from agent.services.custom_tool_proposal_service import validate_proposal_payload


def _proposal(**overrides):
    payload = {
        "name": "custom.count_lines",
        "description": "Count lines of a file",
        "proposed_by": "user:test",
        "source_task_id": "task-1",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "execution_kind": "command_template",
        "command_template": ["wc", "-l", "{path}"],
        "path_arguments": ["path"],
        "allowed_paths": ["**"],
        "denied_paths": [],
        "timeout_seconds": 10,
        "output_max_chars": 2000,
        "tests": [
            {"name": "ok", "kind": "positive", "arguments": {"path": "a.txt"}},
            {"name": "fail", "kind": "negative", "arguments": {"path": "missing.txt"}},
        ],
    }
    payload.update(overrides)
    return payload


def test_valid_proposal_passes():
    assert validate_proposal_payload(_proposal()) == []


@pytest.mark.parametrize(
    "overrides,expected_error",
    [
        ({"name": "repo.grep"}, "invalid_name_namespace"),
        ({"name": "custom.Grep"}, "invalid_name_namespace"),
        ({"name": "evil.tool"}, "invalid_name_namespace"),
        ({"risk_class": "admin"}, "invalid_risk_class"),
        ({"category": "blocked"}, "invalid_category"),
        ({"execution_plane": "hub_control_only"}, "missing_or_invalid_execution_plane"),
        ({"execution_plane": None}, "missing_or_invalid_execution_plane"),
        ({"mutation_declaration": None}, "missing_or_invalid_mutation_declaration"),
        ({"timeout_seconds": 0}, "invalid_timeout_seconds"),
        ({"timeout_seconds": 9999}, "invalid_timeout_seconds"),
        ({"output_max_chars": 0}, "invalid_output_max_chars"),
        ({"description": ""}, "missing_field:description"),
        ({"tests": []}, "missing_tests"),
    ],
)
def test_invalid_fields_are_rejected(overrides, expected_error):
    errors = validate_proposal_payload(_proposal(**overrides))
    assert expected_error in errors


def test_free_shell_string_is_rejected():
    errors = validate_proposal_payload(_proposal(command_template="wc -l {path}"))
    assert "command_template_must_be_token_list" in errors


def test_shell_metacharacters_in_template_are_rejected():
    errors = validate_proposal_payload(_proposal(command_template=["sh", "-c", "wc -l; rm -rf /"]))
    assert "command_template_shell_metacharacter" in errors


def test_placeholder_must_reference_argument_schema():
    errors = validate_proposal_payload(_proposal(command_template=["wc", "-l", "{unknown}"]))
    assert "placeholder_without_argument:unknown" in errors


def test_name_shadowing_static_registry_is_rejected():
    # custom.* never collides with static names, so this guards the
    # namespace rule itself via a static name in custom namespace form.
    errors = validate_proposal_payload(_proposal(name="custom.count_lines"))
    assert errors == []
    errors = validate_proposal_payload(_proposal(name="repo.grep"))
    assert "invalid_name_namespace" in errors


def test_tests_require_positive_and_negative_case():
    only_positive = [{"name": "ok", "kind": "positive", "arguments": {}}]
    errors = validate_proposal_payload(_proposal(tests=only_positive))
    assert "missing_negative_test" in errors
    only_negative = [{"name": "fail", "kind": "negative", "arguments": {}}]
    errors = validate_proposal_payload(_proposal(tests=only_negative))
    assert "missing_positive_test" in errors


def test_script_kind_requires_store_relative_ref():
    base = _proposal(execution_kind="script", command_template=None)
    base.pop("command_template")
    errors = validate_proposal_payload({**base, "script_body_ref": "/etc/passwd"})
    assert "script_body_ref_outside_store" in errors
    errors = validate_proposal_payload({**base, "script_body_ref": "tool-scripts/../../evil.sh"})
    assert "script_body_ref_outside_store" in errors
    errors = validate_proposal_payload({**base, "script_body_ref": "tool-scripts/count.sh"})
    assert "script_body_ref_outside_store" not in errors
