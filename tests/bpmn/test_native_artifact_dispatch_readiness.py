"""The Hub must reject unavailable declared inputs before Worker dispatch."""

from dataclasses import replace

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from tests.test_native_graph_runtime import plan, runtime


def test_missing_declared_input_fails_without_queue_or_worker_execution():
    compiled = plan()
    first = compiled.nodes[0]
    compiled = replace(compiled, nodes=tuple(
        replace(node, input_artifacts=("report",)) if node == first else node for node in compiled.nodes
    ))
    hub, queue, handler, *_ = runtime()
    request = NativeGraphRequest(compiled, "synthetic-missing-input", "synthetic-control")
    result = hub.start(request)
    for _ in range(8):
        if result.status in {"completed", "failed", "cancelled"}:
            break
        result = hub.advance(request)
    assert result.status == "failed", result
    assert result.reason_code == "native_input_artifact_missing:report"
    assert queue.submissions == []
    assert handler.calls == []
