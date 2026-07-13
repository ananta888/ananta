from __future__ import annotations

import ast
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from scripts.validate_workflow_runtime_docs import (
    _load_json,
    _sensitive_fixture_paths,
    _validate_security_gate,
    validate_repository,
)

ROOT = Path(__file__).resolve().parents[3]


def test_workflow_runtime_documentation_and_security_gate_are_complete() -> None:
    assert validate_repository(ROOT) == []


def test_open_critical_finding_blocks_production_policy() -> None:
    gate = copy.deepcopy(_load_json(ROOT, "docs/security/workflow-runtime-security-gates.v1.json"))
    gate["threats"][0]["status"] = "open"

    errors = _validate_security_gate(ROOT, gate)

    assert any("open_critical_finding_blocks_production" in error for error in errors)


def test_runtime_neutral_example_plan_uses_the_same_valid_contract() -> None:
    manifest = _load_json(ROOT, "examples/workflow-runtime/example-manifest.v1.json")
    plan_ref = manifest["execution_plan_ref"]
    assert {runtime["execution_plan_ref"] for runtime in manifest["runtimes"].values()} == {plan_ref}

    raw_plan = _load_json(ROOT, plan_ref)
    plan = ExecutionPlan.from_mapping(raw_plan)
    plan.assert_valid()

    assert plan.to_dict() == raw_plan
    assert manifest["runtimes"]["temporal"]["durable"] is True
    assert manifest["langchain"]["control_plane"] is False
    assert manifest["langchain"]["task_queue_owner"] is False


def test_documentation_fixtures_have_no_secret_keys_or_volatile_identifiers() -> None:
    manifest = _load_json(ROOT, "examples/workflow-runtime/example-manifest.v1.json")
    fixture_refs = (
        manifest["execution_plan_ref"],
        manifest["fake_provider_ref"],
        manifest["rollout_policy_ref"],
    )
    uuid_pattern = re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    )
    for reference in fixture_refs:
        fixture = _load_json(ROOT, reference)
        assert list(_sensitive_fixture_paths(fixture)) == []
        rendered = json.dumps(fixture, sort_keys=True)
        assert uuid_pattern.search(rendered) is None
        assert "created_at" not in rendered
        assert "updated_at" not in rendered
        assert "timestamp" not in rendered


def test_hub_core_contracts_do_not_import_optional_runtime_frameworks() -> None:
    contract_files = (
        ROOT / "agent/services/workflow_runtime/execution_plan.py",
        ROOT / "agent/services/workflow_runtime/ports.py",
        ROOT / "agent/services/workflow_control_service.py",
    )
    forbidden = ("temporalio", "langchain", "langgraph")
    for path in contract_files:
        text = path.read_text(encoding="utf-8").lower()
        for framework in forbidden:
            assert f"import {framework}" not in text
            assert f"from {framework}" not in text


def test_temporal_worker_has_no_direct_hub_package_import() -> None:
    for path in sorted((ROOT / "worker/temporal").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"^(?:from|import)\s+agent(?:\.|\s|$)", text, re.MULTILINE) is None


def test_worker_runtimes_do_not_import_hub_control_or_persistence_implementations() -> None:
    forbidden_modules = (
        "agent.routes",
        "agent.database",
        "agent.repository",
        "agent.db_models",
        "agent.services.ananta_tool_registry_service",
        "agent.services.approval_request_service",
        "agent.services.structured_output_service",
        "agent.llm_integration",
        "agent.services.native_worker_runtime_service",
        "agent.services.provider_invocation_middleware",
        "agent.services.task_runtime_service",
        "agent.services.worker_runtime_execution_adapter",
        "agent.services.worker_workspace_service",
        "agent.services.workflow_control_service",
        "agent.services.workflow_runtime_selection_service",
        "agent.services.native_graph_orchestration_service",
        "agent.services.task_queue_service",
    )
    worker_files = [
        *sorted((ROOT / "worker/temporal").rglob("*.py")),
        *sorted((ROOT / "worker/runtime").rglob("*.py")),
        *sorted((ROOT / "worker/adapters").rglob("*.py")),
    ]
    for path in worker_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        for module in imported:
            assert not any(
                module == forbidden
                or module.startswith(f"{forbidden}.")
                for forbidden in forbidden_modules
            ), f"Worker imports Hub implementation {module}: {path}"


def test_hub_native_compatibility_paths_cannot_execute_worker_code() -> None:
    facade = ROOT / "agent/services/native_worker_runtime_service.py"
    execute_path = ROOT / "agent/services/_task_scoped_execute_workspace.py"

    facade_tree = ast.parse(facade.read_text(encoding="utf-8"), filename=str(facade))
    facade_imports: list[str] = []
    for node in ast.walk(facade_tree):
        if isinstance(node, ast.ImportFrom):
            facade_imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            facade_imports.extend(alias.name for alias in node.names)
    assert not any(
        module == "worker" or module.startswith("worker.")
        for module in facade_imports
    )
    assert "subprocess" not in facade_imports
    assert "sgpt_fallback_proxy" not in facade.read_text(encoding="utf-8")

    execute_source = execute_path.read_text(encoding="utf-8")
    assert "get_native_worker_runtime_service" not in execute_source
    assert ".execute_and_verify_command(" not in execute_source
    assert "native_worker_in_process_execution_disabled" in execute_source


def test_remaining_worker_agent_imports_are_explicit_contract_facades() -> None:
    """Keep temporary namespace debt visible without allowing Hub services broadly."""

    allowed_facades = frozenset(
        {
            "agent.providers.lc_lg",
            "agent.services.workflow_runtime.components",
            "agent.services.workflow_runtime.condition_evaluator",
            "agent.services.workflow_runtime.execution_plan",
            "agent.services.workflow_runtime.native_graph_contracts",
            "agent.services.workflow_runtime.native_graph_ports",
            "agent.services.workflow_runtime.parallel",
            "agent.services.workflow_runtime.ports",
            "agent.services.workflow_runtime.security",
        }
    )
    worker_files = [
        *sorted((ROOT / "worker/temporal").rglob("*.py")),
        *sorted((ROOT / "worker/runtime").rglob("*.py")),
        *sorted((ROOT / "worker/adapters").rglob("*.py")),
    ]
    for path in worker_files:
        if path.name.startswith("restricted_inference_"):
            # The restricted-inference boundary has its own AIR architecture
            # program and is deliberately not widened by this workflow task.
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        for module in imported:
            if module != "agent" and not module.startswith("agent."):
                continue
            assert module in allowed_facades, (
                f"Unreviewed Worker-to-agent dependency {module}: {path}"
            )


def test_flask_worker_composition_is_role_guarded_before_worker_imports() -> None:
    source = (ROOT / "agent/ai_agent.py").read_text(encoding="utf-8")
    function_start = source.index("def _initialize_workflow_adapter_worker_runtime")
    role_guard = source.index('if settings.role != "worker":', function_start)
    worker_runtime_import = source.index(
        "from worker.runtime.native_worker_runtime_service import",
        function_start,
    )
    assert role_guard < worker_runtime_import


def test_worker_composition_imports_without_hub_database_packages() -> None:
    script = r'''
import importlib.abc
import sys

FORBIDDEN = (
    "agent.database",
    "agent.db_models",
    "agent.repository",
    "agent.services.ananta_tool_registry_service",
    "agent.services.approval_request_service",
    "agent.services.native_worker_runtime_service",
    "agent.services.provider_invocation_middleware",
    "agent.services.task_runtime_service",
    "agent.services.worker_runtime_execution_adapter",
    "agent.services.worker_workspace_service",
)

class BlockHubImplementations(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(
            fullname == prefix or fullname.startswith(prefix + ".")
            for prefix in FORBIDDEN
        ):
            raise ImportError("blocked Hub implementation: " + fullname)
        return None

sys.meta_path.insert(0, BlockHubImplementations())
import worker.runtime.workflow_tool_pipeline_composition
import worker.runtime.native_graph.composition
print("worker-composition-import-ok")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "worker-composition-import-ok"
