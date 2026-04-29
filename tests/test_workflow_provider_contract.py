from __future__ import annotations

from pathlib import Path

from agent.providers.interfaces import ProviderDescriptor, ProviderHealthReport
from agent.providers.workflow import WorkflowExecutionRequest, WorkflowExecutionResult

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "agent" / "providers" / "workflow.py"


class _MockWorkflowProvider:
    descriptor = ProviderDescriptor(
        provider_id="mock_workflow",
        provider_family="workflow",
        capabilities=("dry_run", "execute"),
        risk_class="medium",
        enabled_by_default=False,
    )

    def health(self) -> ProviderHealthReport:
        return ProviderHealthReport(status="healthy")

    def execute(self, request: WorkflowExecutionRequest) -> WorkflowExecutionResult:
        return WorkflowExecutionResult(
            status="completed" if not request.dry_run else "degraded",
            output_payload={"workflow_id": request.workflow_id, "dry_run": request.dry_run},
            callback_correlation_id=request.callback_correlation_id,
            provider_run_ref="run-mock-1",
            metadata={"provider_id": self.descriptor.provider_id},
        )


def test_workflow_provider_contract_supports_dry_run_and_callback_correlation() -> None:
    provider = _MockWorkflowProvider()
    request = WorkflowExecutionRequest(
        workflow_id="wf-notify",
        input_payload={"task_id": "T-1"},
        dry_run=True,
        callback_correlation_id="corr-1",
        trace_id="trace-1",
    )
    result = provider.execute(request)
    assert result.status == "degraded"
    assert result.callback_correlation_id == "corr-1"
    assert result.output_payload["dry_run"] is True


def test_workflow_interface_module_has_no_n8n_specific_imports() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("n8n", "node-red", "activepieces"):
        assert forbidden not in source
