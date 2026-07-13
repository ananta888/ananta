from __future__ import annotations

import ast
from pathlib import Path

from agent.services.native_graph_control_bridge import (
    NativeGraphWorkflowControlBridge,
)
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand
from worker.runtime.native_graph.contracts import NativeNodeCommand as WorkerNativeNodeCommand
from worker.runtime.native_graph.execution_adapter import NativeExecutionRuntimeAdapter


def test_worker_reexports_the_hub_owned_wire_contract() -> None:
    assert WorkerNativeNodeCommand is NativeNodeCommand


def test_native_graph_hub_services_never_import_worker_packages() -> None:
    root = Path(__file__).resolve().parents[1]
    hub_paths = (
        root / "agent/services/native_graph_orchestration_service.py",
        root / "agent/services/native_graph_task_queue_adapter.py",
        root / "agent/services/workflow_runtime/native_graph_contracts.py",
        root / "agent/services/workflow_runtime/native_graph_ports.py",
    )

    for path in hub_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
        assert not any(
            module == "worker" or module.startswith("worker.")
            for module in imported_modules
        ), f"Hub service imports worker implementation: {path}"


def test_native_checkpoint_resume_and_stream_authority_stays_in_hub_bridge() -> None:
    assert callable(NativeGraphWorkflowControlBridge.history)
    assert callable(NativeGraphWorkflowControlBridge.signal)
    assert callable(NativeGraphWorkflowControlBridge.cancel)
    assert not hasattr(NativeExecutionRuntimeAdapter, "checkpoint")
    assert not hasattr(NativeExecutionRuntimeAdapter, "resume")
