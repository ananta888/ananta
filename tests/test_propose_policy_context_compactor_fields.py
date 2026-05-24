from agent.services.propose_policy import ProposePolicy, build_policy_from_dict
from agent.services.propose_policy_service import ProposePolicyService


def test_policy_defaults_include_compactor_fields():
    p = ProposePolicy()
    d = p.to_dict()
    assert d["context_compaction_enabled"] is True
    assert d["context_compaction_required"] is False
    assert d["context_compactor_fail_open"] is False


def test_policy_clamps_compactor_ranges():
    p = build_policy_from_dict(
        {
            "context_compactor_timeout_seconds": 999,
            "context_compactor_max_output_chars": 999999,
            "context_compactor_retry_attempts": 99,
        }
    )
    assert 30 <= p.context_compactor_timeout_seconds <= 120
    assert 1000 <= p.context_compactor_max_output_chars <= 50000
    assert 0 <= p.context_compactor_retry_attempts <= 3


def test_runtime_profile_resolution_for_compactor():
    svc = ProposePolicyService()
    p = svc.get_effective_policy(project_config={"runtime_profile": "lmstudio_laptop"})
    assert p.context_compactor_profile in {"default", "lmstudio_laptop", "ollama_rtx3080"}
