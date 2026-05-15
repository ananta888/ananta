import unittest
from unittest.mock import MagicMock
import sys
import os

# Ensure project root is in path
sys.path.append(os.getcwd())

from worker.core.context_access_policy import (
    ContextAccessPolicy, ContextAccessRule, SourceType, Sensitivity, Decision, ModelScope, RequestedOperation
)
from worker.core.execution_envelope import ExecutionEnvelope, ModelPolicy, ToolPolicy
from worker.core.preflight import PreflightGate, PreflightDecision
from agent.services.result_memory_service import ResultMemoryService

class TestContextAccessPolicyRegression(unittest.TestCase):
    def setUp(self):
        self.gate = PreflightGate()
        # Mocking the repository to avoid DB calls in ResultMemoryService
        self.memory_repo_mock = MagicMock()
        # We need to mock the import in result_memory_service
        import agent.services.result_memory_service as rms
        rms.memory_entry_repo = self.memory_repo_mock

        self.memory_service = ResultMemoryService()

        # Policy: Deny secrets to everything
        self.policy = ContextAccessPolicy(
            policy_id="regression_test",
            version=1,
            scope="project",
            rules=[
                ContextAccessRule(
                    id="rule_no_secrets",
                    description="No secrets allowed",
                    sensitivity=Sensitivity.secret,
                    send_allowed=False,
                    write_allowed=False,
                    cloud_allowed=False,
                    external_worker_allowed=False
                )
            ]
        )

    def test_tool_access_denied_for_secret(self):
        # CAP-BE-T024 integration in PreflightGate
        policy_dict = {
            "policy_id": self.policy.policy_id,
            "version": self.policy.version,
            "scope": self.policy.scope,
            "rules": [
                {
                    "id": r.id,
                    "description": r.description,
                    "sensitivity": r.sensitivity.value if hasattr(r.sensitivity, 'value') else r.sensitivity,
                    "send_allowed": r.send_allowed,
                    "write_allowed": r.write_allowed,
                    "cloud_allowed": r.cloud_allowed,
                    "external_worker_allowed": r.external_worker_allowed
                } for r in self.policy.rules
            ]
        }

        envelope = ExecutionEnvelope(
            task_id="task_1",
            actor_ref="actor_1",
            audit_correlation_id="audit_1",
            capability_grant={"capabilities": ["planning"]},
            context_envelope_ref="ctx:task_1",
            model_policy=ModelPolicy(),
            tool_policy=ToolPolicy(allowed_tool_ids=["read_file"]),
            context_access_policy=policy_dict
        )

        secret_block = {
            "block_id": "b1",
            "source_type": SourceType.secret_file.value,
            "source_ref": "secrets.env",
            "sensitivity": Sensitivity.secret.value
        }

        # Should be blocked
        result = self.gate.check_tool(envelope, "read_file", context_block=secret_block)
        self.assertEqual(result.decision, PreflightDecision.blocked)
        self.assertIn("denied by CAP", result.detail)

    def test_memory_write_denied_for_secret(self):
        # CAP-BE-T025 integration in ResultMemoryService
        result = self.memory_service.record_worker_result_memory(
            task_id="t1",
            goal_id="g1",
            trace_id="tr1",
            worker_job_id="w1",
            title="Secret Memory",
            output="password=123",
            policy={"sensitivity": "secret"},
            context_access_policy=self.policy
        )
        self.assertIsNone(result)
        self.memory_repo_mock.create.assert_not_called()

    def test_memory_write_allowed_for_internal(self):
        # If result is not None, it passed the CAP check
        # We need to mock more to get past the full record_worker_result_memory logic
        self.memory_repo_mock.create.return_value = MagicMock()

        allow_internal_policy = ContextAccessPolicy(
            policy_id="regression_test_allow_internal",
            version=1,
            scope="project",
            rules=[
                ContextAccessRule(
                    id="rule_allow_internal_memory",
                    description="Allow project_internal memory writes locally",
                    sensitivity=Sensitivity.project_internal,
                    write_allowed=True,
                    allowed_model_scopes=[ModelScope.local_tool_only, ModelScope.local_model, ModelScope.none],
                )
            ],
            defaults={"read_allowed": True, "write_allowed": False, "send_allowed": False},
        )

        result = self.memory_service.record_worker_result_memory(
            task_id="t1",
            goal_id="g1",
            trace_id="tr1",
            worker_job_id="w1",
            title="Safe Memory",
            output="some info",
            policy={"sensitivity": "project_internal", "enabled": True},
            context_access_policy=allow_internal_policy,
            approved=True
        )
        # It should pass CAP and reach the mock
        self.assertIsNotNone(result)

if __name__ == "__main__":
    unittest.main()
