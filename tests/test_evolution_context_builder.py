from types import SimpleNamespace

import pytest

from agent.services.evolution import EvolutionContextBuilder, EvolutionContextBuildOptions
from agent.services.evolution_service import EvolutionService


class Repo:
    def __init__(self, **items):
        self._items = items

    def get_by_id(self, item_id):
        return self._items.get(item_id)


class VerificationRepo:
    def get_by_task_id(self, task_id):
        return [
            SimpleNamespace(
                id="ver-1",
                status="failed",
                verification_type="quality_gate",
                retry_count=1,
                repair_attempts=1,
                escalation_reason=None,
                results={"quality_gates_reason": "missing_evidence"},
            )
        ]


class AuditRepo:
    def get_all(self, limit=100, offset=0):
        return [
            SimpleNamespace(
                id=1,
                action="task_verification_updated",
                timestamp=123.0,
                task_id="T-1",
                goal_id="G-1",
                trace_id="trace-1",
                details={"task_id": "T-1", "token": "***REDACTED***"},
            ),
            SimpleNamespace(
                id=2,
                action="unrelated",
                timestamp=122.0,
                task_id="T-OTHER",
                details={"task_id": "T-OTHER"},
            ),
        ]


class FakeRepos:
    def __init__(self):
        self.task_repo = Repo(
            **{
                "T-1": SimpleNamespace(
                    id="T-1",
                    title="Improve task queue",
                    description="Make queue state clearer",
                    status="blocked",
                    priority="High",
                    task_kind="coding",
                    goal_id="G-1",
                    goal_trace_id="trace-1",
                    plan_id="P-1",
                    context_bundle_id="bundle-1",
                    required_capabilities=["coding"],
                    verification_spec={"required": True},
                    verification_status={"status": "failed"},
                    last_exit_code=1,
                    last_output="failure",
                    last_proposal={"artifacts": [{"artifact_id": "art-1"}], "review": {"required": True}},
                    worker_execution_context={},
                    history=[{"event": "task_output", "artifact_id": "art-2"}],
                    updated_at=456.0,
                )
            }
        )
        self.context_bundle_repo = Repo(
            **{
                "bundle-1": SimpleNamespace(
                    id="bundle-1",
                    bundle_type="worker_execution_context",
                    chunks=[{"metadata": {"artifact_id": "art-3"}}],
                    token_estimate=42,
                    bundle_metadata={"artifact_ids": ["art-4"], "policy": "compact"},
                )
            }
        )
        self.artifact_repo = Repo(
            **{
                "art-1": SimpleNamespace(
                    id="art-1",
                    status="stored",
                    latest_media_type="text/plain",
                    latest_filename="proposal.txt",
                    size_bytes=120,
                    created_by="worker",
                ),
                "art-2": SimpleNamespace(
                    id="art-2",
                    status="stored",
                    latest_media_type="text/markdown",
                    latest_filename="result.md",
                    size_bytes=220,
                    created_by="worker",
                ),
                "art-3": SimpleNamespace(
                    id="art-3",
                    status="stored",
                    latest_media_type="application/json",
                    latest_filename="context.json",
                    size_bytes=320,
                    created_by="hub",
                ),
            }
        )
        self.verification_record_repo = VerificationRepo()
        self.audit_repo = AuditRepo()


def test_evolution_context_builder_collects_task_verification_audit_and_artifact_signals():
    context = EvolutionContextBuilder(repositories=FakeRepos()).build_for_task("T-1")

    assert context.objective == "Improve task queue"
    assert context.task_id == "T-1"
    assert context.goal_id == "G-1"
    assert context.trace_id == "trace-1"
    assert context.plan_id == "P-1"
    assert context.signals["task"]["status"] == "blocked"
    assert context.signals["verification"]["latest_status"] == "failed"
    assert context.signals["audit"]["event_count"] == 1
    assert context.signals["context_bundle"]["context_bundle_id"] == "bundle-1"
    assert {item["artifact_id"] for item in context.signals["artifacts"]} == {"art-1", "art-2", "art-3", "art-4"}
    assert context.constraints["required_capabilities"] == ["coding"]
    assert context.constraints["review_required"] is True


def test_context_builder_can_override_objective_and_limit_audit_details():
    options = EvolutionContextBuildOptions(include_audit_details=False, audit_limit=1)
    context = EvolutionContextBuilder(repositories=FakeRepos()).build_for_task(
        "T-1", objective="Custom improvement objective", options=options
    )

    assert context.objective == "Custom improvement objective"
    assert "details" not in context.signals["audit"]["events"][0]


def test_context_builder_raises_clear_error_for_missing_task():
    with pytest.raises(KeyError, match="task_not_found"):
        EvolutionContextBuilder(repositories=FakeRepos()).build_for_task("missing")


def test_evolution_service_exposes_task_context_builder():
    service = EvolutionService(context_builder=EvolutionContextBuilder(repositories=FakeRepos()))

    context = service.build_context_for_task("T-1")

    assert context.task_id == "T-1"
    assert any(item["kind"] == "context_bundle" for item in context.source_refs)
