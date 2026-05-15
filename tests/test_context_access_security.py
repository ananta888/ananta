import pytest
from worker.core.context_access_policy import (
    ContextAccessPolicy, ContextAccessRule, Sensitivity, 
    ModelScope, SourceType, DestinationContext, RequestedOperation,
    Decision, ContextAccessPolicyEvaluator, ReasonCode
)

def test_security_policy_denies_secret_to_public_cloud():
    # Setup policy
    rule = ContextAccessRule(
        id="R1",
        description="Deny secrets to public cloud",
        sensitivity=Sensitivity.secret,
        allowed_model_scopes=[ModelScope.local_model, ModelScope.private_remote],
        cloud_allowed=False
    )
    policy = ContextAccessPolicy(policy_id="P1", version=1, scope="test", rules=[rule])
    evaluator = ContextAccessPolicyEvaluator(policy)
    
    # Setup destination (Public Cloud)
    dest = DestinationContext(
        worker_id="w1", worker_kind="native", runtime_target_id="cloud",
        runtime_kind="remote", provider_id="openai", provider_location="external",
        model_id="gpt-4", model_scope=ModelScope.public_cloud,
        cloud_effective=True, external_effective=True, local_effective=False,
        requested_operation=RequestedOperation.send_to_llm
    )
    
    # Setup block (Secret)
    block = {
        "source_type": SourceType.codecompass_code,
        "source_ref": "secrets.json",
        "sensitivity": Sensitivity.secret,
        "content_hash": "h1"
    }
    
    decision = evaluator.get_decision(block, dest)
    assert decision.decision == Decision.deny
    assert decision.reason_code in {ReasonCode.model_scope_not_allowed, ReasonCode.cloud_blocked}

def test_security_policy_allows_public_to_anywhere():
    rule = ContextAccessRule(
        id="R1",
        description="Allow public to anywhere",
        sensitivity=Sensitivity.public,
        cloud_allowed=True,
        send_allowed=True
    )
    policy = ContextAccessPolicy(policy_id="P1", version=1, scope="test", rules=[rule])
    evaluator = ContextAccessPolicyEvaluator(policy)
    
    dest = DestinationContext(
        worker_id="w1", worker_kind="native", runtime_target_id="cloud",
        runtime_kind="remote", provider_id="openai", provider_location="external",
        model_id="gpt-4", model_scope=ModelScope.public_cloud,
        cloud_effective=True, external_effective=True, local_effective=False,
        requested_operation=RequestedOperation.send_to_llm
    )
    
    block = {
        "source_type": SourceType.codecompass_code,
        "source_ref": "README.md",
        "sensitivity": Sensitivity.public,
        "content_hash": "h1"
    }
    
    decision = evaluator.get_decision(block, dest)
    assert decision.decision == Decision.allow

def test_security_policy_redaction():
    rule = ContextAccessRule(
        id="R1",
        description="Redact project internal",
        sensitivity=Sensitivity.project_internal,
        redaction_required=True,
        cloud_allowed=True,
        send_allowed=True
    )
    policy = ContextAccessPolicy(policy_id="P1", version=1, scope="test", rules=[rule])
    evaluator = ContextAccessPolicyEvaluator(policy)
    
    dest = DestinationContext(
        worker_id="w1", worker_kind="native", runtime_target_id="cloud",
        runtime_kind="remote", provider_id="openai", provider_location="external",
        model_id="gpt-4", model_scope=ModelScope.public_cloud,
        cloud_effective=True, external_effective=True, local_effective=False,
        requested_operation=RequestedOperation.send_to_llm
    )
    
    block = {
        "source_type": SourceType.codecompass_code,
        "source_ref": "src/main.py",
        "sensitivity": Sensitivity.project_internal,
        "content_hash": "h1"
    }
    
    decision = evaluator.get_decision(block, dest)
    assert decision.decision == Decision.allow_redacted
