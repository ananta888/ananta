"""HDE-016: sandbox/allowlist executor boundaries."""
import pytest

from agent.services.custom_tool_executor import CustomToolExecutor


def _spec(**overrides):
    spec = {
        "name": "custom.count_lines",
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
        "timeout_seconds": 5,
        "output_max_chars": 2000,
    }
    spec.update(overrides)
    return spec


def _run(tmp_path, spec=None, arguments=None, config=None):
    return CustomToolExecutor(tmp_path / "data").execute_spec(
        spec=spec or _spec(),
        arguments=arguments or {},
        workspace_dir=str(tmp_path / "ws"),
        tool_call_id="t-1",
        config=config or {},
    )


@pytest.fixture(autouse=True)
def workspace(tmp_path):
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "a.txt").write_text("eins\nzwei\n", encoding="utf-8")
    return tmp_path / "ws"


def test_happy_path_counts_lines(tmp_path):
    result = _run(tmp_path, arguments={"path": "a.txt"})
    assert result["status"] == "ok"
    assert result["data"]["exit_code"] == 0
    assert "2" in result["evidence"][0]["excerpt"]


def test_command_injection_via_argument_is_blocked(tmp_path):
    result = _run(tmp_path, arguments={"path": "a.txt; rm -rf /"})
    assert result["status"] == "rejected"
    assert "argument_contains_shell_metacharacter" in result["error"]


def test_path_escape_is_blocked(tmp_path):
    (tmp_path / "outside.txt").write_text("geheim", encoding="utf-8")
    result = _run(tmp_path, arguments={"path": "../outside.txt"})
    assert result["status"] == "rejected"
    assert "path_argument_outside_workspace" in result["error"]


def test_denied_path_glob_is_blocked(tmp_path):
    spec = _spec(denied_paths=["a.txt"])
    result = _run(tmp_path, spec=spec, arguments={"path": "a.txt"})
    assert result["status"] == "rejected"
    assert "path_argument_not_allowed" in result["error"]


def test_unknown_argument_is_rejected(tmp_path):
    result = _run(tmp_path, arguments={"path": "a.txt", "extra": "x"})
    assert result["status"] == "rejected"
    assert "unknown_argument:extra" in result["error"]


def test_output_is_truncated_to_limit(tmp_path):
    spec = _spec(
        command_template=["seq", "1", "5000"],
        argument_schema={"type": "object", "properties": {}},
        path_arguments=[],
        output_max_chars=200,
    )
    result = _run(tmp_path, spec=spec)
    assert result["status"] == "ok"
    assert len(result["evidence"][0]["excerpt"]) <= 220
    assert "[truncated]" in result["evidence"][0]["excerpt"]


def test_timeout_is_enforced(tmp_path):
    spec = _spec(
        command_template=["sleep", "5"],
        argument_schema={"type": "object", "properties": {}},
        path_arguments=[],
        timeout_seconds=1,
    )
    result = _run(tmp_path, spec=spec)
    assert result["status"] == "rejected"
    assert "timeout" in result["error"]


def test_env_is_restricted_to_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("ANANTA_SECRET_VAR", "geheim")
    spec = _spec(
        command_template=["printenv", "ANANTA_SECRET_VAR"],
        argument_schema={"type": "object", "properties": {}},
        path_arguments=[],
    )
    result = _run(tmp_path, spec=spec)
    assert result["status"] == "error", "non-allowlisted env var must be invisible"

    spec_allowed = _spec(
        command_template=["printenv", "ANANTA_SECRET_VAR"],
        argument_schema={"type": "object", "properties": {}},
        path_arguments=[],
        env_allowlist=["ANANTA_SECRET_VAR"],
    )
    result = _run(tmp_path, spec=spec_allowed)
    assert result["status"] == "ok"
    assert "geheim" in result["evidence"][0]["excerpt"]


def test_script_outside_store_is_blocked(tmp_path):
    spec = _spec(
        execution_kind="script",
        script_body_ref="tool-scripts/missing.sh",
        command_template=None,
    )
    result = _run(tmp_path, spec=spec)
    assert result["status"] == "rejected"
    assert "script_body_ref_not_in_approved_store" in result["error"]


def test_script_from_approved_store_runs(tmp_path):
    store = tmp_path / "data" / "tool-scripts"
    store.mkdir(parents=True)
    script = store / "hello.sh"
    script.write_text("#!/bin/bash\necho hallo-aus-script\n", encoding="utf-8")
    import hashlib

    spec = _spec(
        execution_kind="script",
        script_body_ref="tool-scripts/hello.sh",
        script_body_digest=hashlib.sha256(script.read_bytes()).hexdigest(),
        command_template=None,
        argument_schema={"type": "object", "properties": {}},
        path_arguments=[],
    )
    result = _run(tmp_path, spec=spec)
    assert result["status"] == "ok"
    assert "hallo-aus-script" in result["evidence"][0]["excerpt"]


def test_script_digest_mismatch_is_blocked(tmp_path):
    store = tmp_path / "data" / "tool-scripts"
    store.mkdir(parents=True)
    script = store / "hello.sh"
    script.write_text("#!/bin/bash\necho first\n", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    script.write_text("#!/bin/bash\necho tampered\n", encoding="utf-8")
    spec = _spec(
        execution_kind="script",
        script_body_ref="tool-scripts/hello.sh",
        script_body_digest=digest,
        command_template=None,
        argument_schema={"type": "object", "properties": {}},
        path_arguments=[],
    )
    result = _run(tmp_path, spec=spec)
    assert result["status"] == "rejected"
    assert result["error"] == "script_body_digest_mismatch"
