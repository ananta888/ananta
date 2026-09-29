import unittest
from worker.core.context_access_policy import (
    Sensitivity,
    ModelScope,
    SourceType,
    Decision,
    ReasonCode,
    RequestedOperation,
    DestinationContext,
    ContextBlockAccessDecision,
    ContextAccessRule,
    ContextAccessPolicy
)
from agent.services.context_access_policy_service import ContextAccessPolicyService

class TestContextAccessPolicy(unittest.TestCase):
    def test_minimal_policy(self):
        policy = ContextAccessPolicy(
            policy_id="p1",
            version=1,
            scope="project",
            rules=[
                ContextAccessRule(
                    id="r1",
                    description="allow public code to any local model",
                    source_types=[SourceType.codecompass_code],
                    sensitivity=Sensitivity.public,
                    allowed_model_scopes=[ModelScope.local_model],
                    read_allowed=True,
                    send_allowed=True
                )
            ]
        )
        service = ContextAccessPolicyService()
        errors = service.validate_policy(policy)
        self.assertEqual(len(errors), 0)

    def test_invalid_secret_to_cloud_policy(self):
        policy = ContextAccessPolicy(
            policy_id="p2",
            version=1,
            scope="project",
            rules=[
                ContextAccessRule(
                    id="r2",
                    description="unsafe secret to cloud",
                    source_types=[SourceType.secret_file],
                    sensitivity=Sensitivity.secret,
                    allowed_model_scopes=[ModelScope.public_cloud],
                    send_allowed=True,
                    cloud_allowed=True
                )
            ]
        )
        service = ContextAccessPolicyService()
        errors = service.validate_policy(policy)
        self.assertGreater(len(errors), 0)
        self.assertIn("cannot be allowed for cloud without approval", errors[0])

    def test_invalid_unrestricted_write_policy(self):
        policy = ContextAccessPolicy(
            policy_id="p3",
            version=1,
            scope="project",
            rules=[
                ContextAccessRule(
                    id="r3",
                    description="unrestricted write",
                    write_allowed=True
                )
            ]
        )
        service = ContextAccessPolicyService()
        errors = service.validate_policy(policy)
        self.assertGreater(len(errors), 0)
        self.assertIn("requires at least one worker/runtime/model constraint", errors[0])

    def test_decision_serialization(self):
        decision = ContextBlockAccessDecision(
            block_id="b1",
            source_ref="file.txt",
            matched_rule_ids=["r1"],
            decision=Decision.allow,
            reason_code=ReasonCode.approval_required,
            policy_version=1
        )
        # Just check if it can be represented
        self.assertEqual(decision.block_id, "b1")
        self.assertEqual(decision.decision, Decision.allow)

    def test_destination_context(self):
        dest = DestinationContext(
            worker_id="w1",
            worker_kind="native",
            runtime_target_id="rt1",
            runtime_kind="local",
            provider_id="p1",
            provider_location="local",
            model_id="m1",
            model_scope=ModelScope.local_model,
            cloud_effective=False,
            external_effective=False,
            local_effective=True,
            requested_operation=RequestedOperation.send_to_llm
        )
        self.assertTrue(dest.local_effective)
        self.assertEqual(dest.model_scope, ModelScope.local_model)

    def test_policy_merge(self):
        p1 = ContextAccessPolicy(policy_id="sys", version=1, scope="system_default", precedence=0)
        p2 = ContextAccessPolicy(policy_id="proj", version=1, scope="project", precedence=10, rules=[
            ContextAccessRule(id="r_proj", description="proj rule")
        ])

        service = ContextAccessPolicyService()
        merged = service.merge_policies([p1, p2])

        self.assertEqual(merged.scope, "merged")
        self.assertEqual(len(merged.rules), 1)
        self.assertEqual(merged.rules[0].id, "r_proj")

    def test_source_matching(self):
        service = ContextAccessPolicyService()
        rule = ContextAccessRule(
            id="r1",
            description="match python files",
            source_match="*.py",
            source_types=[SourceType.local_file]
        )

        # Positive match
        self.assertTrue(service.match_source(rule, {
            "source_type": SourceType.local_file.value,
            "source_ref": "src/main.py"
        }))

        # Negative match (wrong extension)
        self.assertFalse(service.match_source(rule, {
            "source_type": SourceType.local_file.value,
            "source_ref": "README.md"
        }))

        # Negative match (wrong source type)
        self.assertFalse(service.match_source(rule, {
            "source_type": SourceType.memory.value,
            "source_ref": "main.py"
        }))

    def test_secret_detection(self):
        service = ContextAccessPolicyService()

        # Detect in content
        self.assertEqual(
            service.detect_sensitivity("my api_key = 'sk-1234567890abcdef1234567890abcdef'", "config.py"),
            Sensitivity.secret
        )

        # Detect in filename
        self.assertEqual(
            service.detect_sensitivity("random content", ".env"),
            Sensitivity.secret
        )

        # Normal content
        self.assertEqual(
            service.detect_sensitivity("print('hello')", "main.py"),
            Sensitivity.unknown
        )

    def test_decision_engine(self):
        service = ContextAccessPolicyService()
        policy = ContextAccessPolicy(
            policy_id="p1",
            version=1,
            scope="project",
            rules=[
                ContextAccessRule(
                    id="r1",
                    description="deny cloud for confidential",
                    sensitivity=Sensitivity.customer_confidential,
                    cloud_allowed=False,
                    send_allowed=True
                ),
                ContextAccessRule(
                    id="r2",
                    description="allow public everywhere",
                    sensitivity=Sensitivity.public,
                    cloud_allowed=True,
                    send_allowed=True
                )
            ]
        )

        # Test Case 1: Confidential to Cloud (should be denied)
        dest_cloud = DestinationContext(
            worker_id="w1", worker_kind="native", runtime_target_id="rt1", runtime_kind="local",
            provider_id="openai", provider_location="us", model_id="gpt-4",
            model_scope=ModelScope.public_cloud, cloud_effective=True,
            external_effective=True, local_effective=False,
            requested_operation=RequestedOperation.send_to_llm
        )

        block_confidential = {
            "block_id": "b1",
            "source_ref": "contract.pdf",
            "sensitivity": Sensitivity.customer_confidential,
            "content_hash": "abc"
        }

        decision1 = service.get_decision(policy, block_confidential, dest_cloud)
        self.assertEqual(decision1.decision, Decision.deny)
        self.assertEqual(decision1.reason_code, ReasonCode.cloud_blocked)

        # Test Case 2: Public to Cloud (should be allowed)
        block_public = {
            "block_id": "b2",
            "source_ref": "readme.md",
            "sensitivity": Sensitivity.public,
            "content_hash": "def"
        }

        decision2 = service.get_decision(policy, block_public, dest_cloud)
        self.assertEqual(decision2.decision, Decision.allow)
        self.assertIsNotNone(decision2.decision_hash)

    def test_redaction_and_summarization(self):
        service = ContextAccessPolicyService()

        # Test Redaction
        content = "My password is 'topsecret123' and api_key='sk-12345'"
        redacted = service.redact_content(content)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("topsecret123", redacted)

        # Test Summarization
        long_content = "\n".join([f"Line {i}" for i in range(20)])
        summary = service.summarize_content(long_content)
        self.assertTrue(summary.startswith("[SUMMARY"))

        # Test Policy Decision with Redaction
        policy = ContextAccessPolicy(
            policy_id="p1", version=1, scope="project",
            rules=[
                ContextAccessRule(
                    id="r1", description="redact for cloud",
                    source_match="*.log", redaction_required=True,
                    send_allowed=True, cloud_allowed=True
                )
            ]
        )
        dest_cloud = DestinationContext(
            worker_id="w1", worker_kind="native", runtime_target_id="rt1", runtime_kind="local",
            provider_id="openai", provider_location="us", model_id="gpt-4",
            model_scope=ModelScope.public_cloud, cloud_effective=True,
            external_effective=True, local_effective=False,
            requested_operation=RequestedOperation.send_to_llm
        )
        block = {"block_id": "b1", "source_ref": "app.log", "content_hash": "123"}

        decision = service.get_decision(policy, block, dest_cloud)
        self.assertEqual(decision.decision, Decision.allow_redacted)

    def test_audit_and_persistence(self):
        service = ContextAccessPolicyService()
        policy = ContextAccessPolicy(
            policy_id="p-persist", version=1, scope="project",
            rules=[ContextAccessRule(id="r1", description="desc")]
        )

        # Test Persistence
        import os
        path = "test_policy.json"
        try:
            service.save_policy(policy, path)
            loaded = service.load_policy(path)
            self.assertEqual(loaded.policy_id, "p-persist")
            self.assertEqual(len(loaded.rules), 1)
            self.assertEqual(loaded.rules[0].description, "desc")
        finally:
            if os.path.exists(path):
                os.remove(path)

        # Test Audit (should just not crash)
        decision = ContextBlockAccessDecision(
            block_id="b1", source_ref="ref", matched_rule_ids=[],
            decision=Decision.deny, reason_code=ReasonCode.cloud_blocked
        )
        service.audit_decision(decision, "task-123")

    def test_merge_policy_precedence_order(self):
        service = ContextAccessPolicyService()
        p_task = ContextAccessPolicy(
            policy_id="task-pol",
            version=1,
            scope="task",
            precedence=0,
            rules=[ContextAccessRule(id="r-task", description="task")],
        )
        p_sys = ContextAccessPolicy(
            policy_id="sys-pol",
            version=1,
            scope="system_default",
            precedence=999,
            rules=[ContextAccessRule(id="r-sys", description="sys")],
        )
        merged = service.merge_policies([p_task, p_sys])
        self.assertEqual([r.id for r in merged.rules], ["r-sys", "r-task"])

    def test_filter_blocks_uses_default_policy_when_missing(self):
        service = ContextAccessPolicyService()
        dest = DestinationContext(
            worker_id="w1",
            worker_kind="native",
            runtime_target_id="rt1",
            runtime_kind="local",
            provider_id="openai",
            provider_location="public_cloud",
            model_id="gpt-4",
            model_scope=ModelScope.public_cloud,
            cloud_effective=True,
            external_effective=True,
            local_effective=False,
            requested_operation=RequestedOperation.send_to_llm,
        )
        blocks = [{
            "block_id": "b-secret",
            "source_type": SourceType.secret_file.value,
            "source_ref": ".env",
            "sensitivity": Sensitivity.secret,
            "content_hash": "h1",
            "content": "api_key=secret",
        }]
        filtered = service.filter_blocks(None, blocks, dest)
        self.assertEqual(filtered, [])
